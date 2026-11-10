"""Less common paths through the conversation."""

from __future__ import annotations

from datetime import datetime

from napominator.bot import callbacks as cb
from napominator.bot import keyboards
from napominator.domain.entities import (
    MAX_ACTIVE_REMINDERS,
    MAX_CATEGORIES,
    NewReminder,
    ReminderStatus,
)
from tests.bot.harness import Telegram, stored
from tests.support import UowFactory

# --- pickers ---


async def test_calendar_navigation_and_cancel(user: Telegram) -> None:
    await user.send("сдать отчёт")
    await user.tap("Выбрать дату и время")
    await user.tap("▶")
    assert "Ноябрь 2026" in user.all_buttons()
    await user.tap("◀")
    assert user.buttons().count("Октябрь 2026") >= 1
    await user.tap("Отмена")
    assert "Отменено" in user.text
    await user.send("/cancel")
    assert "нечего отменять" in user.text


async def test_going_back_from_minutes_to_hours(user: Telegram) -> None:
    await user.send("сдать отчёт")
    await user.tap("Выбрать дату и время")
    await user.tap("20", exact=True)
    await user.tap("18", exact=True)
    assert "Выбери минуты" in user.text
    await user.tap("◀ Часы")
    assert "Выбери час" in user.text
    await user.tap("09", exact=True)
    await user.tap("09:05", exact=True)
    assert "вт, 20 октября в 09:05" in user.text


async def test_a_stale_picker_button_does_nothing_harmful(user: Telegram) -> None:
    await user.send("сдать отчёт")
    await user.tap("Выбрать дату и время")
    stale_day = user.data_of("20")
    await user.tap("Отмена")
    before = len(user.messages)
    await user.press(stale_day, message_id=user.messages[-2].message_id)  # state is gone
    assert len(user.messages) in (before, before + 1)


# --- confirm / clarify ---


async def test_confirming_an_uncertain_reading_creates_it(
    user: Telegram, uow_factory: UowFactory
) -> None:
    await user.send("в 5 корпусе встреча")
    await user.tap("Верно")
    assert "Запланировано" in user.text
    assert len(await stored(uow_factory)) == 1


async def test_cancel_chip(user: Telegram, uow_factory: UowFactory) -> None:
    await user.send("позвонить маме")
    await user.tap("Отмена")
    assert "Отменено" in user.text
    assert await stored(uow_factory) == []


async def test_a_chip_without_a_pending_reminder_is_stale(user: Telegram) -> None:
    await user.send("позвонить маме")
    chip = user.data_of("Через час")
    await user.send("/cancel")
    await user.press(chip, message_id=user.messages[-3].message_id)
    assert "неактуальна" in user.text


async def test_too_frequent_repeats_are_explained(user: Telegram) -> None:
    await user.send("каждые 3 минуты проверять почту")
    assert "минимальный интервал" in user.text


async def test_a_repeat_that_ends_before_it_starts_is_explained(user: Telegram) -> None:
    await user.send("каждый день до 13.10 в 9:00 зарядка")
    assert "заканчивается раньше" in user.text


async def test_an_invalid_date_is_explained(user: Telegram) -> None:
    await user.send("31 апреля в 10:00 встреча")
    assert "не существует" in user.text


async def test_today_without_a_time_after_the_morning_asks_for_the_time(user: Telegram) -> None:
    await user.send("сегодня позвонить маме")
    assert "Во сколько" in user.text


async def test_a_too_long_text_is_rejected(user: Telegram, uow_factory: UowFactory) -> None:
    await user.send("завтра в 10:00 " + "а" * 1200)
    assert await stored(uow_factory) == [] or len((await stored(uow_factory))[0].text) <= 1000


async def test_the_active_reminder_limit_is_enforced(
    user: Telegram, uow_factory: UowFactory
) -> None:
    async with uow_factory() as uow:
        owner = await uow.users.get_by_telegram_id(1001)
        assert owner is not None
        for _ in range(MAX_ACTIVE_REMINDERS):
            await uow.reminders.add(
                NewReminder(owner.id, "x", "Europe/Moscow", datetime(2027, 1, 1)),
                user.clock.now(),
            )
    await user.send("завтра в 10:00 ещё одно")
    assert "лимит" in user.text


# --- onboarding corners ---


async def test_wrong_zone_can_be_corrected(tg: Telegram) -> None:
    await tg.send("/start")
    await tg.send(keyboards.BTN_CITY)
    await tg.send("Москва")
    await tg.tap("Нет, исправить")
    assert keyboards.BTN_CITY in tg.buttons()
    await tg.send(keyboards.BTN_CITY)
    await tg.send("Омск")
    assert "18:00" in tg.text  # Omsk is UTC+6


async def test_zone_list_has_a_back_button(tg: Telegram) -> None:
    await tg.send("/start")
    await tg.send(keyboards.BTN_ZONE_LIST)
    await tg.tap("СНГ")
    assert "Минск" in tg.buttons()
    await tg.tap("Назад")
    assert "Россия" in tg.buttons()


