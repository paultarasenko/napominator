from __future__ import annotations

from napominator.bot import callbacks as cb
from napominator.domain.entities import ReminderStatus
from tests.bot.harness import Telegram, stored
from tests.support import UowFactory


async def test_today_is_empty_at_first(user: Telegram) -> None:
    await user.send("/today")
    assert "Сегодня, 13 октября" in user.text
    assert "ничего не запланировано" in user.text


async def test_today_lists_the_rest_of_the_day_and_hides_tomorrow(user: Telegram) -> None:
    await user.send("сегодня в 18:00 позвонить маме")
    await user.send("сегодня в 20:30 тренировка")
    await user.send("завтра в 9 планерка")
    await user.send("📅 Сегодня")
    assert "• 18:00 — Позвонить маме" in user.text
    assert "• 20:30 — Тренировка" in user.text
    assert "Планерка" not in user.text


async def test_delivered_but_unacknowledged_items_wait_in_today(user: Telegram) -> None:
    await user.send("через 10 минут выпить воду")
    user.clock.advance(minutes=11)
    await user.deliver()
    await user.send("/today")
    assert "Ждут отметки" in user.text
    assert "Выпить воду" in user.text
    await user.tap("✅ Выпить воду")
    await user.send("/today")
    assert "Ждут отметки" not in user.text


async def test_list_tabs_pages_and_opening_a_card(user: Telegram) -> None:
    for i in range(10):
        await user.send(f"завтра в {10 + i}:00 дело {i}")
    await user.send("/list")
    assert "Ближайшие" in user.text
    assert "Страница 1 из 2" in user.text
    assert "1/2" in user.buttons()
    await user.tap("▶")
    assert "Страница 2 из 2" in user.text
    assert sum("Дело" in b or "дело" in b for b in user.buttons()) == 2
    await user.tap("Дело 9")
    assert "Дело 9" in user.text
    assert "⏰ Время" in user.buttons()


async def test_recurring_and_done_tabs(user: Telegram) -> None:
    await user.send("каждый день в 8:00 таблетки")
    await user.send("завтра в 10:00 врач")
    await user.send("/list")
    await user.tap("🔁 Повторы")
    assert "Повторяющиеся — 1" in user.text
    await user.tap("Выполнено")
    assert "Здесь пока пусто" in user.text


async def test_a_category_chip_filters_the_list(user: Telegram) -> None:
    await user.send("завтра в 10:00 планерка #работа")
    await user.send("завтра в 11:00 купить хлеб")
    await user.send("/list")
    await user.tap("💼 Работа")
    assert "Работа" in user.text
    assert any("планерка" in b.lower() for b in user.buttons())
    assert not any("хлеб" in b.lower() for b in user.buttons())


# --- the card ---


async def test_editing_the_text(user: Telegram, uow_factory: UowFactory) -> None:
    await user.send("завтра в 10:00 звонок")
    await user.tap("Текст")
    assert "новый текст" in user.text
    await user.send("Позвонить бабушке")
    assert "Позвонить бабушке" in user.text
    (reminder,) = await stored(uow_factory)
    assert reminder.text == "Позвонить бабушке"


async def test_editing_the_time_by_typing_and_with_the_calendar(
    user: Telegram, uow_factory: UowFactory
) -> None:
    await user.send("завтра в 10:00 звонок")
    await user.tap("Время")
    await user.send("в пятницу в 18:30")
    assert "Время обновлено" in user.text
    assert "пт, 16 октября в 18:30" in user.text
    await user.tap("Время")
    await user.tap("Выбрать дату и время")
    await user.tap("21", exact=True)
    await user.tap("07", exact=True)
    await user.tap("07:15", exact=True)
    assert "ср, 21 октября в 07:15" in user.text
    (reminder,) = await stored(uow_factory)
    assert reminder.dtstart_local.isoformat() == "2026-10-21T07:15:00"


async def test_a_past_time_in_the_edit_flow_is_refused(user: Telegram) -> None:
    await user.send("завтра в 10:00 звонок")
    await user.tap("Время")
    await user.send("сегодня в 14:00")
    assert "уже прошло" in user.text


async def test_changing_the_category_learns_the_habit(user: Telegram) -> None:
    await user.send("завтра в 10:00 врач")
    assert "💊 Здоровье" in user.text  # dictionary suggestion
    await user.tap("Категория")
    await user.tap("Работа")
    assert "💼 Работа" in user.text
    await user.send("послезавтра в 10:00 врач")
    assert "💼 Работа" in user.text  # the user's own choice now beats the dictionary


async def test_delete_and_restore(user: Telegram, uow_factory: UowFactory) -> None:
    await user.send("завтра в 10:00 звонок")
    await user.tap("Удалить")
    assert "Удалено" in user.text
    assert user.buttons() == ["↩️ Вернуть"]
    (gone,) = await stored(uow_factory)
    assert gone.status is ReminderStatus.DELETED
    await user.tap("Вернуть")
    assert "Возвращено" in user.text
    (back,) = await stored(uow_factory)
    assert back.status is ReminderStatus.ACTIVE
    await user.deliver()
    assert await user.deliver() == 0  # still waiting for tomorrow 10:00


async def test_a_deleted_reminder_never_fires(user: Telegram) -> None:
    await user.send("через 10 минут выпить воду")
    await user.tap("Удалить")
    user.clock.advance(minutes=30)
    assert await user.deliver() == 0


async def test_pause_and_resume_a_series(user: Telegram) -> None:
    await user.send("каждый день в 8:00 таблетки")
    await user.tap("Пауза")
    assert "На паузе" in user.text
    user.clock.advance(days=2)
    assert await user.deliver() == 0
    await user.tap("Возобновить")
    assert "На паузе" not in user.text
    user.clock.advance(days=1)
    assert await user.deliver() == 1


async def test_a_card_of_a_missing_reminder_says_so(user: Telegram) -> None:
    await user.send("завтра в 10:00 звонок")
    await user.press(cb.Card(a="open", r=99999).pack())
    assert "не найдено" in user.text
