"""Drives the real handlers with a fake Telegram session (no network)."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageReplyMarkup,
    EditMessageText,
    SendDocument,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import Chat, InlineKeyboardMarkup, Message, ReplyKeyboardMarkup, Update

from napominator.application.dispatcher import Dispatcher as DeliveryEngine
from napominator.application.dispatcher import DispatcherConfig
from napominator.bot.app import build_dispatcher
from napominator.bot.deps import AppDeps
from napominator.bot.texts import render_delivery
from napominator.domain.entities import Reminder, ReminderFilter, ReminderStatus
from napominator.infrastructure.telegram_notifier import TelegramNotifier
from napominator.infrastructure.tzlookup import TimezoneResolver
from napominator.nlp import RuleBasedParser
from tests.support import FakeClock, FakeSleeper, UowFactory

_RESOLVER = TimezoneResolver()  # the city index is expensive: share it across tests
TAG = re.compile(r"<[^>]+>")


@dataclass(slots=True)
class Shown:
    """A message as the user currently sees it."""

    message_id: int
    text: str
    markup: Any = None  # inline / reply keyboard, or a removal marker
    history: list[str] = field(default_factory=list)


class FakeSession(BaseSession):
    def __init__(self, clock: FakeClock) -> None:
        super().__init__()
        self.clock = clock
        self.calls: list[TelegramMethod[Any]] = []
        self.shown: dict[int, Shown] = {}
        self.order: list[int] = []  # message ids in the order they first appeared
        self.answers: list[str | None] = []
        self.documents: list[tuple[Any, Any, str | None]] = []
        self._next_id = 5000

    async def close(self) -> None:  # pragma: no cover - nothing to release
        return None

    async def stream_content(
        self, *args: Any, **kwargs: Any
    ) -> AsyncGenerator[bytes, None]:  # pragma: no cover - unused
        yield b""

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: Any = None) -> Any:
        self.calls.append(method)
        if isinstance(method, SendMessage):
            self._next_id += 1
            text = str(method.text)
            self.shown[self._next_id] = Shown(self._next_id, text, method.reply_markup, [text])
            self.order.append(self._next_id)
            return self._message(method.chat_id, self._next_id, text)
        if isinstance(method, EditMessageText):
            shown = self.shown[int(method.message_id or 0)]
            shown.text, shown.markup = str(method.text), method.reply_markup
            shown.history.append(shown.text)
            return self._message(method.chat_id, shown.message_id, shown.text)
        if isinstance(method, EditMessageReplyMarkup):
            self.shown[int(method.message_id or 0)].markup = method.reply_markup
            return True
        if isinstance(method, SendDocument):
            self._next_id += 1
            doc: Any = method.document
            self.documents.append((doc.filename, doc.data, method.caption))
            return self._message(method.chat_id, self._next_id, method.caption or "")
        if isinstance(method, AnswerCallbackQuery):
            self.answers.append(method.text)
        return True

    def _message(self, chat_id: Any, message_id: int, text: str) -> Message:
        return Message(
            message_id=message_id,
            date=self.clock.now(),
            chat=Chat(id=int(chat_id), type="private"),
            text=text,
        )


class Telegram:
    """One user talking to the bot."""

    def __init__(
        self,
        uow_factory: UowFactory,
        clock: FakeClock,
        user_id: int = 1001,
        *,
        throttle: bool = False,
    ) -> None:
        self.clock = clock
        self.user_id = user_id
        self.session = FakeSession(clock)
        self.bot = Bot("123456:TEST-TOKEN-FOR-TESTS", session=self.session)
        self.wakes = 0
        self.deps = AppDeps(
            uow_factory=uow_factory,
            clock=clock,
            parser=RuleBasedParser(),
            tz=_RESOLVER,
            wake=self._wake,
        )
        self.dp: Dispatcher = build_dispatcher(self.deps, MemoryStorage(), throttle=throttle)
        self.engine = DeliveryEngine(
            uow_factory,
            TelegramNotifier(self.bot, render_delivery),
            clock,
            DispatcherConfig(chat_rate=1000.0, global_rate=1000.0),
            sleep=FakeSleeper(clock),
            rng=lambda: 0.5,
        )
        self._update_id = 10_000 * user_id
        self._message_id = 100

    def _wake(self) -> None:
        self.wakes += 1

    # --- sending ---

    def _chat(self) -> dict[str, Any]:
        return {"id": self.user_id, "type": "private"}

    def _user(self) -> dict[str, Any]:
        return {"id": self.user_id, "is_bot": False, "first_name": "Тест", "username": "tester"}

    async def _feed(self, **payload: Any) -> None:
        self._update_id += 1
        update = Update.model_validate(
            {"update_id": self._update_id, **payload}, context={"bot": self.bot}
        )
        await self.dp.feed_update(self.bot, update)

    async def replay_last_update(self) -> None:
        """Telegram delivering the very same update twice (webhook retry)."""
        update_id = self._update_id
        update = Update.model_validate(self._last_payload, context={"bot": self.bot})
        assert update.update_id == update_id
        await self.dp.feed_update(self.bot, update)

    async def send(self, text: str) -> None:
        self._message_id += 1
        message = {
            "message_id": self._message_id,
            "date": int(self.clock.now().timestamp()),
            "chat": self._chat(),
            "from": self._user(),
            "text": text,
        }
        self._last_payload = {"update_id": self._update_id + 1, "message": message}
        await self._feed(message=message)

    async def send_in_group(self, text: str) -> None:
        self._message_id += 1
        await self._feed(
            message={
                "message_id": self._message_id,
                "date": int(self.clock.now().timestamp()),
                "chat": {"id": -500, "type": "group", "title": "g"},
                "from": self._user(),
                "text": text,
            }
        )

    async def send_photo(self) -> None:
        self._message_id += 1
        await self._feed(
            message={
                "message_id": self._message_id,
                "date": int(self.clock.now().timestamp()),
                "chat": self._chat(),
                "from": self._user(),
                "photo": [{"file_id": "x", "file_unique_id": "y", "width": 1, "height": 1}],
            }
        )

    async def send_location(self, latitude: float, longitude: float) -> None:
        self._message_id += 1
        await self._feed(
            message={
                "message_id": self._message_id,
                "date": int(self.clock.now().timestamp()),
                "chat": self._chat(),
                "from": self._user(),
                "location": {"latitude": latitude, "longitude": longitude},
            }
        )

    async def press(self, data: str, *, message_id: int | None = None) -> None:
        target = message_id or self.last_with_markup().message_id
        shown = self.session.shown[target]
        await self._feed(
            callback_query={
                "id": f"cb{self._update_id}",
                "from": self._user(),
                "chat_instance": "test",
                "data": data,
                "message": {
                    "message_id": target,
                    "date": int(self.clock.now().timestamp()),
                    "chat": self._chat(),
                    "text": shown.text,
                },
            }
        )

    async def tap(self, label: str, *, exact: bool = False) -> None:
        """Press the inline button whose text contains (or equals) ``label``, newest first."""
        for shown in reversed(self.messages):
            if isinstance(shown.markup, InlineKeyboardMarkup):
                for row in shown.markup.inline_keyboard:
                    for button in row:
                        matches = button.text == label if exact else label in button.text
                        if matches and button.callback_data:
                            await self.press(button.callback_data, message_id=shown.message_id)
                            return
        raise AssertionError(f"no inline button containing {label!r}; have {self.all_buttons()}")

    # --- inspecting ---

    @property
    def messages(self) -> list[Shown]:
        return [self.session.shown[i] for i in self.session.order]

    @property
    def last(self) -> Shown:
        return self.messages[-1]

    def last_with_markup(self) -> Shown:
        for shown in reversed(self.messages):
            if isinstance(shown.markup, InlineKeyboardMarkup):
                return shown
        raise AssertionError("no message with inline buttons")

    @property
    def text(self) -> str:
        """Plain text of the newest message (HTML tags removed)."""
        return TAG.sub("", self.last.text)

    def data_of(self, label: str, shown: Shown | None = None) -> str:
        """callback_data of the inline button containing ``label`` on a message."""
        shown = shown or self.last_with_markup()
        assert isinstance(shown.markup, InlineKeyboardMarkup)
        for row in shown.markup.inline_keyboard:
            for button in row:
                if label in button.text and button.callback_data:
                    return button.callback_data
        raise AssertionError(f"no button {label!r}")

    def buttons(self, shown: Shown | None = None) -> list[str]:
        shown = shown or self.last
        if isinstance(shown.markup, InlineKeyboardMarkup):
            return [b.text for row in shown.markup.inline_keyboard for b in row]
        if isinstance(shown.markup, ReplyKeyboardMarkup):
            return [b.text for row in shown.markup.keyboard for b in row]
        return []

    def all_buttons(self) -> list[str]:
        return [b for shown in self.messages for b in self.buttons(shown)]

    def transcript(self) -> str:
        return "\n---\n".join(TAG.sub("", m.text) for m in self.messages)

    # --- the delivery engine ---

    async def deliver(self) -> int:
        """Run the delivery engine until the queue is drained; returns messages delivered."""
        before = len(self.messages)
        while await self.engine.tick():
            await asyncio.sleep(0)
        return len(self.messages) - before


def at(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


async def stored(uow_factory: UowFactory, telegram_id: int = 1001) -> list[Reminder]:
    """Every reminder of a user (any status), oldest id first."""
    async with uow_factory() as uow:
        found = await uow.users.get_by_telegram_id(telegram_id)
        assert found is not None
        views = await uow.reminders.search(
            found.id, ReminderFilter(statuses=tuple(ReminderStatus), order="created"), limit=100
        )
    return sorted((v.reminder for v in views), key=lambda r: r.id)
