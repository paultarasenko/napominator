"""Parser behaviour that depends on 'now', the zone and the user's anchors."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from napominator.domain.timeutil import local_to_utc
from napominator.nlp import Anchors, Flag, ParseContext, RuleBasedParser

PARSER = RuleBasedParser()
MOSCOW = ZoneInfo("Europe/Moscow")
BERLIN = ZoneInfo("Europe/Berlin")


def parse(text: str, local_now: datetime, tz: ZoneInfo = MOSCOW, **ctx: object):  # type: ignore[no-untyped-def]
    return PARSER.parse(text, ParseContext(now=local_to_utc(local_now, tz), tz=tz, **ctx))  # type: ignore[arg-type]


def test_year_rollover_for_time_only() -> None:
    result = parse("в 0:10 тест", datetime(2026, 12, 31, 23, 50))
    assert result.when == datetime(2027, 1, 1, 0, 10)
    assert Flag.ROLLED_TO_FUTURE in result.flags


def test_leap_day_is_found_in_a_leap_year() -> None:
    result = parse("29 февраля в 10:00 праздник", datetime(2028, 2, 28, 12, 0))
    assert result.when == datetime(2028, 2, 29, 10, 0)


def test_leap_day_in_a_common_year_waits_for_the_next_leap_year() -> None:
    assert parse("29 февраля позвонить", datetime(2025, 3, 1, 12, 0)).when == datetime(
        2028, 2, 29, 9, 0
    )


def test_one_month_from_the_31st_clamps_to_month_end() -> None:
    result = parse("через месяц позвонить", datetime(2026, 1, 31, 10, 0))
    assert result.when == datetime(2026, 2, 28, 9, 0)


def test_every_31st_in_february_starts_on_the_last_day() -> None:
    result = parse("каждое 31 число оплатить", datetime(2026, 2, 10, 12, 0))
    assert result.when == datetime(2026, 2, 28, 9, 0)
    assert result.recurrence is not None
    assert result.recurrence.to_rrule() == "FREQ=MONTHLY;BYMONTHDAY=31"


def test_nonexistent_local_time_is_kept_as_written_for_the_dispatcher() -> None:
    result = parse("завтра в 2:30 проверить", datetime(2026, 3, 28, 12, 0), BERLIN)
    assert result.when == datetime(2026, 3, 29, 2, 30)  # the domain layer shifts it (ADR-0006)


def test_relative_time_is_absolute_across_a_dst_change() -> None:
    result = parse("через 3 часа позвонить", datetime(2026, 3, 29, 0, 30), BERLIN)
    assert result.when == datetime(2026, 3, 29, 4, 30)  # 01:30 -> 03:30 CEST is +2h wall time


def test_user_anchors_are_respected() -> None:
    anchors = Anchors(morning=time(8, 0), midday=time(12, 30), evening=time(20, 0))
    now = datetime(2026, 10, 13, 15, 0)
    assert parse("завтра утром зарядка", now, anchors=anchors).when == datetime(2026, 10, 14, 8, 0)
    assert parse("завтра днем обед", now, anchors=anchors).when == datetime(2026, 10, 14, 12, 30)
    assert parse("завтра вечером кино", now, anchors=anchors).when == datetime(2026, 10, 14, 20, 0)
    assert parse("завтра позвонить", now, anchors=anchors).when == datetime(2026, 10, 14, 8, 0)


def test_another_zone_changes_the_local_date() -> None:
    tokyo = ZoneInfo("Asia/Tokyo")
    result = parse("сегодня в 20:00 тренировка", datetime(2026, 10, 14, 9, 0), tokyo)
    assert result.when == datetime(2026, 10, 14, 20, 0)


def test_seconds_in_now_do_not_break_the_one_minute_margin() -> None:
    now_utc = local_to_utc(datetime(2026, 10, 13, 15, 0, 59), MOSCOW)
    ctx = ParseContext(now=now_utc, tz=MOSCOW)
    assert PARSER.parse("в 15:01 тест", ctx).when == datetime(2026, 10, 13, 15, 1)
    assert PARSER.parse("через 1 минуту тест", ctx).when == datetime(2026, 10, 13, 15, 1)


def test_naive_now_is_rejected() -> None:
    with pytest.raises(ValueError, match="aware"):
        ParseContext(now=datetime(2026, 1, 1), tz=MOSCOW)


def test_uncertain_number_triggers_clarification() -> None:
    result = parse("в 5 корпусе встреча", datetime(2026, 10, 13, 15, 0))
    assert Flag.TIME_UNCERTAIN in result.flags
    assert result.needs_clarification


def test_several_conflicting_times_trigger_clarification() -> None:
    result = parse("в 9:00 позвонить, в 10:00 встреча", datetime(2026, 10, 13, 15, 0))
    assert Flag.MULTIPLE_TIMES in result.flags
    assert result.needs_clarification


def test_explicit_category_is_matched_fuzzily_against_existing_ones() -> None:
    now = datetime(2026, 10, 13, 15, 0)
    result = parse("завтра в 10:00 планерка #работа", now, categories=("Дом", "Работа"))
    assert result.category_ref == "работа"
    assert result.category == "Работа"
    typo = parse("завтра в 10:00 планерка #рабта", now, categories=("Дом", "Работа"))
    assert typo.category == "Работа"
    unknown = parse("завтра в 10:00 планерка #хобби", now, categories=("Дом", "Работа"))
    assert unknown.category is None
    assert unknown.category_ref == "хобби"


def test_schedule_view_uses_the_resolved_recurrence() -> None:
    result = parse("каждый понедельник в 9:00 планерка", datetime(2026, 10, 13, 15, 0))
    schedule = result.schedule(MOSCOW)
    assert schedule is not None
    assert schedule.recurrence == result.recurrence
    assert parse("позвонить маме", datetime(2026, 10, 13, 15, 0)).schedule(MOSCOW) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("за час до встречи завтра в 18:00", "Встреча"),
        ("за час до встречи семьи завтра в 18:00", "Встреча семьи"),
        ("за час до встречи собраться завтра в 18:00", "До встречи собраться"),
    ],
)
def test_event_after_a_lead_shift_becomes_the_reminder_text(text: str, expected: str) -> None:
    assert parse(text, datetime(2026, 10, 13, 15, 0)).text == expected


def test_a_phrase_with_only_a_date_has_empty_text() -> None:
    result = parse("завтра в 18:00", datetime(2026, 10, 13, 15, 0))
    assert result.text == ""
    assert Flag.EMPTY_TEXT in result.flags
