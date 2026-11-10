"""All user-facing strings (Russian, Telegram HTML). Builders escape user content."""

from __future__ import annotations

from datetime import datetime

from aiogram.types import InlineKeyboardMarkup

from napominator.application.ports import Delivery
from napominator.application.queries import Page, TodayView
from napominator.bot import keyboards
from napominator.bot.formatting import (
    MONTHS_GEN,
    clock,
    count,
    describe_moment,
    describe_rule,
    h,
    lead_phrase,
    short_moment,
)
from napominator.domain.entities import (
    AdminSnapshot,
    Category,
    NotificationKind,
    Reminder,
    ReminderStatus,
)
from napominator.domain.recurrence import Recurrence
from napominator.domain.timeutil import load_zone, utc_to_local

# --- onboarding ---

WELCOME = (
    "Привет! Я <b>Напоминатор</b> — личный помощник, который не даст забыть важное.\n\n"
    "Просто напиши, о чём и когда напомнить, например:\n"
    "• <i>завтра в 9 позвонить маме</i>\n"
    "• <i>через 20 минут выключить духовку</i>\n"
    "• <i>каждый понедельник в 10:00 планёрка</i>\n\n"
    "Для начала скажи, в каком ты часовом поясе — так напоминания придут вовремя."
)
WELCOME_BACK = "С возвращением! 👋 Всё на месте."
SERIES_RESUMED = "Повторяющиеся напоминания ({n}) снова работают."
TZ_ASK_CITY = "Напиши свой город, например «Екатеринбург»."
TZ_CITY_NOT_FOUND = (
    "Не нашёл такой город. Попробуй написать иначе, отправь геопозицию или выбери из списка."
)
TZ_LOCATION_FAILED = (
    "Не получилось определить пояс по геопозиции. Напиши город или выбери из списка."
)
TZ_PICK_REGION = "Выбери регион:"
TZ_PICK_METHOD = "Как определим часовой пояс?"
TZ_PICK_ZONE = "Выбери город:"
TZ_NEED_FIRST = "Сначала настроим часовой пояс — это займёт секунду."
TZ_CHANGE_MODE = (
    "У тебя уже есть напоминания. Как их пересчитать?\n\n"
    "🕘 <b>Время на часах то же</b> — «в 10:00» останется «в 10:00» (ты переехал).\n"
    "🌍 <b>Тот же момент</b> — напоминания сработают в те же мгновения, "
    "просто у тебя изменился пояс."
)
TZ_CHANGED = "Часовой пояс обновлён: <b>{zone}</b>. Пересчитано напоминаний: {n}."


def tz_confirm(local_now: datetime, zone_label: str) -> str:
    return f"У тебя сейчас <b>{clock(local_now)}</b> ({h(zone_label)})? Всё верно?"


def onboarding_done(zone_label: str) -> str:
    return (
        f"Готово! Часовой пояс: <b>{h(zone_label)}</b>.\n\n"
        "Теперь просто напиши, что и когда напомнить. Меню — кнопки внизу."
    )


DEMO_OFFER = "Хочешь увидеть, как это работает? Я пришлю тестовое напоминание через пару минут."
DEMO_TEXT = "Это пробное напоминание 🎉 Нажми «Готово» или «Отложить»"
DEMO_STARTED = "Отлично! Пробное напоминание придёт <b>{when}</b> — всего пара минут."

HELP = (
    "<b>Как пользоваться</b>\n\n"
    "Пиши обычным языком:\n"
    "• <i>завтра в 18:30 забрать посылку</i>\n"
    "• <i>через 2 часа 15 минут позвонить</i>\n"
    "• <i>в пятницу в 10 врач #здоровье</i>\n"
    "• <i>по будням в 8:30 принять таблетки</i>\n"
    "• <i>1 числа каждого месяца оплатить аренду</i>\n"
    "• <i>завтра в 10:00 врач за час до</i> — напомню заранее\n\n"
    "Если я не уверен во времени — переспрошу кнопками.\n\n"
    "<b>Команды</b>\n"
    "/new — создать по шагам\n/today — сегодня\n/list — все напоминания\n"
    "/categories — категории\n/search — поиск\n/settings — настройки\n"
    "/privacy — какие данные я храню\n/cancel — отменить текущее действие"
)

