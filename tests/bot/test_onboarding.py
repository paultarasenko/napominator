from __future__ import annotations

from napominator.bot import keyboards
from tests.bot.harness import Telegram
from tests.support import UowFactory


async def test_start_greets_and_offers_three_ways_to_set_the_zone(tg: Telegram) -> None:
    await tg.send("/start")
    assert "Напоминатор" in tg.text
    assert tg.buttons() == [keyboards.BTN_LOCATION, keyboards.BTN_CITY, keyboards.BTN_ZONE_LIST]


async def test_city_flow_ends_with_a_working_menu_and_default_categories(
    tg: Telegram, uow_factory: UowFactory
) -> None:
    await tg.send("/start")
    await tg.send(keyboards.BTN_CITY)
    assert "город" in tg.text
    await tg.send("Екатеринбург")
    assert "17:00" in tg.text  # 12:00Z in Yekaterinburg (UTC+5)
    assert tg.buttons() == ["✅ Да", "✏️ Нет, исправить"]
    await tg.tap("Да")
    assert "Готово! Часовой пояс" in tg.transcript()
    assert keyboards.BTN_TODAY in tg.all_buttons()
    assert tg.buttons() == ["🎯 Попробовать на примере"]  # the demo offer comes right after
    async with uow_factory() as uow:
        user = await uow.users.get_by_telegram_id(1001)
        assert user is not None
        assert (user.timezone, user.onboarded_at is not None) == ("Asia/Yekaterinburg", True)
        assert len(await uow.categories.list(user.id)) == 6


async def test_a_misspelled_city_still_resolves(tg: Telegram) -> None:
    await tg.send("/start")
    await tg.send(keyboards.BTN_CITY)
    await tg.send("новосибирс")
    assert "19:00" in tg.text  # Novosibirsk is UTC+7


async def test_unknown_city_asks_again_and_keeps_the_state(tg: Telegram) -> None:
    await tg.send("/start")
    await tg.send(keyboards.BTN_CITY)
    await tg.send("абвгдеёжзийклмн")
    assert "Не нашёл" in tg.text
    await tg.send(keyboards.BTN_CITY)
    await tg.send("Москва")
    assert "15:00" in tg.text


async def test_location_sets_the_zone_without_storing_coordinates(
    tg: Telegram, uow_factory: UowFactory
) -> None:
    await tg.send("/start")
    await tg.send_location(55.75, 37.62)
    assert "15:00" in tg.text
    await tg.tap("Да")
    async with uow_factory() as uow:
        user = await uow.users.get_by_telegram_id(1001)
    assert user is not None
    assert user.timezone == "Europe/Moscow"
    assert "55.75" not in tg.transcript()


async def test_picking_a_zone_from_the_list(tg: Telegram) -> None:
    await tg.send("/start")
    await tg.send(keyboards.BTN_ZONE_LIST)
    await tg.tap("Россия")
    await tg.tap("Владивосток")
    assert "22:00" in tg.text  # 12:00Z in Vladivostok (UTC+10)
