"""A reminder that is still being assembled across several messages (stored in FSM data)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from napominator.domain.recurrence import Recurrence
from napominator.nlp.models import Flag, ParseResult

NAG_INTERVAL_MIN = 10
NAG_MAX = 3


@dataclass(slots=True)
class Pending:
    text: str = ""
    when: str | None = None  # ISO naive local time of the first occurrence
    rrule: str | None = None
    category_ref: str | None = None
    lead_minutes: list[int] = field(default_factory=list)
    nag: bool = False
    flags: list[str] = field(default_factory=list)
    confident: bool = True

    @classmethod
    def from_result(cls, result: ParseResult) -> Pending:
        lead = int(result.lead.total_seconds() // 60) if result.lead else 0
        return cls(
            text=result.text,
            when=result.when.isoformat() if result.when else None,
            rrule=result.recurrence.to_rrule() if result.recurrence else None,
            category_ref=result.category_ref,
            lead_minutes=[lead] if lead > 0 else [],
            nag=result.nag,
            flags=sorted(f.value for f in result.flags),
            confident=not result.needs_clarification,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Pending:
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def when_local(self) -> datetime | None:
        return datetime.fromisoformat(self.when) if self.when else None

    @property
    def recurrence(self) -> Recurrence | None:
        return Recurrence.from_rrule(self.rrule) if self.rrule else None

    def has(self, flag: Flag) -> bool:
        return flag.value in self.flags