# --- creating ---

ASK_WHEN = "Когда напомнить? Напиши, например «завтра в 9» или выбери ниже."
ASK_TEXT = "О чём напомнить?"
ASK_TEXT_FOR = "Понял: <b>{when}</b>. О чём напомнить?"
NEW_ASK_TEXT = "О чём напомнить? Напиши текст напоминания."
PAST = "Это время уже прошло. Когда напомнить?"
INVALID_DATE = "Такой даты не существует. Когда напомнить?"
NEEDS_TIME = "Во сколько? Утро, день или вечер — выбери или напиши время."
TOO_FREQUENT = "Слишком часто: минимальный интервал повтора — 5 минут. Напиши реже."
PAST_UNTIL = "Повтор заканчивается раньше первого срабатывания. Уточни срок."
CANCELLED = "Отменено."
NOTHING_TO_CANCEL = "Сейчас нечего отменять."
NOT_UNDERSTOOD_TIME = (
    "Не понял время. Напиши, например «завтра в 9» или «через час», либо выбери кнопкой."
)
PICK_DATE = "Выбери дату:"
PICK_HOUR = "Выбери час:"
PICK_MINUTE = "Выбери минуты:"
LIMIT_REMINDERS = "Достигнут лимит активных напоминаний (1000). Удали ненужные и повтори."
NO_FUTURE = "У этого напоминания нет ближайшего срабатывания в будущем. Уточни время."
TEXT_TOO_LONG = "Слишком длинный текст — не больше 1000 символов."
BUSY = "Слишком часто — подожди пару секунд."
GENERIC_ERROR = "Что-то пошло не так. Попробуй ещё раз чуть позже."
GONE = "Напоминание не найдено или уже удалено."
EXPIRED = "Эта кнопка уже неактуальна."


def guess(when_text: str, text: str) -> str:
    return (
        f"Правильно ли я понял?\n\n📝 {h(text)}\n🕒 <b>{when_text}</b>\n\nЕсли нет — измени время."
    )


def card(
    reminder: Reminder,
    *,
    when: datetime | None,
    now_local: datetime,
    category: Category | None,
    rule: Recurrence | None,
    header: str = "📌 Напоминание",
    suggested: bool = False,
) -> str:
    cat = (
        f"{category.emoji or '🗂'} {h(category.name)}"
        + (" <i>(предложено)</i>" if suggested else "")
        if category
        else "▫️ Без категории"
    )
    lines = [f"<b>{header}</b>", cat, f"📝 {h(reminder.text)}"]
    if when is not None:
        repeat = f"  ·  🔁 {describe_rule(rule)}" if rule is not None else ""
        lines.append(f"🕒 <b>{describe_moment(when, now_local)}</b>{repeat}")
    if reminder.lead_minutes:
        lines.append("🔔 Заранее: " + ", ".join(_lead(m) for m in sorted(reminder.lead_minutes)))
    if reminder.status is ReminderStatus.PAUSED:
        lines.append("⏸ На паузе")
    elif reminder.status is ReminderStatus.DONE:
        lines.append("✔ Выполнено")
    elif reminder.status is ReminderStatus.DELETED:
        lines.append("🗑 Удалено")
    return "\n".join(lines)


