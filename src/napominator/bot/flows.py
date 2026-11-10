"""Shared conversation flows: parse -> clarify -> create, and applying a chosen time."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram.fsm.context import FSMContext

from napominator.application import actions, categories
from napominator.application.errors import (
    ActionExpiredError,
    InvalidScheduleError,
    LimitExceededError,
    NotFoundError,
)
from napominator.application.ports import UnitOfWork
from napominator.application.reminders import create_reminder
from napominator.bot import keyboards, texts
from napominator.bot.deps import AppDeps
from napominator.bot.formatting import describe_moment
from napominator.bot.pending import NAG_INTERVAL_MIN, NAG_MAX, Pending
from napominator.bot.pickers import calendar_markup
from napominator.bot.respond import Responder
from napominator.bot.states import Create, Pick
from napominator.domain.entities import NewReminder, User
from napominator.domain.timeutil import load_zone, local_to_utc, utc_to_local
from napominator.nlp import Anchors, Flag, ParseContext, ParseResult

PENDING_KEY = "pending"
PURPOSE_KEY = "purpose"  # create | edit_time | snooze | anchor_morning | anchor_midday | ...
TARGET_KEY = "target"

_TIME_FLAGS = {
    Flag.PAST_DATE.value,
    Flag.INVALID_DATE.value,
    Flag.NEEDS_TIME.value,
    Flag.TIME_UNCERTAIN.value,
    Flag.MULTIPLE_TIMES.value,
    Flag.RECURRENCE_TOO_FREQUENT.value,
    Flag.PAST_UNTIL.value,
}


@dataclass(slots=True)
class Ctx:
    """Everything a handler step needs, passed around as one value."""

    resp: Responder
    state: FSMContext
    deps: AppDeps
    uow: UnitOfWork
    user: User

    @property
    def now(self) -> datetime:
        return self.deps.clock.now()

    @property
    def zone(self) -> ZoneInfo:
        return load_zone(self.user.timezone or "UTC")

    @property
    def local_now(self) -> datetime:
        return utc_to_local(self.now, self.zone)

    def describe(self, local: datetime) -> str:
        return describe_moment(local, self.local_now)


async def parse_context(ctx: Ctx) -> ParseContext:
    names = tuple(c.name for c in await ctx.uow.categories.list(ctx.user.id))
    user = ctx.user
    return ParseContext(
        now=ctx.now,
        tz=ctx.zone,
        anchors=Anchors(user.morning_time, user.midday_time, user.evening_time),
        categories=names,
    )


async def parse_text(ctx: Ctx, text: str) -> ParseResult:
    return ctx.deps.parser.parse(text, await parse_context(ctx))


# --- creating ---


async def begin(ctx: Ctx, text: str) -> None:
    """A free-text message: parse it and move to the right next step."""
    await advance(ctx, Pending.from_result(await parse_text(ctx, text)))


async def advance(ctx: Ctx, pending: Pending) -> None:
    """Decide: create now, or ask exactly the one thing that is missing."""
    await ctx.state.update_data({PENDING_KEY: pending.to_dict(), PURPOSE_KEY: "create"})
    when = pending.when_local
    if pending.has(Flag.RECURRENCE_TOO_FREQUENT):
        await ctx.state.set_state(Create.clarify)
        await ctx.resp.send(texts.TOO_FREQUENT)
    elif when is None:
        await ctx.state.set_state(Create.clarify)
        await ctx.resp.send(_why_no_time(pending), keyboards.when_chips())
    elif not pending.text:
        await ctx.state.set_state(Create.text)
        await ctx.resp.send(texts.ASK_TEXT_FOR.format(when=ctx.describe(when)))
    elif not pending.confident:
        await ctx.state.set_state(Create.confirm)
        await ctx.resp.send(
            texts.guess(ctx.describe(when), pending.text), keyboards.confirm_guess()
        )
    else:
        await create_now(ctx, pending)


def _why_no_time(pending: Pending) -> str:
    for flag, message in (
        (Flag.PAST_UNTIL, texts.PAST_UNTIL),
        (Flag.PAST_DATE, texts.PAST),
        (Flag.INVALID_DATE, texts.INVALID_DATE),
        (Flag.NEEDS_TIME, texts.NEEDS_TIME),
    ):
        if pending.has(flag):
            return message
    return texts.ASK_WHEN


async def create_now(ctx: Ctx, pending: Pending) -> None:
    when = pending.when_local
    user = ctx.user
    if when is None or not pending.text or user.timezone is None:
        await advance(ctx, pending)
        return
    parsed = ParseResult(text=pending.text, when=None, category_ref=pending.category_ref)
    choice = await categories.choose_category(ctx.uow, user.id, parsed)
    new = NewReminder(
        user_id=user.id,
        text=pending.text,
        tz=user.timezone,
        dtstart_local=when,
        rrule=pending.rrule,
        category_id=choice.category.id if choice.category else None,
        lead_minutes=tuple(pending.lead_minutes),
        nag_interval_min=NAG_INTERVAL_MIN if pending.nag else None,
        nag_max=NAG_MAX if pending.nag else None,
    )
    try:
        created = await create_reminder(ctx.uow, new, ctx.now)
    except LimitExceededError:
        await ctx.state.clear()
        await ctx.resp.send(texts.LIMIT_REMINDERS)
        return
    except InvalidScheduleError as exc:
        await ctx.state.clear()
        await ctx.resp.send(texts.TEXT_TOO_LONG if "characters" in str(exc) else texts.NO_FUTURE)
        return
    if choice.explicit and choice.category is not None:
        await categories.learn(ctx.uow, user.id, pending.text, choice.category.id)
    card = texts.card(
        created.reminder,
        when=utc_to_local(created.first_occurrence, ctx.zone),
        now_local=ctx.local_now,
        category=choice.category,
        rule=pending.recurrence,
        header="✅ Запланировано",
        suggested=not choice.explicit and choice.category is not None,
    )
    await ctx.state.clear()
    await ctx.resp.send(card, keyboards.card(created.reminder))


# --- choosing a time ---


def chip_time(choice: str, user: User, now: datetime) -> datetime | None:
    """Quick-reply chips -> local wall-clock time (``None`` for an unknown chip)."""
    local = utc_to_local(now, load_zone(user.timezone or "UTC"))
    tomorrow = local.date() + timedelta(days=1)
    result: datetime | None = None
    if choice == "in1h":
        result = (local + timedelta(hours=1)).replace(second=0, microsecond=0)
    elif choice == "tmr_morning":
        result = datetime.combine(tomorrow, user.morning_time)
    elif choice == "tmr_eve":
        result = datetime.combine(tomorrow, user.evening_time)
    elif choice == "today_eve":
        tonight = datetime.combine(local.date(), user.evening_time)
        result = (
            tonight
            if tonight > local + timedelta(minutes=1)
            else datetime.combine(tomorrow, user.evening_time)
        )
    return result


async def apply_time(ctx: Ctx, when: datetime, rrule: str | None = None) -> None:
    """A time was chosen (chip / typed / picker): continue whatever flow asked for it."""
    data = await ctx.state.get_data()
    purpose = data.get(PURPOSE_KEY, "create")
    if purpose == "edit_time":
        await _apply_edit_time(ctx, data[TARGET_KEY], when, rrule)
    elif purpose == "snooze":
        await _apply_snooze(ctx, data[TARGET_KEY], when)
    else:
        pending = Pending.from_dict(data[PENDING_KEY])
        pending.when = when.isoformat()
        pending.rrule = rrule or pending.rrule
        pending.flags = [f for f in pending.flags if f not in _TIME_FLAGS]
        pending.confident = True
        await advance(ctx, pending)


async def _apply_edit_time(ctx: Ctx, reminder_id: int, when: datetime, rrule: str | None) -> None:
    user = ctx.user
    reminder = await ctx.uow.reminders.get(reminder_id, user.id)
    if reminder is None:
        await ctx.state.clear()
        await ctx.resp.send(texts.GONE)
        return
    try:
        result = await actions.reschedule(
            ctx.uow,
            user.id,
            reminder_id,
            dtstart_local=when,
            rrule=rrule or reminder.rrule,
            now=ctx.now,
        )
    except InvalidScheduleError:
        await ctx.resp.send(texts.PAST)
        return
    await ctx.state.clear()
    category = (
        await ctx.uow.categories.get(result.reminder.category_id, user.id)
        if result.reminder.category_id
        else None
    )
    card = texts.card(
        result.reminder,
        when=utc_to_local(result.first_occurrence, ctx.zone),
        now_local=ctx.local_now,
        category=category,
        rule=result.reminder.schedule.recurrence,
    )
    await ctx.resp.send(f"{texts.TIME_UPDATED}\n\n{card}", keyboards.card(result.reminder))


async def _apply_snooze(ctx: Ctx, notification_id: int, when: datetime) -> None:
    until = local_to_utc(when, ctx.zone)
    try:
        await actions.snooze(ctx.uow, ctx.user.id, notification_id, until, ctx.now)
    except (InvalidScheduleError, ActionExpiredError, NotFoundError):
        await ctx.resp.send(texts.PAST)
        return
    await ctx.state.clear()
    await ctx.resp.send(
        f"⏰ Отложено до <b>{ctx.describe(when)}</b>", keyboards.undo(notification_id)
    )


def typed_time(result: ParseResult) -> tuple[datetime, str | None] | None:
    """A reply to "when?": usable only if it produced a concrete time."""
    if result.when is None or Flag.PAST_DATE in result.flags:
        return None
    return result.when, result.recurrence.to_rrule() if result.recurrence else None


async def start_picker(ctx: Ctx, purpose: str, target: int = 0) -> None:
    """Open the calendar (step one of choosing a date and time by hand)."""
    local = ctx.local_now
    await ctx.state.update_data({PURPOSE_KEY: purpose, TARGET_KEY: target})
    await ctx.state.set_state(Pick.date)
    await ctx.resp.send(texts.PICK_DATE, calendar_markup(local.year, local.month, local.date()))
