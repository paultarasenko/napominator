from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.exceptions import (
    ClientDecodeError,
    TelegramAPIError,
    TelegramBadRequest,
    TelegramEntityTooLarge,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.methods import SendMessage

from napominator.application.errors import (
    PermanentDeliveryError,
    RecipientBlockedError,
    RetryAfterError,
    TransientDeliveryError,
)
from napominator.application.ports import Delivery
from napominator.bot.texts import render_delivery
from napominator.domain.entities import NotificationKind
from napominator.infrastructure.telegram_notifier import TelegramNotifier

METHOD = SendMessage(chat_id=1, text="x")


def delivery(**kw: object) -> Delivery:
    base: dict[str, object] = {
        "notification_id": 1,
        "kind": NotificationKind.DUE,
        "chat_id": 555,
        "user_id": 1,
        "reminder_id": 2,
        "text": "Позвонить <маме> & папе",
        "occurrence_local": datetime(2026, 10, 14, 18, 0),
        "tz": "Europe/Moscow",
        "late_by": None,
        "is_recurring": False,
        "seq": 0,
    }
    base.update(kw)
    return Delivery(**base)  # type: ignore[arg-type]


def notifier_with(side_effect: object = None) -> tuple[TelegramNotifier, MagicMock]:
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock(side_effect=side_effect, return_value=MagicMock(message_id=777))
    return TelegramNotifier(bot, render_delivery), bot


async def test_success_returns_the_message_id_and_escapes_html() -> None:
    notifier, bot = notifier_with()
    assert await notifier.send(delivery()) == 777
    args, kwargs = bot.send_message.call_args
    assert args[0] == 555
    assert "&lt;маме&gt; &amp; папе" in args[1]
    buttons = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert buttons == ["✅ Готово", "⏰ Отложить"]


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (TelegramRetryAfter(METHOD, "Flood control", 13), RetryAfterError),
        (
            TelegramForbiddenError(METHOD, "Forbidden: bot was blocked by the user"),
            RecipientBlockedError,
        ),
        (TelegramBadRequest(METHOD, "Bad Request: chat not found"), PermanentDeliveryError),
        (TelegramEntityTooLarge(METHOD, "too large"), PermanentDeliveryError),
        (TelegramMigrateToChat(METHOD, "migrated", 99), PermanentDeliveryError),
        (TelegramNetworkError(METHOD, "timeout"), TransientDeliveryError),
        (TelegramServerError(METHOD, "502"), TransientDeliveryError),
        (TelegramUnauthorizedError(METHOD, "bad token"), TransientDeliveryError),
        (TelegramAPIError(METHOD, "something new"), TransientDeliveryError),
        (ClientDecodeError("bad json", ValueError("x"), "{"), TransientDeliveryError),
    ],
)
async def test_error_mapping(raised: Exception, expected: type[Exception]) -> None:
    notifier, _ = notifier_with(raised)
    with pytest.raises(expected):
        await notifier.send(delivery())


async def test_retry_after_carries_the_wait() -> None:
    notifier, _ = notifier_with(TelegramRetryAfter(METHOD, "Flood", 13))
    with pytest.raises(RetryAfterError) as info:
        await notifier.send(delivery())
    assert info.value.seconds == 13.0


async def test_unexpected_errors_are_not_swallowed() -> None:
    notifier, _ = notifier_with(RuntimeError("bug"))
    with pytest.raises(RuntimeError):
        await notifier.send(delivery())


def test_render_variants() -> None:
    due, _ = render_delivery(delivery())
    assert due.startswith("⏰ <b>")
    lead, lead_markup = render_delivery(delivery(kind=NotificationKind.LEAD, seq=60))
    assert "Через час" in lead
    assert "18:00" in lead
    assert lead_markup is None  # an advance notice has nothing to acknowledge
    assert "Напоминаю" in render_delivery(delivery(kind=NotificationKind.SNOOZE))[0]
    assert "Не забудьте" in render_delivery(delivery(kind=NotificationKind.NAG))[0]
    assert "Сводка" in render_delivery(delivery(kind=NotificationKind.DIGEST, text=""))[0]


def test_late_marker_names_the_original_time() -> None:
    text, _ = render_delivery(delivery(late_by=timedelta(minutes=5)))
    assert text.endswith("⏳ Должно было сработать в 18:00")
    assert "⏳" not in render_delivery(delivery())[0]
    digest, _ = render_delivery(delivery(kind=NotificationKind.DIGEST, late_by=timedelta(hours=1)))
    assert "⏳" not in digest
