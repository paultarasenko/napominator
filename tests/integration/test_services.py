"""Application services (categories, users, actions, queries) on every dialect."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest

from napominator.application import actions, categories, queries, users
from napominator.application.dispatcher import Dispatcher
from napominator.application.errors import (
    ActionExpiredError,
    DuplicateCategoryError,
    InvalidScheduleError,
    LimitExceededError,
    NotFoundError,
)
from napominator.application.reminders import CreatedReminder, create_reminder
from napominator.domain.categories import InvalidCategoryNameError
from napominator.domain.entities import (
    MAX_CATEGORIES,
    NewReminder,
    Notification,
    NotificationKind,
    NotificationStatus,
    Reminder,
    ReminderFilter,
    ReminderStatus,
    User,
)
from napominator.nlp import ParseContext, RuleBasedParser
from tests.support import (
    T0,
    FakeClock,
    FakeNotifier,
    FakeSleeper,
    UowFactory,
    add_user,
)

MOSCOW = "Europe/Moscow"
_MESSAGE_IDS = itertools.count(1000, 1000)  # real Telegram message ids never repeat
PARSER = RuleBasedParser()


def at(**delta: float) -> datetime:
    return T0 + timedelta(**delta)


async def moscow_user(uow_factory: UowFactory, telegram_id: int = 1001) -> User:
    user = await add_user(uow_factory, telegram_id)
    async with uow_factory() as uow:
        await users.complete_onboarding(uow, user.id, MOSCOW, T0)
        refreshed = await uow.users.get(user.id)
    assert refreshed is not None
    return refreshed


async def make(
    uow_factory: UowFactory,
    user: User,
    local: datetime,
    *,
    rrule: str | None = None,
    text: str = "Позвонить маме",
    category_id: int | None = None,
) -> CreatedReminder:
    async with uow_factory() as uow:
        return await create_reminder(
            uow,
            NewReminder(user.id, text, MOSCOW, local, rrule=rrule, category_id=category_id),
            T0,
        )


async def deliver_all(uow_factory: UowFactory, clock: FakeClock) -> FakeNotifier:
    notifier = FakeNotifier(clock, id_start=next(_MESSAGE_IDS))
    dispatcher = Dispatcher(uow_factory, notifier, clock, sleep=FakeSleeper(clock), rng=lambda: 0.5)
    while await dispatcher.tick():
        pass
    return notifier


# --- onboarding / registration ---


async def test_onboarding_seeds_default_categories_exactly_once(uow_factory: UowFactory) -> None:
    user = await add_user(uow_factory)
    async with uow_factory() as uow:
        assert await users.complete_onboarding(uow, user.id, MOSCOW, T0) is True
        assert await users.complete_onboarding(uow, user.id, "Asia/Omsk", at(hours=1)) is False
        cats = await uow.categories.list(user.id)
        stored = await uow.users.get(user.id)
    assert [c.name for c in cats] == ["Дом", "Работа", "Здоровье", "Финансы", "Покупки", "Даты"]
    assert stored is not None
    assert (stored.timezone, stored.onboarded_at) == ("Asia/Omsk", T0)  # first time is kept


async def test_onboarding_rejects_an_unknown_zone(uow_factory: UowFactory) -> None:
    user = await add_user(uow_factory)
    with pytest.raises(InvalidScheduleError):
        async with uow_factory() as uow:
            await users.complete_onboarding(uow, user.id, "Mars/Olympus", T0)


async def test_register_creates_then_refreshes(uow_factory: UowFactory) -> None:
    async with uow_factory() as uow:
        first = await users.register(uow, 77, "old", "Old", T0)
    async with uow_factory() as uow:
        second = await users.register(uow, 77, "new", "New", at(hours=1))
    assert (first.created, second.created, second.returned) == (True, False, False)
    assert (second.user.username, second.user.first_name) == ("new", "New")
    assert second.user.last_seen_at == at(hours=1)
    assert first.user.id == second.user.id


async def test_a_user_who_blocked_the_bot_and_returns_gets_their_series_back(
    uow_factory: UowFactory,
) -> None:
    user = await moscow_user(uow_factory)
    created = await make(
        uow_factory, user, datetime(2026, 10, 19, 9, 0), rrule="FREQ=WEEKLY;BYDAY=MO"
    )
    async with uow_factory() as uow:
        await uow.users.set_active(user.id, active=False)
        await uow.notifications.skip_open_for_user(user.id, "recipient_blocked")
    async with uow_factory() as uow:
        back = await users.register(uow, user.telegram_id, None, None, at(hours=2))
        notes = await uow.notifications.list_for_reminder(created.reminder.id)
    assert (back.returned, back.resumed_series, back.user.is_active) == (True, 1, True)
    assert [n.status for n in notes] == [NotificationStatus.PENDING]


async def test_anchors_must_be_ordered(uow_factory: UowFactory) -> None:
    user = await moscow_user(uow_factory)
    async with uow_factory() as uow:
        await users.set_anchors(uow, user.id, morning=time(8), midday=time(12), evening=time(20))
        stored = await uow.users.get(user.id)
    assert stored is not None
    assert (stored.morning_time, stored.evening_time) == (time(8), time(20))
    with pytest.raises(InvalidScheduleError):
        async with uow_factory() as uow:
            await users.set_anchors(
                uow, user.id, morning=time(20), midday=time(12), evening=time(8)
            )


async def test_user_update_rejects_unknown_fields(uow_factory: UowFactory) -> None:
    user = await add_user(uow_factory)
    with pytest.raises(ValueError, match="not updatable"):
        async with uow_factory() as uow:
            await uow.users.update(user.id, is_active=False)  # must go through set_active


# --- categories ---


async def test_create_category_uses_given_or_guessed_emoji(uow_factory: UowFactory) -> None:
    user = await moscow_user(uow_factory)
    async with uow_factory() as uow:
        sport = await categories.create_category(uow, user.id, "Спорт")
        custom = await categories.create_category(uow, user.id, "🎸 Музыка")
        plain = await categories.create_category(uow, user.id, "Разное")
    assert (sport.emoji, custom.emoji, plain.emoji) == ("🏃", "🎸", "🗂")
    assert custom.name == "Музыка"


async def test_duplicate_names_are_rejected_case_insensitively_in_cyrillic(
    uow_factory: UowFactory,
) -> None:
    user = await moscow_user(uow_factory)
    for clash in ("работа", "РАБОТА", "  Работа  "):
        with pytest.raises(DuplicateCategoryError):
            async with uow_factory() as uow:
                await categories.create_category(uow, user.id, clash)
    other = await moscow_user(uow_factory, 2002)
    async with uow_factory() as uow:  # the same name is fine for another user
        assert (await categories.create_category(uow, other.id, "Хобби")).name == "Хобби"


@pytest.mark.parametrize("bad", ["", "   ", "🙂", "x" * 33])
async def test_invalid_category_names(uow_factory: UowFactory, bad: str) -> None:
    user = await moscow_user(uow_factory)
    with pytest.raises(InvalidCategoryNameError):
        async with uow_factory() as uow:
            await categories.create_category(uow, user.id, bad)


async def test_category_limit(uow_factory: UowFactory) -> None:
    user = await moscow_user(uow_factory)
    async with uow_factory() as uow:
        for i in range(MAX_CATEGORIES - 6):
            await categories.create_category(uow, user.id, f"Тема {i}")
    with pytest.raises(LimitExceededError):
        async with uow_factory() as uow:
            await categories.create_category(uow, user.id, "Лишняя")


async def test_rename_and_emoji(uow_factory: UowFactory) -> None:
    user = await moscow_user(uow_factory)
    async with uow_factory() as uow:
        work = next(c for c in await uow.categories.list(user.id) if c.name == "Работа")
        renamed = await categories.rename_category(uow, user.id, work.id, "Офис")
        assert (renamed.name, renamed.emoji) == ("Офис", "💼")  # emoji kept when not given
        with_emoji = await categories.rename_category(uow, user.id, work.id, "🏢 Офис")
        assert with_emoji.emoji == "🏢"
        await categories.set_category_emoji(uow, user.id, work.id, "🏭")
    async with uow_factory() as uow:
        assert (await uow.categories.get(work.id, user.id)).emoji == "🏭"  # type: ignore[union-attr]
    with pytest.raises(DuplicateCategoryError):
        async with uow_factory() as uow:
            await categories.rename_category(uow, user.id, work.id, "дом")
    with pytest.raises(NotFoundError):
        async with uow_factory() as uow:
            await categories.rename_category(uow, user.id, 99999, "Что-то")


async def test_categories_are_isolated_between_users(uow_factory: UowFactory) -> None:
    alice = await moscow_user(uow_factory, 1)
    bob = await moscow_user(uow_factory, 2)
    async with uow_factory() as uow:
        bobs = (await uow.categories.list(bob.id))[0]
    with pytest.raises(NotFoundError):
        async with uow_factory() as uow:
            await categories.rename_category(uow, alice.id, bobs.id, "Мой")
    async with uow_factory() as uow:
        assert await uow.categories.get(bobs.id, alice.id) is None
        assert not await uow.categories.delete(bobs.id, alice.id)


async def test_deleting_a_category_moves_or_unassigns_reminders_never_deletes_them(
    uow_factory: UowFactory,
) -> None:
    user = await moscow_user(uow_factory)
    async with uow_factory() as uow:
        cats = {c.name: c for c in await uow.categories.list(user.id)}
    a = await make(uow_factory, user, datetime(2026, 10, 14, 10, 0), category_id=cats["Работа"].id)
    b = await make(uow_factory, user, datetime(2026, 10, 14, 11, 0), category_id=cats["Дом"].id)
    async with uow_factory() as uow:
        assert (
            await categories.delete_category(
                uow, user.id, cats["Работа"].id, move_to=cats["Дом"].id
            )
            == 1
        )
        assert await categories.delete_category(uow, user.id, cats["Дом"].id, move_to=None) == 2
    async with uow_factory() as uow:
        first = await uow.reminders.get(a.reminder.id, user.id)
        second = await uow.reminders.get(b.reminder.id, user.id)
    assert first is not None
    assert second is not None
    assert (first.status, first.category_id) == (ReminderStatus.ACTIVE, None)
    assert (second.status, second.category_id) == (ReminderStatus.ACTIVE, None)
    with pytest.raises(NotFoundError):  # moving into the category being deleted makes no sense
        async with uow_factory() as uow:
            await categories.delete_category(
                uow, user.id, cats["Финансы"].id, move_to=cats["Финансы"].id
            )


async def test_category_order_can_be_changed(uow_factory: UowFactory) -> None:
    user = await moscow_user(uow_factory)
    async with uow_factory() as uow:
        names = [c.name for c in await uow.categories.list(user.id)]
        second = (await uow.categories.list(user.id))[1]
        assert await uow.categories.swap(user.id, second.id, -1)
        assert not await uow.categories.swap(
            user.id, (await uow.categories.list(user.id))[0].id, -1
        )
        last = (await uow.categories.list(user.id))[-1]
        assert not await uow.categories.swap(user.id, last.id, +1)
        reordered = [c.name for c in await uow.categories.list(user.id)]
    assert reordered[:2] == [names[1], names[0]]
    assert reordered[2:] == names[2:]


async def parse(text: str, categories_: tuple[str, ...] = ()):  # type: ignore[no-untyped-def]
    ctx = ParseContext(now=T0, tz=__import__("zoneinfo").ZoneInfo(MOSCOW), categories=categories_)
    return PARSER.parse(text, ctx)


async def test_explicit_tag_picks_or_creates_a_category(uow_factory: UowFactory) -> None:
    user = await moscow_user(uow_factory)
    async with uow_factory() as uow:
        existing = await categories.choose_category(
            uow, user.id, await parse("завтра в 10:00 планерка #рабта", ("Работа",))
        )
        created = await categories.choose_category(
            uow, user.id, await parse("завтра в 10:00 полив #цветы", ())
        )
    assert existing.explicit
    assert existing.category is not None
    assert existing.category.name == "Работа"
    assert created.explicit
    assert created.category is not None
    assert created.category.name == "цветы"


async def test_habits_beat_the_dictionary_and_the_dictionary_beats_nothing(
    uow_factory: UowFactory,
) -> None:
    user = await moscow_user(uow_factory)
    async with uow_factory() as uow:
        cats = {c.name: c for c in await uow.categories.list(user.id)}
        by_dictionary = await categories.choose_category(
            uow, user.id, await parse("завтра в 10:00 врач")
        )
        nothing = await categories.choose_category(
            uow, user.id, await parse("завтра в 10:00 привет")
        )
        await categories.learn(uow, user.id, "Врач", cats["Работа"].id)
        by_habit = await categories.choose_category(
            uow, user.id, await parse("завтра в 10:00 врач")
        )
        await categories.learn(uow, user.id, "Врач", cats["Дом"].id)  # a newer habit replaces it
        replaced = await categories.choose_category(
            uow, user.id, await parse("завтра в 10:00 врач")
        )
    assert by_dictionary.category is not None
    assert (by_dictionary.category.name, by_dictionary.explicit) == ("Здоровье", False)
    assert nothing.category is None
    assert by_habit.category is not None
    assert by_habit.category.name == "Работа"
    assert replaced.category is not None
    assert replaced.category.name == "Дом"


async def test_processed_updates_register_once_and_can_be_purged(uow_factory: UowFactory) -> None:
    async with uow_factory() as uow:
        assert await uow.processed_updates.register(555, T0) is True
        assert await uow.processed_updates.register(555, at(minutes=1)) is False
        assert await uow.processed_updates.register(556, at(days=2)) is True
    async with uow_factory() as uow:
        assert await uow.processed_updates.purge_before(at(days=1)) == 1
        assert await uow.processed_updates.register(555, at(days=3)) is True  # forgotten


# --- queries ---


async def test_search_orders_by_next_occurrence_and_filters(uow_factory: UowFactory) -> None:
    user = await moscow_user(uow_factory)
    cats = {c.name: c for c in await _cats(uow_factory, user)}
    late = await make(
        uow_factory, user, datetime(2026, 10, 20, 9, 0), text="поздно", category_id=cats["Дом"].id
    )
    soon = await make(uow_factory, user, datetime(2026, 10, 13, 18, 0), text="скоро")
    weekly = await make(
        uow_factory,
        user,
        datetime(2026, 10, 19, 9, 0),
        rrule="FREQ=WEEKLY;BYDAY=MO",
        text="еженедельно",
    )
    async with uow_factory() as uow:
        everything = await uow.reminders.search(user.id, ReminderFilter(), limit=10)
        recurring = await uow.reminders.search(user.id, ReminderFilter(recurring=True), limit=10)
        one_offs = await uow.reminders.count(user.id, ReminderFilter(recurring=False))
        home = await uow.reminders.search(
            user.id, ReminderFilter(category_id=cats["Дом"].id), limit=10
        )
        none = await uow.reminders.search(user.id, ReminderFilter(uncategorized=True), limit=10)
        window = await uow.reminders.search(
            user.id, ReminderFilter(next_from=at(hours=1), next_to=at(days=2)), limit=10
        )
        paged = await uow.reminders.search(user.id, ReminderFilter(), limit=2, offset=2)
    assert [v.reminder.text for v in everything] == ["скоро", "еженедельно", "поздно"]
    assert everything[0].next_at == datetime(2026, 10, 13, 15, 0, tzinfo=UTC)
    assert [v.reminder.id for v in recurring] == [weekly.reminder.id]
    assert one_offs == 2
    assert [v.reminder.id for v in home] == [late.reminder.id]
    assert len(none) == 2
    assert [v.reminder.id for v in window] == [soon.reminder.id]
    assert [v.reminder.text for v in paged] == ["поздно"]


async def _cats(uow_factory: UowFactory, user: User):  # type: ignore[no-untyped-def]
    async with uow_factory() as uow:
        return await uow.categories.list(user.id)


async def test_search_is_scoped_to_the_user_and_hides_deleted(uow_factory: UowFactory) -> None:
    alice = await moscow_user(uow_factory, 1)
    bob = await moscow_user(uow_factory, 2)
    mine = await make(uow_factory, alice, datetime(2026, 10, 14, 10, 0))
    await make(uow_factory, bob, datetime(2026, 10, 14, 10, 0))
    gone = await make(uow_factory, alice, datetime(2026, 10, 14, 11, 0))
    async with uow_factory() as uow:
        await actions.delete_reminder(uow, alice.id, gone.reminder.id, T0)
        found = await uow.reminders.search(alice.id, ReminderFilter(), limit=10)
        everything = await uow.reminders.search(
            alice.id,
            ReminderFilter(statuses=tuple(ReminderStatus)),
            limit=10,
        )
    assert [v.reminder.id for v in found] == [mine.reminder.id]
    assert len(everything) == 2  # deletion is soft


async def test_reminder_update_only_touches_whitelisted_fields(uow_factory: UowFactory) -> None:
    user = await moscow_user(uow_factory)
    created = await make(uow_factory, user, datetime(2026, 10, 14, 10, 0))
    with pytest.raises(ValueError, match="not updatable"):
        async with uow_factory() as uow:
            await uow.reminders.update(created.reminder.id, user.id, T0, status="deleted")
    other = await moscow_user(uow_factory, 9)
    async with uow_factory() as uow:  # somebody else's id is simply "not found"
        assert not await uow.reminders.update(created.reminder.id, other.id, T0, text="hack")


async def test_today_view_lists_the_rest_of_the_local_day_and_unacknowledged_items(
    uow_factory: UowFactory, clock: FakeClock
) -> None:
    user = await moscow_user(uow_factory)
    await make(uow_factory, user, datetime(2026, 10, 13, 18, 0), text="вечером")
    await make(uow_factory, user, datetime(2026, 10, 13, 16, 0), text="днем")
    await make(uow_factory, user, datetime(2026, 10, 14, 9, 0), text="завтра")
    await make(uow_factory, user, datetime(2026, 10, 13, 15, 30), text="ждёт отметки")
    clock.set(at(minutes=45))  # 15:45 Moscow: the 15:30 one fires, the rest stay queued
    await deliver_all(uow_factory, clock)
    async with uow_factory() as uow:
        view = await queries.today(uow, user, clock.now())
    assert [v.reminder.text for v in view.upcoming] == ["днем", "вечером"]
    assert [a.reminder.text for a in view.awaiting] == ["ждёт отметки"]
    assert view.local_date.isoformat() == "2026-10-13"


def test_local_day_bounds_follow_the_users_zone(uow_factory: UowFactory) -> None:
    user = User(
        1,
        1,
        None,
        None,
        "Asia/Vladivostok",
        time(9),
        time(13),
        time(19),
        False,
        time(8),
        True,
        None,
        T0,
        None,
    )
    start, end = queries.local_day_bounds(user, datetime(2026, 10, 13, 20, 0, tzinfo=UTC))
    assert start == datetime(2026, 10, 13, 14, 0, tzinfo=UTC)  # already 14 Oct 06:00 locally
    assert end - start == timedelta(hours=24)


async def test_pagination_clamps_out_of_range_pages(uow_factory: UowFactory) -> None:
    user = await moscow_user(uow_factory)
    for i in range(queries.PAGE_SIZE + 3):
        await make(uow_factory, user, datetime(2026, 10, 14, 10, i), text=f"r{i}")
    async with uow_factory() as uow:
        first = await queries.page_of(uow, user.id, queries.LIST_FILTERS["soon"], 0)
        beyond = await queries.page_of(uow, user.id, queries.LIST_FILTERS["soon"], 99)
        before = await queries.page_of(uow, user.id, queries.LIST_FILTERS["soon"], -5)
    assert (first.total, first.pages, len(first.items)) == (
        queries.PAGE_SIZE + 3,
        2,
        queries.PAGE_SIZE,
    )
    assert (beyond.page, len(beyond.items)) == (1, 3)
    assert before.page == 0


# --- reactions to a delivered notification ---


async def delivered(
    uow_factory: UowFactory, clock: FakeClock, user: User, **kw: Any
) -> tuple[Reminder, Notification]:
    """Create a one-off for 15:05 Moscow, let time pass, deliver it, return the sent row."""
    created = await make(uow_factory, user, kw.pop("local", datetime(2026, 10, 13, 15, 5)), **kw)
    clock.set(max(clock.now(), at(minutes=6)))
    await deliver_all(uow_factory, clock)
    async with uow_factory() as uow:
        notes = await uow.notifications.list_for_reminder(created.reminder.id)
    return created.reminder, next(n for n in notes if n.status is NotificationStatus.SENT)


async def test_done_is_idempotent_owner_checked_and_hides_the_item(
    uow_factory: UowFactory, clock: FakeClock
) -> None:
    user = await moscow_user(uow_factory)
    stranger = await moscow_user(uow_factory, 2)
    clock.advance(minutes=1)
    _, note = await delivered(uow_factory, clock, user)
    with pytest.raises(NotFoundError):
        async with uow_factory() as uow:
            await actions.mark_done(uow, stranger.id, note.id, clock.now())
    for _ in range(2):  # a second press changes nothing and does not fail
        async with uow_factory() as uow:
            await actions.mark_done(uow, user.id, note.id, clock.now())
    async with uow_factory() as uow:
        stored = await uow.notifications.get(note.id)
        view = await queries.today(uow, user, clock.now())
    assert stored is not None
    assert (stored.status, stored.acked_at) == (NotificationStatus.DONE, clock.now())
    assert view.awaiting == []


async def test_snooze_requeues_a_closed_one_off_and_delivers_it_again(
    uow_factory: UowFactory, clock: FakeClock
) -> None:
    user = await moscow_user(uow_factory)
    clock.advance(minutes=1)
    reminder, note = await delivered(uow_factory, clock, user)
    until = clock.now() + timedelta(minutes=15)
    async with uow_factory() as uow:
        assert await actions.snooze(uow, user.id, note.id, until, clock.now()) == until
    async with uow_factory() as uow:
        stored = await uow.reminders.get(reminder.id, user.id)
        notes = await uow.notifications.list_for_reminder(reminder.id)
    assert stored is not None
    assert stored.status is ReminderStatus.ACTIVE
    assert [(n.kind, n.status) for n in notes] == [
        (NotificationKind.DUE, NotificationStatus.SNOOZED),
        (NotificationKind.SNOOZE, NotificationStatus.PENDING),
    ]
    with pytest.raises(ActionExpiredError):  # the same button twice
        async with uow_factory() as uow:
            await actions.snooze(uow, user.id, note.id, until, clock.now())
    clock.set(until)
    notifier = await deliver_all(uow_factory, clock)
    assert [d.kind for d in notifier.sent] == [NotificationKind.SNOOZE]
    async with uow_factory() as uow:
        again = await uow.reminders.get(reminder.id, user.id)
        awaiting = (await queries.today(uow, user, clock.now())).awaiting
    assert again is not None
    assert again.status is ReminderStatus.DONE  # closed once more after the repeat
    assert [a.notification.kind for a in awaiting] == [NotificationKind.SNOOZE]


async def test_snooze_into_the_past_is_rejected(uow_factory: UowFactory, clock: FakeClock) -> None:
    user = await moscow_user(uow_factory)
    clock.advance(minutes=1)
    _, note = await delivered(uow_factory, clock, user)
    with pytest.raises(InvalidScheduleError):
        async with uow_factory() as uow:
            await actions.snooze(uow, user.id, note.id, clock.now(), clock.now())


async def test_undo_restores_the_awaiting_state_within_the_window(
    uow_factory: UowFactory, clock: FakeClock
) -> None:
    user = await moscow_user(uow_factory)
    clock.advance(minutes=1)
    reminder, note = await delivered(uow_factory, clock, user)
    async with uow_factory() as uow:
        await actions.mark_done(uow, user.id, note.id, clock.now())
        await actions.undo_reaction(uow, user.id, note.id, clock.now() + timedelta(minutes=5))
    async with uow_factory() as uow:
        stored = await uow.notifications.get(note.id)
    assert stored is not None
    assert (stored.status, stored.acked_at) == (NotificationStatus.SENT, None)

    async with uow_factory() as uow:
        await actions.snooze(uow, user.id, note.id, clock.now() + timedelta(hours=1), clock.now())
        await actions.undo_reaction(uow, user.id, note.id, clock.now() + timedelta(minutes=1))
    async with uow_factory() as uow:
        notes = await uow.notifications.list_for_reminder(reminder.id)
    assert [n.status for n in notes] == [NotificationStatus.SENT, NotificationStatus.SKIPPED]


async def test_undo_expires(uow_factory: UowFactory, clock: FakeClock) -> None:
    user = await moscow_user(uow_factory)
    clock.advance(minutes=1)
    _, note = await delivered(uow_factory, clock, user)
    async with uow_factory() as uow:
        await actions.mark_done(uow, user.id, note.id, clock.now())
    with pytest.raises(ActionExpiredError):
        async with uow_factory() as uow:
            await actions.undo_reaction(
                uow, user.id, note.id, clock.now() + actions.UNDO_WINDOW + timedelta(seconds=1)
            )
    other_reminder, never_acked = await delivered(uow_factory, clock, user, text="второе")
    assert other_reminder.id != note.id
    with pytest.raises(ActionExpiredError):  # never acknowledged: nothing to undo
        async with uow_factory() as uow:
            await actions.undo_reaction(uow, user.id, never_acked.id, clock.now())


def test_snooze_targets() -> None:
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)  # 15:00 Moscow
    assert actions.snooze_target(now, MOSCOW, choice="15m") == now + timedelta(minutes=15)
    assert actions.snooze_target(now, MOSCOW, choice="1h") == now + timedelta(hours=1)
    assert actions.snooze_target(now, MOSCOW, choice="3h") == now + timedelta(hours=3)
    assert actions.snooze_target(now, MOSCOW, choice="tomorrow") == datetime(
        2026, 10, 14, 6, 0, tzinfo=UTC
    )
    evening = datetime(2026, 10, 13, 16, 0, tzinfo=UTC)
    assert actions.snooze_target(now, MOSCOW, choice="evening", evening=evening) == evening
    with pytest.raises(InvalidScheduleError):
        actions.snooze_target(now, MOSCOW, choice="whenever")


# --- lifecycle ---


async def test_delete_skips_queued_notifications_and_restore_brings_them_back(
    uow_factory: UowFactory,
) -> None:
    user = await moscow_user(uow_factory)
    series = await make(uow_factory, user, datetime(2026, 10, 14, 9, 0), rrule="FREQ=DAILY")
    one_off = await make(uow_factory, user, datetime(2026, 10, 14, 10, 0))
    async with uow_factory() as uow:
        for created in (series, one_off):
            await actions.delete_reminder(uow, user.id, created.reminder.id, T0)
            await actions.delete_reminder(uow, user.id, created.reminder.id, T0)  # idempotent
    async with uow_factory() as uow:
        assert {
            n.status for n in await uow.notifications.list_for_reminder(series.reminder.id)
        } == {NotificationStatus.SKIPPED}
        for created in (series, one_off):
            restored = await actions.restore_reminder(
                uow, user.id, created.reminder.id, at(minutes=5)
            )
            assert restored.status is ReminderStatus.ACTIVE
        pending = [
            n
            for n in await uow.notifications.list_for_reminder(one_off.reminder.id)
            if n.status is NotificationStatus.PENDING
        ]
    assert [n.fire_at for n in pending] == [one_off.first_occurrence]


async def test_restoring_a_one_off_whose_time_has_passed_marks_it_done(
    uow_factory: UowFactory,
) -> None:
    user = await moscow_user(uow_factory)
    created = await make(uow_factory, user, datetime(2026, 10, 13, 16, 0))
    async with uow_factory() as uow:
        await actions.delete_reminder(uow, user.id, created.reminder.id, T0)
        restored = await actions.restore_reminder(uow, user.id, created.reminder.id, at(days=1))
    assert restored.status is ReminderStatus.DONE


async def test_pause_and_resume(uow_factory: UowFactory) -> None:
    user = await moscow_user(uow_factory)
    series = await make(uow_factory, user, datetime(2026, 10, 14, 9, 0), rrule="FREQ=DAILY")
    rid = series.reminder.id
    async with uow_factory() as uow:
        await actions.pause_reminder(uow, user.id, rid, T0)
    with pytest.raises(ActionExpiredError):
        async with uow_factory() as uow:
            await actions.pause_reminder(uow, user.id, rid, T0)
    async with uow_factory() as uow:
        assert not await uow.notifications.has_open_for_reminder(rid)
        await actions.resume_reminder(uow, user.id, rid, at(days=3))
        notes = await uow.notifications.list_for_reminder(rid)
    assert [n.status for n in notes] == [NotificationStatus.SKIPPED, NotificationStatus.PENDING]
    assert notes[-1].fire_at == datetime(
        2026, 10, 17, 6, 0, tzinfo=UTC
    )  # next 09:00 after the pause
    with pytest.raises(ActionExpiredError):
        async with uow_factory() as uow:
            await actions.resume_reminder(uow, user.id, rid, at(days=3))


async def test_edit_text_and_category(uow_factory: UowFactory) -> None:
    user = await moscow_user(uow_factory)
    created = await make(uow_factory, user, datetime(2026, 10, 14, 9, 0))
    cats = await _cats(uow_factory, user)
    async with uow_factory() as uow:
        await actions.edit_text(uow, user.id, created.reminder.id, "Новый текст", at(minutes=1))
        await actions.set_reminder_category(
            uow, user.id, created.reminder.id, cats[0].id, at(minutes=1)
        )
        stored = await uow.reminders.get(created.reminder.id, user.id)
    assert stored is not None
    assert (stored.text, stored.category_id) == ("Новый текст", cats[0].id)
    for bad in ("", "x" * 1001):
        with pytest.raises(InvalidScheduleError):
            async with uow_factory() as uow:
                await actions.edit_text(uow, user.id, created.reminder.id, bad, at(minutes=1))
    with pytest.raises(NotFoundError):
        async with uow_factory() as uow:
            await actions.set_reminder_category(
                uow, user.id, created.reminder.id, 99999, at(minutes=1)
            )


async def test_reschedule_replaces_the_queue(uow_factory: UowFactory) -> None:
    user = await moscow_user(uow_factory)
    created = await make(uow_factory, user, datetime(2026, 10, 14, 9, 0))
    async with uow_factory() as uow:
        result = await actions.reschedule(
            uow,
            user.id,
            created.reminder.id,
            dtstart_local=datetime(2026, 10, 15, 18, 30),
            rrule="FREQ=DAILY",
            now=at(minutes=1),
        )
        notes = await uow.notifications.list_for_reminder(created.reminder.id)
    assert result.first_occurrence == datetime(2026, 10, 15, 15, 30, tzinfo=UTC)
    assert result.reminder.rrule == "FREQ=DAILY"
    assert [(n.status, n.fire_at) for n in notes] == [
        (NotificationStatus.SKIPPED, created.first_occurrence),
        (NotificationStatus.PENDING, result.first_occurrence),
    ]
    with pytest.raises(InvalidScheduleError):
        async with uow_factory() as uow:
            await actions.reschedule(
                uow,
                user.id,
                created.reminder.id,
                dtstart_local=datetime(2026, 1, 1, 9),
                rrule=None,
                now=T0,
            )
    with pytest.raises(InvalidScheduleError):
        async with uow_factory() as uow:
            await actions.reschedule(
                uow,
                user.id,
                created.reminder.id,
                dtstart_local=datetime(2026, 12, 1, 9),
                rrule="FREQ=NOPE",
                now=T0,
            )


@pytest.mark.parametrize(
    ("keep_local", "expected_fire"),
    [
        (
            True,
            datetime(2026, 10, 14, 4, 0, tzinfo=UTC),
        ),  # 09:00 on the Omsk wall clock (UTC+6)... see below
        (False, datetime(2026, 10, 14, 6, 0, tzinfo=UTC)),  # the very same instant as before
    ],
)
async def test_changing_the_zone_replans_every_live_reminder(
    uow_factory: UowFactory, keep_local: bool, expected_fire: datetime
) -> None:
    user = await moscow_user(uow_factory)
    # 09:00 Moscow tomorrow == 06:00Z
    created = await make(uow_factory, user, datetime(2026, 10, 14, 9, 0))
    series = await make(uow_factory, user, datetime(2026, 10, 14, 9, 0), rrule="FREQ=DAILY")
    deleted = await make(uow_factory, user, datetime(2026, 10, 14, 12, 0))
    async with uow_factory() as uow:
        await actions.delete_reminder(uow, user.id, deleted.reminder.id, T0)
        replanned = await actions.change_timezone(
            uow, user, "Asia/Omsk", keep_local_time=keep_local, now=at(minutes=1)
        )
        stored = await uow.users.get(user.id)
        moved = await uow.reminders.get(created.reminder.id, user.id)
        fires = [
            n.fire_at
            for n in await uow.notifications.list_for_reminder(created.reminder.id)
            if n.status is NotificationStatus.PENDING
        ]
        series_fires = [
            n.fire_at
            for n in await uow.notifications.list_for_reminder(series.reminder.id)
            if n.status is NotificationStatus.PENDING
        ]
    assert replanned == 2  # the deleted one is left alone
    assert stored is not None
    assert stored.timezone == "Asia/Omsk"
    assert moved is not None
    assert moved.tz == "Asia/Omsk"
    # Omsk is UTC+6: 09:00 local == 03:00Z; "same instant" keeps 06:00Z (== 12:00 local)
    expected = (
        datetime(2026, 10, 14, 3, 0, tzinfo=UTC)
        if keep_local
        else datetime(2026, 10, 14, 6, 0, tzinfo=UTC)
    )
    assert fires == [expected]
    assert series_fires == [expected]
    assert expected_fire.tzinfo is UTC


async def test_changing_to_an_unknown_zone_fails(uow_factory: UowFactory) -> None:
    user = await moscow_user(uow_factory)
    with pytest.raises(ValueError, match="unknown timezone"):
        async with uow_factory() as uow:
            await actions.change_timezone(uow, user, "Mars/Olympus", keep_local_time=True, now=T0)
