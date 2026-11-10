"""M10: privacy text, export, account deletion, retention; S9 demo; S12 admin."""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from napominator.application.privacy import Janitor, Retention
from napominator.bot import keyboards
from napominator.domain.entities import NotificationStatus, ReminderStatus
from tests.bot.harness import Telegram, stored
from tests.support import FakeClock, UowFactory


async def test_privacy_explains_what_is_stored_and_what_is_not(user: Telegram) -> None:
    await user.send("/privacy")
    assert "Геопозицию я не сохраняю" in user.text
    assert "90 дней" in user.text
    assert "/export" in user.text
    assert user.buttons() == ["📤 Выгрузить мои данные", "🗑 Удалить все мои данные"]


async def test_export_contains_everything_and_no_secrets(user: Telegram) -> None:
    await user.send("завтра в 10:00 врач #здоровье")
    await user.send("каждый день в 8:00 таблетки")
    await user.send("/export")
    (name, payload, caption) = user.session.documents[-1]
    assert name == "napominator-20261013.json"
    assert caption is not None
    data = json.loads(payload)
    assert data["format"] == "napominator-export"
    assert data["profile"]["timezone"] == "Europe/Moscow"
    assert data["profile"]["telegram_id"] == 1001
    assert [r["text"] for r in data["reminders"]] == ["Врач", "Таблетки"]
    assert data["reminders"][0]["category"] == "Здоровье"
    assert data["reminders"][1]["repeat_rule"] == "FREQ=DAILY"
    assert len(data["categories"]) == 6
    assert len(data["deliveries"]) == 2
    assert "dedup" not in payload.decode()  # internal keys never leak
    assert "123456:TEST" not in payload.decode()  # nor the bot token


async def test_export_only_contains_the_requesters_data(
    uow_factory: UowFactory, clock: FakeClock, user: Telegram
) -> None:
    await user.send("завтра в 10:00 мой секрет")
    other = Telegram(uow_factory, clock, user_id=2002)
    await other.send("/start")
    await other.send(keyboards.BTN_CITY)
    await other.send("Москва")
    await other.tap("Да")
    await other.send("завтра в 11:00 чужое")
    await other.send("/export")
    payload = other.session.documents[-1][1].decode()
    assert "чужое" in payload.lower()
    assert "секрет" not in payload


async def test_export_button_from_the_privacy_screen(user: Telegram) -> None:
    await user.send("/privacy")
    await user.tap("Выгрузить")
    assert len(user.session.documents) == 1


