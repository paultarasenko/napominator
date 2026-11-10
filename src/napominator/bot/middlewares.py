"""Per-update plumbing: dedup + transaction + user, throttling, error reporting."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, ErrorEvent, Message, TelegramObject, Update, User

from napominator.application import users
from napominator.bot import texts
from napominator.bot.deps import AppDeps
from napominator.infrastructure.logging import get_logger

log = get_logger("bot")
Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class SessionMiddleware(BaseMiddleware):
    """One transaction per update: dedup the update id, load/register the user, run the handler.

    Any exception rolls the whole update back, including the dedup row, so a retry is processed.
    """

    def __init__(self, deps: AppDeps) -> None:
        self._deps = deps

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        tg_user: User | None = data.get("event_from_user")
        chat = data.get("event_chat")
        if tg_user is None or tg_user.is_bot or (chat is not None and chat.type != "private"):
            return None  # the bot lives in private chats only
        update: Update | None = data.get("event_update")
        now = self._deps.clock.now()
        async with self._deps.uow_factory() as uow:
            if update is not None and not await uow.processed_updates.register(
                update.update_id, now
            ):
                log.info("update_replayed", update_id=update.update_id)
                return None
            registration = await users.register(
                uow, tg_user.id, tg_user.username, tg_user.first_name, now
            )
            data.update(uow=uow, user=registration.user, registration=registration, deps=self._deps)
            result = await handler(event, data)
        self._deps.wake()  # commit done: the dispatcher may have new work
        return result


class ThrottleMiddleware(BaseMiddleware):
    """Drops message floods (a user typing faster than ``limit`` messages per ``window`` s)."""

    _PRUNE_AT = 5000  # users tracked before idle ones are forgotten

    def __init__(self, deps: AppDeps, limit: int = 6, window: float = 3.0) -> None:
        self._deps = deps
        self._limit = limit
        self._window = window
        self._hits: dict[int, deque[float]] = defaultdict(deque)
        self._warned: dict[int, float] = {}

    def _forget_idle(self, now: float) -> None:
        stale = [
            uid for uid, hits in self._hits.items() if not hits or now - hits[-1] > self._window
        ]
        for uid in stale:
            del self._hits[uid]
            self._warned.pop(uid, None)

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        tg_user: User | None = data.get("event_from_user")
        if tg_user is None or not isinstance(event, Message):
            return await handler(event, data)
        now = self._deps.clock.now().timestamp()
        if len(self._hits) > self._PRUNE_AT:
            self._forget_idle(now)
        hits = self._hits[tg_user.id]
        while hits and now - hits[0] > self._window:
            hits.popleft()
        hits.append(now)
        if len(hits) <= self._limit:
            return await handler(event, data)
        if now - self._warned.get(tg_user.id, 0.0) > self._window * 3:
            self._warned[tg_user.id] = now
            await event.answer(texts.BUSY)
        return None


async def report_error(event: ErrorEvent) -> bool:
    """Log without user content and tell the user something went wrong. Always "handled"."""
    update, exception = event.update, event.exception
    log.error("handler_failed", update_id=update.update_id, error=type(exception).__name__)
    if update.callback_query is not None:
        await _safe_answer(update.callback_query, texts.GENERIC_ERROR)
    elif update.message is not None:
        try:
            await update.message.answer(texts.GENERIC_ERROR)
        except Exception:
            log.warning("error_reply_failed", update_id=update.update_id)
    return True


async def _safe_answer(query: CallbackQuery, text: str) -> None:
    try:
        await query.answer(text, show_alert=False)
    except Exception:
        log.warning("callback_answer_failed")
