# Развёртывание на VPS

> Файлы `Dockerfile` и `docker-compose.yml` написаны, но **не собирались и не запускались** в среде разработки (там нет Docker). Первый запуск проверяйте внимательно и сообщайте о расхождениях.

Профиль: 1 vCPU, 1 ГБ RAM, Docker + Compose v2.

## Первый запуск

```bash
git clone <репозиторий> napominator && cd napominator
cp .env.example .env
$EDITOR .env          # BOT_TOKEN, POSTGRES_PASSWORD (длинный случайный), ADMIN_IDS, LOG_FORMAT=json
docker compose up -d --build
docker compose logs -f bot
```

Порядок старта: PostgreSQL → `migrate` (разово, `alembic upgrade head`) → `bot`. Бот работает через long polling: входящие порты открывать не нужно.

Проверка: `docker compose ps` (у `bot` должно быть `healthy`), затем напишите боту `/start`.

## Обновление

```bash
git pull
docker compose up -d --build   # миграции применятся автоматически
```

Остановка корректная: диспетчер дожидается уже начатых отправок (до 30 с) и не берёт новых; недоотправленное подхватится после перезапуска, максимум с одним повтором (ADR-0003).

## Резервные копии

```bash
# ежедневно, например из cron:
docker compose exec -T postgres pg_dump -U napominator -Fc napominator > /backups/napominator-$(date +%F).dump
# хранить вне сервера; чистить копии старше 14 дней
```

Восстановление в пустую базу:

```bash
docker compose stop bot
docker compose exec -T postgres pg_restore -U napominator -d napominator --clean --if-exists < /backups/napominator-ДАТА.dump
docker compose up -d bot
```

Копия содержит личные данные пользователей: храните её зашифрованной и с ограниченным доступом.

## Эксплуатация

- Логи в JSON, без текстов напоминаний: `docker compose logs bot`.
- `/admin` в чате (для `ADMIN_IDS`): число пользователей, очередь, отставание, последние сбои.
- Поднимите отставание очереди или частые `failed` — смотрите логи `dispatcher`.
- Если бот «молчит»: `docker compose ps` → `healthy`? `docker compose exec bot python -m napominator.healthcheck`.

## Чего пока нет

Режим webhook (параметры в конфиге есть, запуск не подключён), TLS-прокси, внешний мониторинг. Для личного бота long polling достаточен.
