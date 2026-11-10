# Архитектура «Напоминатора»

Основание — ТЗ v1.0 (13.10.2026). Документ фиксирует слои, потоки данных, решения и допущения; обновляется по мере реализации.

## 1. Слои

Pragmatic Clean Architecture, зависимости направлены внутрь:

```text
domain  ←  nlp, application  ←  infrastructure, bot  ←  main
```

| Слой | Ответственность | Запрещено |
|---|---|---|
| `domain` | сущности, value objects, время и повторы (`next_occurrence`) | I/O, aiogram, SQLAlchemy, наивные `datetime` |
| `nlp` | детерминированный парсер русского текста, CLI-песочница, порт LLM-fallback | aiogram, SQLAlchemy |
| `application` | use cases, порты (`Clock`, репозитории/UoW, `Notifier`, `ReminderParser`) | знание о Telegram и конкретной БД |
| `infrastructure` | SQLAlchemy, dispatcher, Telegram-отправитель, config, logging | бизнес-логика |
| `bot` | aiogram: routers, handlers, keyboards, `CallbackData`, тексты | бизнес-логика (handlers тонкие) |
| `main.py` | composition root, graceful shutdown | — |

Протоколы вводятся только на границах, где есть вторая реализация или подмена в тестах.

## 2. Потоки данных

**Создание.** Сообщение → throttling/dedup-middleware → `UnitOfWork` на апдейт → `ReminderParser.parse` (чистая функция) → use case `CreateReminder` → запись `reminders` и первого `notifications` (одна транзакция) → пробуждение dispatcher'а → карточка пользователю.

**Доставка** (transactional outbox, ADR-0002). Dispatcher: claim пачки созревших уведомлений (короткая транзакция, lease) → отправка вне транзакции с лимитерами → commit результата и вставка следующего вхождения серии (одна транзакция) → ожидание до ближайшего `fire_at`.

## 3. Сквозные решения

- Время: UTC в БД, пояс пользователя — IANA; «сейчас» только через `Clock` (ADR-0006).
- Повторы считаются по локальному настенному времени (`dateutil.rrule` над наивным `dtstart_local`, затем локализация).
- Идемпотентность: условные `UPDATE … WHERE status = :expected`, ключ идемпотентности вхождения, дедупликация по `update_id`.
- Конфигурация: `pydantic-settings`, fail-fast с читаемым сообщением (код выхода 2); недоступная БД — код 3.
- БД: PostgreSQL в проде, SQLite в dev/тестах (ADR-0005); SQLite включается с `foreign_keys`, WAL, `busy_timeout`.
- Логи без текстов напоминаний; только внутренний `user_id` и `update_id`.

## 4. Модель данных: отступления от наброска ТЗ

- `categories.name_key` — свёрнутое имя; уникальность `(user_id, name_key)`: `lower()` в SQLite работает только для ASCII.
- `notifications`: `scheduled_at` (исходное время; не меняется), `seq` (номер повтора/смещение «заранее» в минутах), `dedup_key` (уникальный ключ идемпотентности вместо составного индекса: `reminder_id` бывает `NULL` у сводок, а `NULL` в уникальных индексах не сравниваются).
- Статусы и виды хранятся строками с `CHECK`, а не нативным enum: одинаково на SQLite и PostgreSQL, миграции не требуют `ALTER TYPE`.
- Миграции не импортируют код приложения (`UTCDateTime` рендерится как `DateTime(timezone=True)`); тест следит, что слово `napominator` в миграциях не встречается.

## 5. Допущения (этап 0–2)

1. Версии зависимостей фиксируются `uv.lock`; на момент старта: aiogram 3.31, SQLAlchemy 2.1, Alembic 1.20, pydantic-settings 2.15.
2. Alembic берёт URL из `DATABASE_URL` (токен бота для миграций не нужен), поэтому `migrate`-сервис в compose не требует `BOT_TOKEN`.
3. Тип `UTCDateTime` возвращает aware-UTC на любом диалекте; передача наивного `datetime` — ошибка программиста (исключение).
4. Docker и PostgreSQL в среде разработки недоступны; PostgreSQL-проверки запускаются в CI через service container (`TEST_POSTGRES_URL`), локально — помечены `skip`.
5. PostgreSQL-проверки локально выполняются на временном сервере из пакета `pgserver` (dev-зависимость); в CI — через `TEST_POSTGRES_URL` (service container). Docker в среде разработки недоступен.
6. Каждый этап завершается `make check`, обновлением `docs/PROGRESS.md` и коммитом.
