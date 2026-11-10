from __future__ import annotations

from datetime import time

from napominator.bot import keyboards
from tests.bot.harness import Telegram, stored
from tests.support import UowFactory


async def test_the_category_screen_lists_defaults_with_counts(user: Telegram) -> None:
    await user.send("завтра в 10:00 планерка #работа")
    await user.send(keyboards.BTN_CATEGORIES)
    buttons = user.buttons()
    assert "💼 Работа · 1" in buttons
    assert "🏠 Дом · 0" in buttons
    assert "➕ Новая категория" in buttons


async def test_creating_a_category_with_and_without_emoji(user: Telegram) -> None:
    await user.send("/categories")
    await user.tap("Новая категория")
    await user.send("🎸 Музыка")
    assert "🎸 Музыка · 0" in user.buttons()
    await user.tap("Новая категория")
    await user.send("Спорт")
    assert "🏃 Спорт · 0" in user.buttons()


async def test_bad_and_duplicate_names_are_explained_and_the_flow_continues(
    user: Telegram,
) -> None:
    await user.send("/categories")
    await user.tap("Новая категория")
    await user.send("работа")
    assert "уже есть" in user.text
    await user.send("x" * 40)
    assert "от 1 до 32" in user.text
    await user.send("Дача")
    assert "🌱 Дача · 0" in user.buttons()


async def test_rename_and_change_the_emoji(user: Telegram) -> None:
    await user.send("/categories")
    await user.tap("Дом · 0")
    await user.tap("Переименовать")
    await user.send("Семья")
    assert "Семья · 0" in " ".join(user.buttons())
    await user.tap("Семья · 0")
    await user.tap("Эмодзи")
    await user.send("не эмодзи")
    assert "Не вижу эмодзи" in user.text
    await user.send("🧸")
    assert "🧸 Семья · 0" in user.buttons()


async def test_reordering(user: Telegram) -> None:
    await user.send("/categories")
    first_before = user.buttons()[0]
    await user.tap("Работа · 0")
    await user.tap("⬆️")
    assert user.buttons()[0] == "💼 Работа · 0"
    assert user.buttons()[1] == first_before


async def test_deleting_a_category_moves_its_reminders_and_keeps_them(
    user: Telegram, uow_factory: UowFactory
) -> None:
    await user.send("завтра в 10:00 планерка #работа")
    await user.send("/categories")
    await user.tap("Работа · 1")
    await user.tap("Удалить")
    assert "Они не удалятся" in user.text
    await user.tap("Дом")
    assert "Перенесено напоминаний: 1" in user.transcript()
    (reminder,) = await stored(uow_factory)
    assert reminder.category_id is not None
    assert "Работа" not in " ".join(user.buttons())


async def test_deleting_an_empty_category_asks_once(user: Telegram) -> None:
    await user.send("/categories")
    await user.tap("Даты · 0")
    await user.tap("Удалить")
    assert "Удалить категорию" in user.text
    await user.tap("🗑 Удалить")
    assert "Даты" not in " ".join(user.buttons())


# --- settings ---


async def test_the_settings_screen_shows_zone_and_anchors(user: Telegram) -> None:
    await user.send(keyboards.BTN_SETTINGS)
    assert "Москва" in user.text
    assert "09:00" in user.text
    assert "19:00" in user.text


async def test_changing_the_morning_time_is_used_by_the_parser(
    user: Telegram, uow_factory: UowFactory
) -> None:
    await user.send("/settings")
    await user.tap("Утро")
    await user.tap("07", exact=True)
    await user.tap("07:30", exact=True)
    assert "Сохранено" in user.text
    async with uow_factory() as uow:
        stored_user = await uow.users.get_by_telegram_id(1001)
    assert stored_user is not None
    assert stored_user.morning_time == time(7, 30)
    await user.send("завтра утром зарядка")
    assert "завтра, 14 октября в 07:30" in user.text


async def test_anchors_must_stay_ordered(user: Telegram) -> None:
    await user.send("/settings")
    await user.tap("Утро")
    await user.tap("22", exact=True)
    await user.tap("22:00", exact=True)
    assert "Утро должно быть раньше" in user.text


async def test_changing_the_zone_without_reminders_is_immediate(user: Telegram) -> None:
    await user.send("/settings")
    await user.tap("Часовой пояс")
    await user.send(keyboards.BTN_CITY)
    await user.send("Новосибирск")
    assert "19:00" in user.text
    await user.tap("Да")
    assert "Часовой пояс обновлён" in user.text


async def test_changing_the_zone_with_reminders_asks_how_to_recalculate(
    user: Telegram, uow_factory: UowFactory
) -> None:
    await user.send("завтра в 10:00 звонок")  # 07:00Z
    await user.send("/settings")
    await user.tap("Часовой пояс")
    await user.send(keyboards.BTN_CITY)
    await user.send("Новосибирск")
    await user.tap("Да")
    assert "Как их пересчитать" in user.text
    await user.tap("Время на часах то же")
    assert "Пересчитано напоминаний: 1" in user.text
    (reminder,) = await stored(uow_factory)
    assert reminder.tz == "Asia/Novosibirsk"
    assert reminder.dtstart_local.hour == 10  # wall-clock 10:00 kept


async def test_changing_the_zone_and_keeping_the_moment(
    user: Telegram, uow_factory: UowFactory
) -> None:
    await user.send("завтра в 10:00 звонок")
    await user.send("/settings")
    await user.tap("Часовой пояс")
    await user.send(keyboards.BTN_CITY)
    await user.send("Новосибирск")
    await user.tap("Да")
    await user.tap("Тот же момент")
    (reminder,) = await stored(uow_factory)
    assert reminder.dtstart_local.hour == 14  # 10:00 Moscow == 14:00 Novosibirsk
