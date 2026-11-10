"""Unit of work: one database transaction per ``async with`` block."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from napominator.infrastructure.db.repositories import (
    SqlAdminRepository,
    SqlCategoryRepository,
    SqlHintRepository,
    SqlNotificationRepository,
    SqlProcessedUpdateRepository,
    SqlReminderRepository,
    SqlUserRepository,
)


class SqlAlchemyUnitOfWork:
    """Commits on normal exit, rolls back when the block raises."""

    users: SqlUserRepository
    reminders: SqlReminderRepository
    notifications: SqlNotificationRepository
    categories: SqlCategoryRepository
    hints: SqlHintRepository
    processed_updates: SqlProcessedUpdateRepository
    admin: SqlAdminRepository

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> Self:
        session = self._factory()
        self._session = session
        self.users = SqlUserRepository(session)
        self.reminders = SqlReminderRepository(session)
        self.notifications = SqlNotificationRepository(session)
        self.categories = SqlCategoryRepository(session)
        self.hints = SqlHintRepository(session)
        self.processed_updates = SqlProcessedUpdateRepository(session)
        self.admin = SqlAdminRepository(session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        session = self._session
        if session is None:  # pragma: no cover - defensive
            return
        try:
            if exc_type is None:
                await session.commit()
            else:
                await session.rollback()
        finally:
            await session.close()
            self._session = None

    async def commit(self) -> None:
        if self._session is None:  # pragma: no cover - defensive
            msg = "unit of work is not active"
            raise RuntimeError(msg)
        await self._session.commit()
