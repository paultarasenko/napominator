import pytest

from napominator.infrastructure.db.url import is_sqlite, normalize_database_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgres://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        ("postgresql://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        ("postgresql+asyncpg://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        ("sqlite:///./a.db", "sqlite+aiosqlite:///./a.db"),
        ("sqlite+aiosqlite:///./a.db", "sqlite+aiosqlite:///./a.db"),
        ("  sqlite:///./a.db  ", "sqlite+aiosqlite:///./a.db"),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize_database_url(raw) == expected


def test_unsupported_dialect_is_rejected() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        normalize_database_url("mysql://u:p@h/db")


def test_is_sqlite() -> None:
    assert is_sqlite("sqlite+aiosqlite:///x.db")
    assert not is_sqlite("postgresql+asyncpg://h/db")
