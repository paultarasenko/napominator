"""Curated time zones for the "pick from a list" onboarding path (labels are Russian)."""

from __future__ import annotations

# (group, [(label, IANA)])
POPULAR_ZONES: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Россия",
        (
            ("Калининград", "Europe/Kaliningrad"),
            ("Москва", "Europe/Moscow"),
            ("Самара", "Europe/Samara"),
            ("Екатеринбург", "Asia/Yekaterinburg"),
            ("Омск", "Asia/Omsk"),
            ("Красноярск", "Asia/Krasnoyarsk"),
            ("Иркутск", "Asia/Irkutsk"),
            ("Якутск", "Asia/Yakutsk"),
            ("Владивосток", "Asia/Vladivostok"),
            ("Магадан", "Asia/Magadan"),
            ("Камчатка", "Asia/Kamchatka"),
        ),
    ),
    (
        "СНГ",
        (
            ("Киев", "Europe/Kyiv"),
            ("Минск", "Europe/Minsk"),
            ("Тбилиси", "Asia/Tbilisi"),
            ("Ереван", "Asia/Yerevan"),
            ("Баку", "Asia/Baku"),
            ("Алматы", "Asia/Almaty"),
            ("Ташкент", "Asia/Tashkent"),
            ("Бишкек", "Asia/Bishkek"),
            ("Кишинёв", "Europe/Chisinau"),
        ),
    ),
    (
        "Балтия",
        (
            ("Таллин", "Europe/Tallinn"),
            ("Рига", "Europe/Riga"),
            ("Вильнюс", "Europe/Vilnius"),
        ),
    ),
    (
        "Европа",
        (
            ("Лондон", "Europe/London"),
            ("Берлин", "Europe/Berlin"),
            ("Париж", "Europe/Paris"),
            ("Мадрид", "Europe/Madrid"),
            ("Рим", "Europe/Rome"),
            ("Варшава", "Europe/Warsaw"),
            ("Прага", "Europe/Prague"),
            ("Афины", "Europe/Athens"),
            ("Стамбул", "Europe/Istanbul"),
        ),
    ),
    (
        "Мир",
        (
            ("Нью-Йорк", "America/New_York"),
            ("Чикаго", "America/Chicago"),
            ("Лос-Анджелес", "America/Los_Angeles"),
            ("Дубай", "Asia/Dubai"),
            ("Тель-Авив", "Asia/Jerusalem"),
            ("Дели", "Asia/Kolkata"),
            ("Бангкок", "Asia/Bangkok"),
            ("Сингапур", "Asia/Singapore"),
            ("Токио", "Asia/Tokyo"),
            ("Сидней", "Australia/Sydney"),
        ),
    ),
)


def zone_label(iana: str) -> str:
    """Friendly Russian label for a curated zone, otherwise the IANA name itself."""
    for _, zones in POPULAR_ZONES:
        for label, name in zones:
            if name == iana:
                return label
    return iana
