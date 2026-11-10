"""Delivered notifications and the buttons under them (done / snooze / undo)."""

from __future__ import annotations

from datetime import timedelta

from napominator.domain.entities import NotificationStatus, ReminderStatus
from tests.bot.harness import Telegram, stored
from tests.support import UowFactory


async def deliver_in(tg: Telegram, **delta: float) -> None:
    tg.clock.advance(**delta)
    assert await tg.deliver() >= 1


async def test_the_notification_has_done_and_snooze_buttons(user: Telegram) -> None:
    await user.send("через 20 минут выключить духовку")
    assert "через 20 минут" not in user.text  # the card shows the absolute time
    assert "сегодня, 13 октября в 15:20" in user.text
    await deliver_in(user, minutes=20)
    assert user.text.startswith("⏰ Выключить духовку")
    assert user.buttons() == ["✅ Готово", "⏰ Отложить"]


async def test_done_marks_it_and_undo_brings_the_buttons_back(
    user: Telegram, uow_factory: UowFactory
) -> None:
    await user.send("через 20 минут выключить духовку")
    await deliver_in(user, minutes=20)
    await user.tap("Готово")
    assert "✅ Готово: Выключить духовку" in user.text
    assert user.buttons() == ["↩️ Отменить"]
    await user.tap("Отменить")
    assert user.text.startswith("⏰ Выключить духовку")
    assert user.buttons() == ["✅ Готово", "⏰ Отложить"]
    await user.tap("Готово")
    await user.press(user.data_of("Отменить"))  # a second press of the old button is harmless
    (reminder,) = await stored(uow_factory)
    assert reminder.status is ReminderStatus.DONE


async def test_done_pressed_twice_is_idempotent(user: Telegram) -> None:
    await user.send("через 20 минут выключить духовку")
    await deliver_in(user, minutes=20)
    done = user.data_of("Готово")
    message_id = user.last.message_id
    await user.press(done, message_id=message_id)
    await user.press(done, message_id=message_id)
    assert "✅ Готово" in user.text


async def test_snooze_menu_and_a_quick_snooze_redelivers_later(
    user: Telegram, uow_factory: UowFactory
) -> None:
    await user.send("через 20 минут выключить духовку")
    await deliver_in(user, minutes=20)
    await user.tap("Отложить")
    assert "15 мин" in user.buttons()
    await user.tap("15 мин")
    assert "Отложено до" in user.text
    assert "15:35" in user.text
    first_message = user.last
    await deliver_in(user, minutes=15)
    assert user.last is not first_message
    assert user.text.startswith("⏰ Напоминаю: Выключить духовку")
    (reminder,) = await stored(uow_factory)
    assert reminder.status is ReminderStatus.DONE  # closed again after the repeat


async def test_snooze_to_tomorrow_uses_the_morning_anchor(user: Telegram) -> None:
    await user.send("через 20 минут выключить духовку")
    await deliver_in(user, minutes=20)
    await user.tap("Отложить")
    await user.tap("Завтра")
    assert "завтра, 14 октября в 09:00" in user.text


async def test_snooze_to_a_chosen_date_uses_the_picker(user: Telegram) -> None:
    await user.send("через 20 минут выключить духовку")
    await deliver_in(user, minutes=20)
    await user.tap("Отложить")
    await user.tap("Своё время")
    await user.tap("14", exact=True)
    await user.tap("21", exact=True)
    await user.tap("21:45", exact=True)
    assert "завтра, 14 октября в 21:45" in user.text


async def test_undoing_a_snooze_cancels_the_repeat(user: Telegram, uow_factory: UowFactory) -> None:
    await user.send("через 20 минут выключить духовку")
    await deliver_in(user, minutes=20)
    await user.tap("Отложить")
    await user.tap("1 час")
    await user.tap("Отменить")
    assert user.buttons() == ["✅ Готово", "⏰ Отложить"]
    assert await user.deliver() == 0
    user.clock.advance(hours=2)
    assert await user.deliver() == 0  # the cancelled repeat never fires
    (reminder,) = await stored(uow_factory)
    async with uow_factory() as uow:
        notes = await uow.notifications.list_for_reminder(reminder.id)
    assert [n.status for n in notes] == [NotificationStatus.SENT, NotificationStatus.SKIPPED]


async def test_an_old_undo_button_is_inert(user: Telegram) -> None:
    await user.send("через 20 минут выключить духовку")
    await deliver_in(user, minutes=20)
    await user.tap("Готово")
    user.clock.advance(minutes=16)
    await user.tap("Отменить")
    assert user.buttons() == []  # the stale button is removed instead of lying


async def test_a_late_delivery_says_when_it_was_due(user: Telegram) -> None:
    await user.send("через 20 минут выключить духовку")
    await deliver_in(user, minutes=50)
    assert "Должно было сработать в 15:20" in user.text


async def test_a_recurring_reminder_keeps_going_after_done(
    user: Telegram, uow_factory: UowFactory
) -> None:
    await user.send("каждый день в 8:00 таблетки")
    await deliver_in(user, hours=18)  # tomorrow 08:00 Moscow has passed by a bit
    await user.tap("Готово")
    (reminder,) = await stored(uow_factory)
    assert reminder.status is ReminderStatus.ACTIVE
    async with uow_factory() as uow:
        notes = await uow.notifications.list_for_reminder(reminder.id)
    assert [n.status for n in notes] == [NotificationStatus.DONE, NotificationStatus.PENDING]
    assert notes[-1].fire_at - notes[0].fire_at == timedelta(days=1)
