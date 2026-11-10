from __future__ import annotations

import pytest
from pydantic import ValidationError

from napominator.infrastructure.config import Settings

TOKEN = "123456:ABCDEF-token-for-tests"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "BOT_TOKEN",
        "DATABASE_URL",
        "ADMIN_IDS",
        "WEBHOOK_URL",
        "WEBHOOK_SECRET",
        "LLM_ENABLED",
        "LLM_API_KEY",
        "LLM_MODEL",
        "REDIS_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir("/tmp")  # noqa: S108 - make sure no stray .env is picked up


def make(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[arg-type]


def test_defaults() -> None:
    s = make(bot_token=TOKEN)
    assert s.database_url.startswith("sqlite+aiosqlite://")
    assert s.admin_ids == []
    assert s.dispatcher_poll_seconds == 5.0
    assert not s.webhook_enabled
    assert not s.llm_enabled


def test_missing_token_fails() -> None:
    with pytest.raises(ValidationError) as info:
        Settings(_env_file=None)
    assert "bot_token" in str(info.value)


def test_token_is_never_printed() -> None:
    s = make(bot_token=TOKEN)
    assert TOKEN not in repr(s)
    assert TOKEN not in str(s)


def test_admin_ids_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_IDS", "1, 2;3")
    s = make(bot_token=TOKEN)
    assert s.admin_ids == [1, 2, 3]


def test_admin_ids_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_IDS", "")
    assert make(bot_token=TOKEN).admin_ids == []


def test_database_url_is_normalised() -> None:
    s = make(bot_token=TOKEN, database_url="postgres://u:p@h/db")
    assert s.database_url == "postgresql+asyncpg://u:p@h/db"


def test_bad_database_url_fails() -> None:
    with pytest.raises(ValidationError):
        make(bot_token=TOKEN, database_url="mysql://x")


def test_webhook_requires_https_and_secret() -> None:
    with pytest.raises(ValidationError, match="https"):
        make(bot_token=TOKEN, webhook_url="http://example.com")
    with pytest.raises(ValidationError, match="WEBHOOK_SECRET"):
        make(bot_token=TOKEN, webhook_url="https://example.com")
    s = make(bot_token=TOKEN, webhook_url="https://example.com", webhook_secret="s" * 16)
    assert s.webhook_enabled


def test_llm_requires_key_and_model() -> None:
    with pytest.raises(ValidationError, match="LLM_API_KEY"):
        make(bot_token=TOKEN, llm_enabled="true")
    s = make(bot_token=TOKEN, llm_enabled="true", llm_api_key="k", llm_model="m")
    assert s.llm_enabled


def test_empty_optionals_become_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "")
    assert make(bot_token=TOKEN).redis_url is None
