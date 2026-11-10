"""Database URL normalisation shared by the app and Alembic."""

from __future__ import annotations

DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./napominator.db"

_PREFIX_MAP = (
    ("postgresql+asyncpg://", "postgresql+asyncpg://"),
    ("postgresql://", "postgresql+asyncpg://"),
    ("postgres://", "postgresql+asyncpg://"),
    ("sqlite+aiosqlite://", "sqlite+aiosqlite://"),
    ("sqlite://", "sqlite+aiosqlite://"),
)


def normalize_database_url(url: str) -> str:
    """Map common URL spellings onto the async drivers this project ships with.

    Raises ``ValueError`` for unsupported dialects so misconfiguration fails fast.
    """
    stripped = url.strip()
    for prefix, replacement in _PREFIX_MAP:
        if stripped.startswith(prefix):
            return replacement + stripped[len(prefix) :]
    msg = "DATABASE_URL must start with postgresql://, postgres:// or sqlite:///"
    raise ValueError(msg)


def is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")
