"""Telegram adapter for the ``Notifier`` port: aiogram errors -> typed delivery errors."""

from __future__ import annotations

from collections.abc import Callable

from aiogram import Bot
from aiogram.exceptions import (
    ClientDecodeError,
    TelegramAPIError,
    TelegramBadRequest,
    TelegramEntityTooLarge,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardMarkup

from napominator.application.errors import (
    PermanentDeliveryError,
    RecipientBlockedError,
    RetryAfterError,
    TransientDeliveryError,
)
from napominator.application.ports import Delivery

Renderer = Callable[[Delivery], tuple[str, InlineKeyboardMarkup | None]]


class TelegramNotifier:
    def __init__(self, bot: Bot, render: Renderer) -> None:
        self._bot = bot
        self._render = render

    async def send(self, delivery: Delivery) -> int:
        text, markup = self._render(delivery)
        try:
            message = await self._bot.send_message(delivery.chat_id, text, reply_markup=markup)
        except TelegramRetryAfter as exc:
            raise RetryAfterError(float(exc.retry_after)) from exc
        except TelegramForbiddenError as exc:  # blocked, deactivated, kicked: the user is gone
            raise RecipientBlockedError(_short(exc)) from exc
        except (TelegramBadRequest, TelegramEntityTooLarge, TelegramMigrateToChat) as exc:
            raise PermanentDeliveryError(_short(exc)) from exc
        except (TelegramAPIError, ClientDecodeError) as exc:
            # network trouble, 5xx, a conflicting poller, a bad token: not this message's fault
            raise TransientDeliveryError(_short(exc)) from exc
        return message.message_id


def _short(exc: Exception) -> str:
    # aiogram's text is Telegram's description, never the user's message; keep it brief anyway
    return f"{type(exc).__name__}: {getattr(exc, 'message', '')}"[:200]
