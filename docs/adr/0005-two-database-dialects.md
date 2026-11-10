# ADR-0005: PostgreSQL в проде, SQLite в dev и тестах

**Статус:** принято · 13.10.2026

**Контекст.** Нужны конкурентность в проде и запуск без Docker локально.

**Решение.** Один код на оба диалекта через SQLAlchemy 2 async; выбор по `DATABASE_URL`. Для SQLite: `PRAGMA foreign_keys=ON`, WAL, `busy_timeout`; `TypeDecorator` `UTCDateTime` возвращает aware-UTC; Alembic `render_as_batch`; детерминированные имена ограничений (naming convention). Claim: PostgreSQL — `FOR UPDATE SKIP LOCKED`, SQLite — атомарный `UPDATE … RETURNING`.

**Последствия.** Конкурентные сценарии проверяются только на PostgreSQL (CI); SQLite не используется в проде с несколькими экземплярами.
