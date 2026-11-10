"""Parser sandbox.

Example: ``python -m napominator.nlp "завтра в 9 позвонить маме" --now "2026-10-13 15:00"``.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from napominator.domain.timeutil import local_to_utc
from napominator.nlp import ParseContext, RuleBasedParser


def main() -> None:
    cli = argparse.ArgumentParser(description="Parse a reminder phrase and print the result.")
    cli.add_argument("text")
    cli.add_argument("--now", help='local time "YYYY-MM-DD HH:MM" (default: current time)')
    cli.add_argument("--tz", default="Europe/Moscow")
    cli.add_argument("--category", action="append", default=[], help="existing category name")
    args = cli.parse_args()

    tz = ZoneInfo(args.tz)
    if args.now:
        local = datetime.strptime(args.now, "%Y-%m-%d %H:%M")  # noqa: DTZ007
        now = local_to_utc(local, tz)
    else:
        now = datetime.now(UTC)
    ctx = ParseContext(now=now, tz=tz, categories=tuple(args.category))
    result = RuleBasedParser().parse(args.text, ctx)

    print(f"text:        {result.text!r}")
    print(f"when:        {result.when.isoformat(' ') if result.when else None}")
    print(f"rrule:       {result.recurrence.to_rrule() if result.recurrence else None}")
    print(f"category:    ref={result.category_ref!r} match={result.category!r}")
    print(f"lead/nag:    {result.lead} / {result.nag}")
    print(f"confidence:  {result.confidence:.2f}  clarify={result.needs_clarification}")
    print(f"flags:       {sorted(f.value for f in result.flags)}")


if __name__ == "__main__":
    main()
