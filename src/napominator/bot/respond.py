"""One way to answer both messages and button presses."""

from __future__ import annotations

import contextlib

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

Markup = InlineKeyboardMarkup | ReplyKeyboardMarkup | ReplyKeyboardRemove | None


class Responder:
    def __init__(self, event: Message | CallbackQuery) -> None:
        self._event = event

    @property
    def message(self) -> Message | None:
        if isinstance(self._event, Message):
            return self._event
        inner = self._event.message
        return inner if isinstance(inner, Message) else None

    @property
    def is_callback(self) -> bool:
        return isinstance(self._event, CallbackQuery)

    async def send(self, text: str, markup: Markup = None, **kwargs: object) -> Message | None:
        """A new message in the same chat."""
        message = self.message
        if message is not None:
            return await message.answer(text, reply_markup=markup, **kwargs)  # type: ignore[arg-type]
        event = self._event
        if isinstance(event, CallbackQuery) and event.bot is not None:
            return await event.bot.send_message(
                event.from_user.id,
                text,
                reply_markup=markup,
                **kwargs,  # type: ignore[arg-type]
            )
        return None

    async def edit(self, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
        """Edit the message the button was on; fall back to a new message otherwise."""
        message = self.message
        if message is None or not self.is_callback:
            await self.send(text, markup)
            return
        try:
            await message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as exc:
            if "not modified" not in str(exc).lower():
                await self.send(text, markup)

    async def ack(self, text: str | None = None, *, alert: bool = False) -> None:
        """Answer the callback query (stops the client spinner); a no-op for messages."""
        if isinstance(self._event, CallbackQuery):
            with contextlib.suppress(TelegramBadRequest):
                await self._event.answer(text, show_alert=alert)

    async def edit_markup(self, markup: InlineKeyboardMarkup | None) -> None:
        """Replace only the buttons, keeping the message text."""
        message = self.message
        if message is not None and self.is_callback:
            with contextlib.suppress(TelegramBadRequest):
                await message.edit_reply_markup(reply_markup=markup)
