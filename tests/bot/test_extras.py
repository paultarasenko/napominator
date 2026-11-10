"""S2 forwarded messages, S4 advance notice, S8 search."""

from __future__ import annotations

from tests.bot.harness import Telegram, stored
from tests.support import UowFactory


async def test_a_heads_up_arrives_before_the_main_reminder(user: Telegram) -> None:
    await user.send("завтра в 10:00 врач за час до")
    assert "Заранее: за 1 час" in user.text
    user.clock.set(user.clock.now().replace(day=14, hour=6, minute=0))  # 09:00 Moscow
    assert await user.deliver() == 1
    assert "Через час: Врач" in user.text
    assert user.buttons() == []  # a heads-up has nothing to acknowledge
    user.clock.advance(hours=1)
    assert await user.deliver() == 1
    assert user.text.startswith("⏰ Врач")
    assert user.buttons()[0] == "✅ Готово"


async def test_the_heads_up_of_a_day_before(user: Telegram) -> None:
    await user.send("в пятницу в 10:00 сдача отчёта за день до")
    user.clock.set(user.clock.now().replace(day=15, hour=7, minute=0))
    assert await user.deliver() == 1
    assert "Через день: Сдача отчёта" in user.text


async def test_a_deleted_reminder_sends_no_heads_up(user: Telegram) -> None:
    await user.send("завтра в 10:00 врач за час до")
    await user.tap("Удалить")
    user.clock.set(user.clock.now().replace(day=14, hour=6, minute=30))
    assert await user.deliver() == 0


# --- forwarded messages ---


async def forward(user: Telegram, **fields: object) -> None:
    user._message_id += 1
    message = {
        "message_id": user._message_id,
        "date": int(user.clock.now().timestamp()),
        "chat": user._chat(),
        "from": user._user(),
        "forward_origin": {"type": "hidden_user", "date": 1, "sender_user_name": "Аня"},
        **fields,
    }
    await user._feed(message=message)


async def test_a_forwarded_message_becomes_the_text_and_the_bot_asks_when(
    user: Telegram, uow_factory: UowFactory
) -> None:
    await forward(user, text="Не забудь принести документы в понедельник")
    assert "Когда напомнить" in user.text
    await user.tap("Завтра утром")
    assert "Не забудь принести документы в понедельник" in user.text
    (reminder,) = await stored(uow_factory)
    assert reminder.text.startswith("Не забудь принести")


async def test_a_forwarded_photo_caption_is_used(user: Telegram) -> None:
    await forward(
        user,
        caption="Чек на ноутбук",
        photo=[{"file_id": "x", "file_unique_id": "y", "width": 1, "height": 1}],
    )
    assert "Когда напомнить" in user.text


async def test_a_forward_without_text_is_explained(user: Telegram) -> None:
    await forward(user, photo=[{"file_id": "x", "file_unique_id": "y", "width": 1, "height": 1}])
    assert "нет текста" in user.text


# --- search ---


async def test_search_finds_by_word_form_and_typo(user: Telegram) -> None:
    await user.send("завтра в 10:00 купить хлеба")
    await user.send("завтра в 11:00 позвонить врачу")
    await user.send("/search хлеб")
    assert "Найдено: 1" in user.text
    assert any("хлеба" in b for b in user.buttons())
    await user.send("/search врач")
    assert any("врачу" in b for b in user.buttons())
    await user.send("/search позвонт")
    assert any("врачу" in b for b in user.buttons())


async def test_search_asks_for_the_query_and_reports_nothing_found(user: Telegram) -> None:
    await user.send("завтра в 10:00 купить хлеба")
    await user.send("/search")
    assert "Что искать" in user.text
    await user.send("самолёт")
    assert "Ничего не нашёл" in user.text


async def test_a_search_result_opens_the_card(user: Telegram) -> None:
    await user.send("завтра в 10:00 купить хлеба")
    await user.send("/list")
    await user.tap("Поиск")
    await user.send("хлеб")
    await user.tap("хлеба")
    assert "Купить хлеба" in user.text
    assert "⏰ Время" in user.buttons()


async def test_search_never_returns_other_users_reminders(
    user: Telegram, uow_factory: UowFactory
) -> None:
    from napominator.bot import keyboards  # noqa: PLC0415
    from tests.bot.harness import Telegram as Other  # noqa: PLC0415

    other = Other(uow_factory, user.clock, user_id=2002)
    await other.send("/start")
    await other.send(keyboards.BTN_CITY)
    await other.send("Москва")
    await other.tap("Да")
    await other.send("завтра в 10:00 чужие секреты")
    await user.send("/search секреты")
    assert "Ничего не нашёл" in user.text
