"""Things that must not break the bot: floods, replays, foreign ids, groups, crashes."""

from __future__ import annotations

from typing import Any

from napominator.bot import callbacks as cb
from napominator.bot import keyboards
from napominator.domain.entities import ReminderStatus
from tests.bot.harness import Telegram, stored
from tests.support import FakeClock, UowFactory


async def test_a_message_flood_is_throttled_once_not_per_message(
    uow_factory: UowFactory, clock: FakeClock
) -> None:
    tg = Telegram(uow_factory, clock, throttle=True)
    for _ in range(12):
        await tg.send("/help")
    replies = [m.text for m in tg.messages]
    assert replies.count("Слишком часто — подожди пару секунд.") == 1
    assert sum("Как пользоваться" in t for t in replies) == 6  # the first six went through


async def test_the_throttle_window_slides(uow_factory: UowFactory, clock: FakeClock) -> None:
    tg = Telegram(uow_factory, clock, throttle=True)
    for _ in range(8):
        await tg.send("/help")
    clock.advance(seconds=5)
    before = len(tg.messages)
    await tg.send("/help")
    assert "Как пользоваться" in tg.text
    assert len(tg.messages) == before + 1


async def test_a_replayed_update_is_processed_once(user: Telegram, uow_factory: UowFactory) -> None:
    await user.send("завтра в 10:00 звонок")
    await user.replay_last_update()
    await user.replay_last_update()
    assert len(await stored(uow_factory)) == 1


async def test_a_failed_update_is_rolled_back_and_can_be_retried(
    user: Telegram, uow_factory: UowFactory
) -> None:
    class Broken:
        def parse(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("boom")

    good = user.deps.parser
    object.__setattr__(user.deps, "parser", Broken())
    await user.send("завтра в 10:00 звонок")
    assert user.text == "Что-то пошло не так. Попробуй ещё раз чуть позже."
    assert await stored(uow_factory) == []
    object.__setattr__(user.deps, "parser", good)
    await user.replay_last_update()  # the dedup row was rolled back with everything else
    assert "Запланировано" in user.text
    assert len(await stored(uow_factory)) == 1


async def test_a_broken_button_press_answers_the_callback(user: Telegram) -> None:
    await user.send("завтра в 10:00 звонок")
    await user.press("garbage:data")  # not parseable callback data: nobody handles it
    assert user.text  # the bot is still alive and the transcript is intact


async def test_group_chats_are_ignored(user: Telegram) -> None:
    before = len(user.messages)
    await user.send_in_group("завтра в 10:00 звонок")
    assert len(user.messages) == before


async def test_commands_and_non_text_messages_get_a_polite_answer(user: Telegram) -> None:
    await user.send("/нетакой")
    assert "Не знаю такой команды" in user.text
    await user.send_photo()
    assert "только текст" in user.text


async def test_text_before_onboarding_leads_to_the_zone_step(tg: Telegram) -> None:
    await tg.send("завтра в 10:00 звонок")
    assert "Сначала настроим часовой пояс" in tg.text
    assert keyboards.BTN_CITY in tg.buttons()


async def test_menu_buttons_before_onboarding_do_the_same(tg: Telegram) -> None:
    await tg.send(keyboards.BTN_TODAY)
    assert "часовой пояс" in tg.text


async def test_a_foreign_reminder_id_is_just_not_found(
    uow_factory: UowFactory, clock: FakeClock, user: Telegram
) -> None:
    await user.send("завтра в 10:00 звонок")
    (mine,) = await stored(uow_factory)
    intruder = Telegram(uow_factory, clock, user_id=2002)
    await intruder.send("/start")
    await intruder.send(keyboards.BTN_CITY)
    await intruder.send("Москва")
    await intruder.tap("Да")
    for action in ("open", "del", "pause", "resume", "text", "time", "cat", "restore"):
        await intruder.press(
            cb.Card(a=action, r=mine.id).pack(), message_id=intruder.last.message_id
        )
        foreign_reply = intruder.text
        await intruder.press(cb.Card(a=action, r=99999).pack(), message_id=intruder.last.message_id)
        # a foreign id is indistinguishable from one that does not exist
        assert foreign_reply == intruder.text, action
        assert foreign_reply in {
            "Напоминание не найдено или уже удалено.",
            "Эта кнопка уже неактуальна.",
        }
    await intruder.press(cb.CardCat(r=mine.id, c=0).pack(), message_id=intruder.last.message_id)
    assert "не найдено" in intruder.text
    (still,) = await stored(uow_factory)
    assert still.status is ReminderStatus.ACTIVE
    assert still.category_id == mine.category_id


async def test_a_foreign_notification_cannot_be_acknowledged(
    uow_factory: UowFactory, clock: FakeClock, user: Telegram
) -> None:
    await user.send("через 10 минут выпить воду")
    clock.advance(minutes=11)
    await user.deliver()
    data = user.data_of("Готово")
    intruder = Telegram(uow_factory, clock, user_id=2002)
    await intruder.send("/start")
    await intruder.send(keyboards.BTN_CITY)
    await intruder.send("Москва")
    await intruder.tap("Да")
    await intruder.press(data, message_id=intruder.last.message_id)
    assert "не найдено" in intruder.text
    await user.send("/today")
    assert "Ждут отметки" in user.text  # still unacknowledged for the real owner


async def test_a_user_who_blocked_the_bot_and_returns_gets_the_series_back(
    user: Telegram, uow_factory: UowFactory
) -> None:
    await user.send("каждый день в 8:00 таблетки")
    async with uow_factory() as uow:
        owner = await uow.users.get_by_telegram_id(1001)
        assert owner is not None
        await uow.users.set_active(owner.id, active=False)
        await uow.notifications.skip_open_for_user(owner.id, "recipient_blocked")
    await user.send("/start")
    assert "С возвращением" in user.text
    assert "Повторяющиеся напоминания (1)" in user.text
    user.clock.advance(days=1)
    assert await user.deliver() == 1


async def test_start_again_keeps_everything(user: Telegram, uow_factory: UowFactory) -> None:
    await user.send("завтра в 10:00 звонок")
    await user.send("/start")
    assert "С возвращением" in user.text
    assert len(await stored(uow_factory)) == 1


async def test_idle_throttle_entries_are_forgotten(
    uow_factory: UowFactory, clock: FakeClock
) -> None:
    from napominator.bot.middlewares import ThrottleMiddleware  # noqa: PLC0415

    tg = Telegram(uow_factory, clock, throttle=True)
    middleware = ThrottleMiddleware(tg.deps)
    for uid in range(ThrottleMiddleware._PRUNE_AT + 10):
        middleware._hits[uid].append(clock.now().timestamp())
    clock.advance(seconds=60)
    middleware._forget_idle(clock.now().timestamp())
    assert not middleware._hits