def _lead(minutes: int) -> str:
    if minutes % 1440 == 0:
        return "за " + count(minutes // 1440, "день", "дня", "дней")
    if minutes % 60 == 0:
        return "за " + count(minutes // 60, "час", "часа", "часов")
    return "за " + count(minutes, "минуту", "минуты", "минут")


# --- reactions ---

DONE = "✅ Готово: {text}"
SNOOZED = "⏰ Отложено до <b>{when}</b>: {text}"
DELETED = "🗑 Удалено: {text}"
RESTORED = "↩️ Возвращено."
UNDONE = "↩️ Отменено."
SNOOZE_PICK = "На сколько отложить?"
SNOOZE_OWN = "Выбери дату и время, когда напомнить ещё раз:"
PAUSED = "⏸ Поставлено на паузу."
RESUMED = "▶️ Возобновлено."
ASK_NEW_TEXT = "Напиши новый текст напоминания."
ASK_NEW_TIME = "Напиши новое время, например «завтра в 9», или выбери кнопкой."
TEXT_UPDATED = "✏️ Текст обновлён."
CATEGORY_UPDATED = "🗂 Категория обновлена."
TIME_UPDATED = "⏰ Время обновлено."
PICK_CATEGORY = "Выбери категорию:"


# --- lists ---


def today_screen(view: TodayView, tz_name: str, now: datetime) -> str:
    tz = load_zone(tz_name)
    now_local = utc_to_local(now, tz)
    title = f"📅 <b>Сегодня, {now_local.day} {_month(now_local)}</b>"
    lines = [title, ""]
    if view.awaiting:
        lines.append("⏳ <b>Ждут отметки</b>")
        for item in view.awaiting[:5]:
            lines.append(f"• {h(item.reminder.text)}")
        lines.append("")
    if view.upcoming:
        lines.append("🕒 <b>Впереди сегодня</b>")
        for entry in view.upcoming:
            when = (
                short_moment(utc_to_local(entry.next_at, tz), now_local) if entry.next_at else "—"
            )
            lines.append(f"• {when} — {h(entry.reminder.text)}")
    elif not view.awaiting:
        lines.append("На сегодня ничего не запланировано 🎉")
    return "\n".join(lines).rstrip()


def _month(local: datetime) -> str:
    return MONTHS_GEN[local.month - 1]


LIST_TITLES = {
    "soon": "📋 Ближайшие",
    "recurring": "🔁 Повторяющиеся",
    "done": "✔ Выполненные",
    "all": "🗒 Все напоминания",
    "cat": "🗂 Категория",
}


def list_screen(active: str, page: Page, category: Category | None) -> str:
    title = LIST_TITLES.get(active, "📋 Напоминания")
    if active == "cat" and category is not None:
        title = f"{category.emoji or '🗂'} {h(category.name)}"
    if page.total == 0:
        return f"<b>{title}</b>\n\nЗдесь пока пусто."
    return f"<b>{title}</b> — {page.total}\nСтраница {page.page + 1} из {page.pages}"


FORWARD_EMPTY = "В пересланном сообщении нет текста, поэтому напомнить не о чем."
SEARCH_ASK = "Что искать? Напиши слово или часть текста напоминания."
SEARCH_EMPTY = "Ничего не нашёл. Попробуй другое слово."
SEARCH_FOUND = "🔎 Найдено: {n}"

# --- categories ---

CATEGORIES_TITLE = "🗂 <b>Категории</b>\nГруппируй напоминания — я буду предлагать подходящую сам."
CAT_ASK_NEW = "Напиши название новой категории. Можно с эмодзи: «🎸 Музыка»."
CAT_ASK_RENAME = "Напиши новое название (можно с эмодзи)."
CAT_ASK_EMOJI = "Отправь эмодзи для этой категории."
CAT_DUPLICATE = "Категория с таким названием уже есть."
CAT_BAD_NAME = "Название должно быть от 1 до 32 символов, без служебных знаков."
CAT_LIMIT = "Достигнут лимит категорий (30)."
CAT_NO_EMOJI = "Не вижу эмодзи. Отправь одно эмодзи."
CAT_DELETED = "🗑 Категория удалена. Перенесено напоминаний: {n}."


def category_menu_text(category: Category, reminders: int) -> str:
    return f"{category.emoji or '🗂'} <b>{h(category.name)}</b>\nАктивных напоминаний: {reminders}"


def category_delete_text(category: Category, reminders: int) -> str:
    if reminders == 0:
        return f"Удалить категорию «{h(category.name)}»?"
    return (
        f"В категории «{h(category.name)}» напоминаний: {reminders}. "
        "Они не удалятся — куда их перенести?"
    )


# --- privacy ---

EXPORT_CAPTION = "📤 Все твои данные в одном файле (JSON). Храни его в надёжном месте."
DELETE_DONE = (
    "🗑 Готово: все твои данные удалены, напоминания отменены. "
    "Если захочешь вернуться — просто нажми /start."
)


def privacy(*, llm_enabled: bool) -> str:
    llm = (
        "\n• Если я не смог разобрать фразу сам, её текст может быть отправлен во внешний "
        "языковой сервис для разбора. Это можно отключить у владельца бота."
        if llm_enabled
        else "\n• Тексты не уходят во внешние сервисы: разбор фраз идёт на сервере бота."
    )
    return (
        "🔒 <b>Какие данные я храню</b>\n\n"
        "• Твой Telegram-id, имя и @username — чтобы присылать напоминания именно тебе.\n"
        "• Часовой пояс и время «утро / день / вечер».\n"
        "• Тексты напоминаний, категории и историю доставки.\n"
        "• Геопозицию я <b>не сохраняю</b>: по ней один раз определяется пояс, и всё."
        f"{llm}\n\n"
        "<b>Сроки хранения.</b> Выполненные напоминания — 90 дней, удалённые — 7 дней "
        "(пока работает «Вернуть»), история доставки — 90 дней.\n\n"
        "Выгрузить все данные (/export) или удалить их навсегда (/delete_me) можно в любой момент."
    )


def delete_confirm(reminders: int, categories: int) -> str:
    return (
        "⚠️ <b>Удалить все данные?</b>\n\n"
        f"Напоминаний: {reminders}, категорий: {categories}. Всё будет стёрто безвозвратно, "
        "запланированные напоминания не придут."
    )


def admin_overview(snapshot: AdminSnapshot) -> str:
    lines = [
        "🛠 <b>Состояние бота</b>",
        f"Пользователи: {snapshot.users_active} активных из {snapshot.users_total}",
        f"Активных напоминаний: {snapshot.reminders_active}",
        f"В очереди уведомлений: {snapshot.notifications_open}",
        f"Отставание очереди: {int(snapshot.queue_lag_seconds)} с",
        f"Сбоев доставки за 24 ч: {snapshot.failures_24h}",
    ]
    if snapshot.recent_errors:
        lines.append("\nПоследние ошибки:")
        lines.extend(f"• {kind}/{status}: {h(err)}" for kind, status, err in snapshot.recent_errors)
    return "\n".join(lines)


# --- settings ---


def settings_screen(zone_label: str, morning: str, midday: str, evening: str) -> str:
    return (
        "⚙️ <b>Настройки</b>\n\n"
        f"🌍 Часовой пояс: <b>{h(zone_label)}</b>\n"
        f"🌅 Утро: <b>{morning}</b>   ☀️ День: <b>{midday}</b>   🌆 Вечер: <b>{evening}</b>\n\n"
        "Эти времена я использую для «утром», «вечером» и когда ты не указал время."
    )


ASK_ANCHOR = "Во сколько у тебя {name}? Выбери час."
ANCHOR_SAVED = "Сохранено."
ANCHOR_ORDER = "Утро должно быть раньше дня, а день — раньше вечера."


# --- notification message ---


def render_delivery(delivery: Delivery) -> tuple[str, InlineKeyboardMarkup | None]:
    body = h(delivery.text)
    when = delivery.occurrence_local
    stamp = clock(when) if when else ""
    match delivery.kind:
        case NotificationKind.LEAD:
            lines = [f"⏳ Через {lead_phrase(delivery.seq)}: <b>{body}</b>", f"🕒 В {stamp}"]
        case NotificationKind.SNOOZE:
            lines = [f"⏰ Напоминаю: <b>{body}</b>"]
        case NotificationKind.NAG:
            lines = [f"❗ Не забудьте: <b>{body}</b>"]
        case NotificationKind.DIGEST:
            lines = ["🗓 Сводка на сегодня"]
        case _:
            lines = [f"⏰ <b>{body}</b>"]
    late = delivery.late_by
    if late is not None and stamp and delivery.kind is not NotificationKind.DIGEST:
        lines.append(f"⏳ Должно было сработать в {stamp}")
    reactable = delivery.kind in (
        NotificationKind.DUE,
        NotificationKind.SNOOZE,
        NotificationKind.NAG,
    )
    markup = keyboards.ack(delivery.notification_id) if reactable else None
    return "\n".join(lines), markup
