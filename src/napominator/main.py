"""Composition root: wires dependencies, starts the bot, shuts down gracefully."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram import Dispatcher as AiogramDispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.utils.token import TokenValidationError
from pydantic import ValidationError

from napominator.application.dispatcher import Dispatcher, DispatcherConfig
from napominator.application.privacy import Janitor
from napominator.bot.app import COMMANDS, build_dispatcher
from napominator.bot.deps import AppDeps
from napominator.bot.texts import render_delivery
from napominator.infrastructure.clock import SystemClock
from napominator.infrastructure.config import Settings
from napominator.infrastructure.db.engine import create_engine, create_session_factory, ping
from napominator.infrastructure.db.uow import SqlAlchemyUnitOfWork
from napominator.infrastructure.heartbeat import FileHeartbeat
from napominator.infrastructure.logging import configure_logging, get_logger
from napominator.infrastructure.telegram_notifier import TelegramNotifier
from napominator.infrastructure.tzlookup import TimezoneResolver
from napominator.nlp import ParseContext, RuleBasedParser

EXIT_CONFIG_ERROR = 2
EXIT_STARTUP_ERROR = 3
_RETRY_FIRST = 1.0
_RETRY_MAX = 30.0


async def _sleep(seconds: float) -> bool:
    """Wait between connection attempts; ``True`` if SIGINT/SIGTERM asked us to stop.

    aiogram only handles signals while it is polling, so the retry wait needs its own handlers.
    """
    loop = asyncio.get_running_loop()
    stop_requested = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_requested.set)
    try:
        await asyncio.wait_for(stop_requested.wait(), seconds)
    except TimeoutError:
        return False
    else:
        return True
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


async def _poll(dp: AiogramDispatcher, bot: Bot) -> None:
    """Run long polling; survive a Telegram outage at start-up, reject a bad token clearly."""
    log = get_logger("main")
    delay = _RETRY_FIRST
    while True:
        try:
            await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
        except TelegramUnauthorizedError:
            print("Telegram rejected BOT_TOKEN. Check it in @BotFather.", file=sys.stderr)  # noqa: T201
            raise SystemExit(EXIT_CONFIG_ERROR) from None
        except TelegramNetworkError as exc:
            log.error("telegram_unreachable", error=type(exc).__name__, retry_in=delay)
            if await _sleep(delay):
                log.info("stop_requested_while_waiting_for_telegram")
                return
            delay = min(delay * 2, _RETRY_MAX)
        else:
            return


def load_settings() -> Settings:
    """Load settings or exit with a human-readable explanation."""
    try:
        return Settings()
    except ValidationError as exc:
        lines = ["Configuration error:"]
        for err in exc.errors():
            name = ".".join(str(part) for part in err["loc"]).upper() or "SETTINGS"
            lines.append(f"  - {name}: {err['msg']}")
        lines.append("Copy .env.example to .env and fill in BOT_TOKEN (get it from @BotFather).")
        print("\n".join(lines), file=sys.stderr)  # noqa: T201
        raise SystemExit(EXIT_CONFIG_ERROR) from None


async def run(settings: Settings) -> None:
    log = get_logger("main")
    engine = create_engine(settings.database_url)
    try:
        await ping(engine)
    except Exception as exc:  # any driver error must yield a clear message
        log.error("database_unavailable", error=type(exc).__name__)
        print(  # noqa: T201
            "Cannot connect to the database. Check DATABASE_URL and credentials.",
            file=sys.stderr,
        )
        await engine.dispose()
        raise SystemExit(EXIT_STARTUP_ERROR) from None

    try:
        bot = Bot(
            token=settings.bot_token.get_secret_value(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
        )
    except TokenValidationError:
        print("BOT_TOKEN has an invalid format. Re-check it in @BotFather.", file=sys.stderr)  # noqa: T201
        await engine.dispose()
        raise SystemExit(EXIT_CONFIG_ERROR) from None

    sessions = create_session_factory(engine)
    dispatcher = Dispatcher(
        lambda: SqlAlchemyUnitOfWork(sessions),
        TelegramNotifier(bot, render_delivery),
        SystemClock(),
        DispatcherConfig(
            poll_seconds=settings.dispatcher_poll_seconds,
            lease=timedelta(seconds=settings.dispatcher_lease_seconds),
            user_hourly_limit=settings.user_hourly_limit,
        ),
        heartbeat=FileHeartbeat(settings.heartbeat_path),
    )
    deps = AppDeps(
        uow_factory=lambda: SqlAlchemyUnitOfWork(sessions),
        clock=SystemClock(),
        parser=RuleBasedParser(),
        tz=TimezoneResolver(),
        wake=dispatcher.wake,
        admin_ids=frozenset(settings.admin_ids),
        llm_enabled=settings.llm_enabled,
    )
    dp = build_dispatcher(deps)
    await asyncio.to_thread(deps.parser.parse, "прогрев завтра в 9", _warmup_context())
    with contextlib.suppress(Exception):
        await bot.set_my_commands(list(COMMANDS))
    log.info("starting", mode="webhook" if settings.webhook_enabled else "polling")
    stop = asyncio.Event()
    delivery_task = asyncio.create_task(dispatcher.run(stop), name="dispatcher")
    janitor = Janitor(deps.uow_factory, deps.clock)
    janitor_task = asyncio.create_task(janitor.run(stop), name="janitor")
    try:
        await _poll(dp, bot)
    finally:
        stop.set()  # stop claiming, let in-flight sends finish (bounded by shutdown_timeout)
        with contextlib.suppress(Exception):
            await asyncio.gather(delivery_task, janitor_task)
        await bot.session.close()
        await engine.dispose()
        log.info("stopped")


def _warmup_context() -> ParseContext:
    return ParseContext(now=datetime.now(UTC), tz=ZoneInfo("UTC"))


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level, settings.log_format)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