async def test_deleting_the_account_needs_confirmation_and_removes_everything(
    user: Telegram,
    uow_factory: UowFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await user.send("завтра в 10:00 врач")
    await user.send("через 5 минут выпить воду")
    user.clock.advance(minutes=6)
    await user.deliver()
    await user.send("/delete_me")
    assert "Напоминаний: 2" in user.text
    await user.tap("Нет, оставить")
    assert len(await stored(uow_factory)) == 2  # nothing happened

    await user.send("/delete_me")
    await user.tap("Да, удалить навсегда")
    assert "все твои данные удалены" in user.text
    async with session_factory() as session:
        for table in ("users", "categories", "reminders", "notifications", "category_hints"):
            count = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar()  # noqa: S608
            assert count == 0, table
    user.clock.advance(days=2)
    assert await user.deliver() == 0  # scheduled reminders are gone too


async def test_a_deleted_account_can_start_over(user: Telegram, uow_factory: UowFactory) -> None:
    await user.send("/delete_me")
    await user.tap("Да, удалить навсегда")
    await user.send("/start")
    assert "Напоминатор" in user.text  # a brand-new onboarding
    assert await stored(uow_factory) == []  # a fresh account, nothing carried over


async def test_deleting_one_account_leaves_others_alone(
    uow_factory: UowFactory, clock: FakeClock, user: Telegram
) -> None:
    other = Telegram(uow_factory, clock, user_id=2002)
    await other.send("/start")
    await other.send(keyboards.BTN_CITY)
    await other.send("Москва")
    await other.tap("Да")
    await other.send("завтра в 11:00 чужое")
    await user.send("/delete_me")
    await user.tap("Да, удалить навсегда")
    assert len(await stored(uow_factory, 2002)) == 1


# --- retention ---


async def test_retention_purges_old_finished_data_but_keeps_recent(
    user: Telegram, uow_factory: UowFactory, clock: FakeClock
) -> None:
    await user.send("через 5 минут выпить воду")
    clock.advance(minutes=6)
    await user.deliver()  # reminder is DONE now
    await user.send("каждый день в 8:00 таблетки")
    await user.send("завтра в 10:00 врач")
    clock.advance(hours=18)  # tomorrow 08:00: the daily series fires once, leaving history
    await user.deliver()
    async with uow_factory() as uow:
        owner = await uow.users.get_by_telegram_id(1001)
        assert owner is not None
        doctor = next(r for r in await uow.reminders.all_for_user(owner.id) if r.text == "Врач")
        await uow.reminders.transition(
            doctor.id,
            owner.id,
            expected=[ReminderStatus.ACTIVE],
            new=ReminderStatus.DELETED,
            now=clock.now(),
        )
    janitor = Janitor(uow_factory, clock, Retention())
    first = await janitor.run_once()
    assert (first.reminders, first.notifications) == (0, 0)  # everything is recent

    clock.advance(days=8)
    second = await janitor.run_once()
    assert second.reminders == 1  # the deleted one (older than 7 days)
    clock.advance(days=100)
    third = await janitor.run_once()
    assert third.reminders == 1  # the finished one-off (older than 90 days)
    assert third.notifications >= 1  # old delivery history of the daily series
    remaining = await stored(uow_factory)
    assert [r.text for r in remaining] == ["Таблетки"]
    assert remaining[0].status is ReminderStatus.ACTIVE


async def test_retention_never_touches_open_notifications(
    user: Telegram, uow_factory: UowFactory, clock: FakeClock
) -> None:
    await user.send("каждый день в 8:00 таблетки")
    clock.advance(days=400)
    await Janitor(uow_factory, clock).run_once()
    (reminder,) = await stored(uow_factory)
    async with uow_factory() as uow:
        notes = await uow.notifications.list_for_reminder(reminder.id)
    assert [n.status for n in notes] == [NotificationStatus.PENDING]


async def test_old_update_ids_are_forgotten(
    user: Telegram, uow_factory: UowFactory, clock: FakeClock
) -> None:
    await user.send("/help")
    clock.advance(days=4)
    result = await Janitor(uow_factory, clock).run_once()
    assert result.updates >= 1


async def test_the_janitor_loop_runs_and_stops(uow_factory: UowFactory, clock: FakeClock) -> None:
    import asyncio  # noqa: PLC0415

    janitor = Janitor(uow_factory, clock, Retention(interval_seconds=0.01))
    stop = asyncio.Event()
    task = asyncio.create_task(janitor.run(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, 2)


# --- demo (S9) and admin (S12) ---


async def test_the_demo_reminder_arrives_and_can_be_acknowledged(tg: Telegram) -> None:
    await tg.send("/start")
    await tg.send(keyboards.BTN_CITY)
    await tg.send("Москва")
    await tg.tap("Да")
    assert "Попробовать на примере" in tg.all_buttons()[-1]
    await tg.tap("Попробовать")
    assert "всего пара минут" in tg.text
    tg.clock.advance(minutes=3)
    assert await tg.deliver() == 1
    assert "пробное напоминание" in tg.text.lower()
    assert tg.buttons()[0] == "✅ Готово"


async def test_admin_sees_the_state_and_others_do_not(
    uow_factory: UowFactory, clock: FakeClock
) -> None:
    admin = Telegram(uow_factory, clock, user_id=777)
    object.__setattr__(admin.deps, "admin_ids", frozenset({777}))
    await admin.send("/start")
    await admin.send(keyboards.BTN_CITY)
    await admin.send("Москва")
    await admin.tap("Да")
    await admin.send("завтра в 10:00 тайный текст")
    await admin.send("/admin")
    assert "Состояние бота" in admin.text
    assert "Активных напоминаний: 1" in admin.text
    assert "тайный" not in admin.text.lower()  # aggregate numbers only

    stranger = Telegram(uow_factory, clock, user_id=778)
    await stranger.send("/admin")
    assert "Не знаю такой команды" in stranger.text
