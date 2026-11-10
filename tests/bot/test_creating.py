"""Creating reminders through the real handlers."""

from __future__ import annotations

from datetime import UTC, datetime

from napominator.domain.entities import NotificationStatus
from tests.bot.harness import Telegram, stored
from tests.support import UowFactory


async def test_a_clear_phrase_is_created_immediately(
    user: Telegram, uow_factory: UowFactory
) -> None:
    await user.send("завтра в 18:00 позвонить маме")
    assert "Запланировано" in user.text
    assert "завтра, 14 октября в 18:00" in user.text
    assert "Позвонить маме" in user.text
    assert user.buttons() == ["⏰ Время", "✏️ Текст", "🗂 Категория", "🗑 Удалить"]
    (reminder,) = await stored(uow_factory)
    assert (reminder.text, reminder.tz) == ("Позвонить маме", "Europe/Moscow")
    assert user.wakes > 0  # the delivery engine was told about the new work


async def test_a_missing_time_is_asked_with_chips_and_a_chip_completes_it(
    user: Telegram, uow_factory: UowFactory
) -> None:
    await user.send("позвонить маме")
    assert "Когда напомнить" in user.text
    assert "📅 Выбрать дату и время" in user.buttons()
    await user.tap("Завтра утром")
    assert "завтра, 14 октября в 09:00" in user.text
    (reminder,) = await stored(uow_factory)
    assert reminder.text == "Позвонить маме"


async def test_a_typed_time_reply_completes_the_pending_reminder(user: Telegram) -> None:
    await user.send("купить хлеб")
    await user.send("в пятницу в 10")
    assert "пт, 16 октября в 10:00" in user.text
    assert "Купить хлеб" in user.text


async def test_garbage_while_waiting_for_a_time_keeps_waiting(user: Telegram) -> None:
    await user.send("купить хлеб")
    await user.send("абракадабра")
    assert "Не понял время" in user.text
    await user.tap("Через час")
    assert "Запланировано" in user.text


async def test_time_without_text_asks_for_the_text(user: Telegram) -> None:
    await user.send("завтра в 9 утра")
    assert "О чём напомнить" in user.text
    assert "завтра, 14 октября в 09:00" in user.text
    await user.send("Забрать посылку")
    assert "Запланировано" in user.text
    assert "Забрать посылку" in user.text


async def test_a_past_time_is_refused_with_a_hint(user: Telegram, uow_factory: UowFactory) -> None:
    await user.send("сегодня в 14:00 позвонить")
    assert "уже прошло" in user.text
    assert await stored(uow_factory) == []


async def test_a_whole_new_phrase_while_waiting_replaces_the_pending_one(
    user: Telegram, uow_factory: UowFactory
) -> None:
    await user.send("позвонить маме")
    await user.send("купить хлеб завтра в 9")
    assert "Купить хлеб" in user.text
    reminders = await stored(uow_factory)
    assert [r.text for r in reminders] == ["Купить хлеб"]


async def test_the_calendar_picker_builds_a_date_and_time(
    user: Telegram, uow_factory: UowFactory
) -> None:
    await user.send("сдать отчёт")
    await user.tap("Выбрать дату и время")
    assert "Октябрь 2026" in user.all_buttons()
    await user.tap("20", exact=True)
    assert "Выбери час" in user.text
    await user.tap("18", exact=True)
    assert "Выбери минуты" in user.text
    await user.tap("18:30", exact=True)
    assert "вт, 20 октября в 18:30" in user.text
    (reminder,) = await stored(uow_factory)
    assert reminder.dtstart_local == datetime(2026, 10, 20, 18, 30)


async def test_past_days_in_the_calendar_do_nothing(
    user: Telegram, uow_factory: UowFactory
) -> None:
    await user.send("сдать отчёт")
    await user.tap("Выбрать дату и время")
    before = len(user.messages)
    await user.tap("·", exact=True)  # a past day of the current month
    assert len(user.messages) == before
    assert await stored(uow_factory) == []


async def test_a_recurring_phrase_shows_the_rule_and_queues_one_occurrence(
    user: Telegram, uow_factory: UowFactory
) -> None:
    await user.send("по будням в 8:30 принять таблетки")
    assert "🔁 по будням" in user.text
    assert "ср, 14 октября в 08:30" not in user.text  # it is "завтра"
    assert "завтра, 14 октября в 08:30" in user.text
    (reminder,) = await stored(uow_factory)
    async with uow_factory() as uow:
        notes = await uow.notifications.list_for_reminder(reminder.id)
    assert [(n.status, n.fire_at) for n in notes] == [
        (NotificationStatus.PENDING, datetime(2026, 10, 14, 5, 30, tzinfo=UTC))
    ]


async def test_an_uncertain_reading_is_confirmed_before_creating(
    user: Telegram, uow_factory: UowFactory
) -> None:
    await user.send("в 5 корпусе встреча")
    assert "Правильно ли я понял" in user.text
    assert user.buttons() == ["✅ Верно", "✏️ Изменить время", "✖ Отмена"]
    assert await stored(uow_factory) == []
    await user.tap("Изменить время")
    assert "Когда напомнить" in user.text


async def test_cancel_drops_the_pending_reminder(user: Telegram, uow_factory: UowFactory) -> None:
    await user.send("позвонить маме")
    await user.send("/cancel")
    assert "Отменено" in user.text
    await user.send("/cancel")
    assert "нечего отменять" in user.text
    assert await stored(uow_factory) == []


async def test_the_new_command_walks_through_steps(user: Telegram) -> None:
    await user.send("/new")
    assert "О чём напомнить" in user.text
    await user.send("полить цветы")
    assert "Когда напомнить" in user.text
    await user.tap("Сегодня вечером")
    assert "сегодня, 13 октября в 19:00" in user.text


async def test_an_explicit_tag_picks_a_category_and_a_dictionary_word_suggests_one(
    user: Telegram,
) -> None:
    await user.send("завтра в 10:00 планерка #работа")
    assert "💼 Работа" in user.text
    assert "(предложено)" not in user.text
    await user.send("завтра в 11:00 врач")
    assert "💊 Здоровье" in user.text
    assert "(предложено)" in user.text


async def test_a_new_tag_creates_the_category_on_the_fly(user: Telegram) -> None:
    await user.send("завтра в 10:00 полить цветы #цветы")
    assert "цветы" in user.text
    await user.send("/categories")
    assert "цветы" in " ".join(user.buttons())
