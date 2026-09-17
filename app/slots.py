"""Розрахунок часу публікації.

Слоти задаються в боті як локальний час каналу ("10:00"), а в БД усе живе в UTC.
Тут одне джерело правди для обох перетворень.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import settings

# Наскільки далеко вперед шукаємо вільне вікно
HORIZON_DAYS = 30


def tz() -> ZoneInfo:
    return ZoneInfo(settings.tz)


def parse_slot(value: str) -> time | None:
    """"10:00" -> time(10, 0). None, якщо формат не той."""
    try:
        hour, minute = value.strip().split(":")
        return time(int(hour), int(minute))
    except (ValueError, AttributeError):
        return None


def normalize_slots(values: list[str]) -> list[str]:
    """Прибирає дублі й сміття, повертає відсортований список."""
    parsed = {slot.strftime("%H:%M") for v in values if (slot := parse_slot(v))}
    return sorted(parsed)


def upcoming_slots(slots: list[str], after: datetime | None = None):
    """Генератор моментів слотів у UTC, починаючи з найближчого після `after`."""
    zone = tz()
    now = (after or datetime.now(timezone.utc)).astimezone(zone)
    times = [t for v in slots if (t := parse_slot(v))]
    if not times:
        return

    for offset in range(HORIZON_DAYS):
        day = (now + timedelta(days=offset)).date()
        for slot_time in sorted(times):
            moment = datetime.combine(day, slot_time, tzinfo=zone)
            if moment > now:
                yield moment.astimezone(timezone.utc)


def next_free_slot(
    slots: list[str], taken: list[datetime], after: datetime | None = None
) -> datetime | None:
    """Найближчий слот, який ще ніхто не зайняв."""
    busy = {t.astimezone(timezone.utc).replace(second=0, microsecond=0) for t in taken if t}
    for moment in upcoming_slots(slots, after):
        if moment.replace(second=0, microsecond=0) not in busy:
            return moment
    return None


def to_local(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz())


def format_local(moment: datetime | None) -> str:
    local = to_local(moment)
    return local.strftime("%d.%m %H:%M") if local else "—"
