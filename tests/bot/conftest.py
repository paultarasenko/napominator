from __future__ import annotations

import pytest

from napominator.bot import keyboards
from tests.bot.harness import Telegram
from tests.support import FakeClock, UowFactory


@pytest.fixture
def tg(uow_factory: UowFactory, clock: FakeClock) -> Telegram:
    return Telegram(uow_factory, clock)


@pytest.fixture
async def user(tg: Telegram) -> Telegram:
    """An onboarded Moscow user (15:00 local at the fake 'now')."""
    await tg.send("/start")
    await tg.send(keyboards.BTN_CITY)
    await tg.send("Москва")
    await tg.tap("Да")
    return tg