async def test_an_unknown_location_is_reported(tg: Telegram) -> None:
    await tg.send("/start")
    await tg.send_location(91.0, 0.0)  # not a real latitude
    assert "Не получилось определить" in tg.text


async def test_confirming_without_a_candidate_is_stale(user: Telegram) -> None:
    await user.send("/settings")
    await user.tap("Часовой пояс")
    await user.send(keyboards.BTN_CITY)
    await user.send("Москва")
    chip = user.data_of("Да")
    await user.send("/cancel")
    await user.send("/start")  # clears the FSM, including the candidate
    await user.press(chip, message_id=user.messages[-3].message_id)
    assert "неактуальна" in user.transcript()


async def test_choosing_the_same_zone_just_confirms(user: Telegram) -> None:
    await user.send("/settings")
    await user.tap("Часовой пояс")
    await user.send(keyboards.BTN_CITY)
    await user.send("Москва")
    await user.tap("Да")
    assert "Часовой пояс: Москва" in user.text


# --- categories corners ---


async def test_the_category_limit_is_reported(user: Telegram) -> None:
    await user.send("/categories")
    for i in range(MAX_CATEGORIES - 6):
        await user.tap("Новая категория")
        await user.send(f"Тема {i}")
    await user.tap("Новая категория")
    await user.send("Лишняя")
    assert "лимит категорий" in user.text


async def test_a_vanished_category_is_reported(user: Telegram) -> None:
    await user.send("/categories")
    await user.press(cb.CatMenu(a="open", c=99999).pack())
    assert "не найдено" in user.text


async def test_renaming_into_an_existing_name_is_refused(user: Telegram) -> None:
    await user.send("/categories")
    await user.tap("Дом · 0")
    await user.tap("Переименовать")
    await user.send("работа")
    assert "уже есть" in user.text
    await user.send("Усадьба")
    assert "Усадьба · 0" in " ".join(user.buttons())


# --- cards & lists corners ---


async def test_resuming_a_series_that_is_not_paused_says_it_is_stale(user: Telegram) -> None:
    await user.send("каждый день в 8:00 таблетки")
    resume = cb.Card(a="resume", r=1).pack()
    await user.press(resume)
    assert "неактуальна" in user.transcript()


async def test_editing_text_of_a_deleted_reminder(user: Telegram) -> None:
    await user.send("завтра в 10:00 звонок")
    await user.tap("Текст")
    await user.tap("Удалить")
    await user.send("Новый текст")
    assert user.text  # the bot answers instead of crashing


async def test_a_too_long_new_text_is_rejected(user: Telegram, uow_factory: UowFactory) -> None:
    await user.send("завтра в 10:00 звонок")
    await user.tap("Текст")
    await user.send("б" * 1500)
    assert "не больше 1000" in user.text
    (reminder,) = await stored(uow_factory)
    assert reminder.text == "Звонок"


async def test_nonsense_in_the_edit_time_flow(user: Telegram) -> None:
    await user.send("завтра в 10:00 звонок")
    await user.tap("Время")
    await user.send("абракадабра")
    assert "Не понял время" in user.text
    await user.tap("Через час")
    assert "Время обновлено" in user.text


async def test_the_today_screen_links_to_the_full_list(user: Telegram) -> None:
    await user.send("завтра в 10:00 звонок")
    await user.send("/today")
    await user.tap("Все напоминания")
    assert "Ближайшие — 1" in user.text


async def test_the_new_button_on_the_today_screen_starts_creation(user: Telegram) -> None:
    await user.send("/today")
    await user.tap("Новое")
    assert "О чём напомнить" in user.text
    await user.send("позвонить маме")
    assert "Когда напомнить" in user.text


async def test_help_from_the_settings_screen(user: Telegram) -> None:
    await user.send("/settings")
    await user.tap("Помощь")
    assert "Как пользоваться" in user.text


async def test_snoozing_a_deleted_reminder_is_refused(
    user: Telegram, uow_factory: UowFactory
) -> None:
    await user.send("через 10 минут выпить воду")
    user.clock.advance(minutes=11)
    await user.deliver()
    (reminder,) = await stored(uow_factory)
    notification_message = user.last
    async with uow_factory() as uow:
        owner = await uow.users.get_by_telegram_id(1001)
        assert owner is not None
        await uow.reminders.transition(
            reminder.id,
            owner.id,
            expected=[ReminderStatus.DONE],
            new=ReminderStatus.DELETED,
            now=user.clock.now(),
        )
    await user.tap("Отложить")
    await user.tap("1 час")
    assert user.last is notification_message
    assert user.buttons(notification_message) == []  # the dead buttons were removed


async def test_snooze_evening_and_three_hours(user: Telegram) -> None:
    await user.send("через 10 минут выпить воду")
    user.clock.advance(minutes=11)
    await user.deliver()
    await user.tap("Отложить")
    await user.tap("Вечером")
    assert "сегодня, 13 октября в 19:00" in user.text
    await user.tap("Отменить")
    await user.tap("Отложить")
    await user.tap("3 часа")
    assert "18:11" in user.text
