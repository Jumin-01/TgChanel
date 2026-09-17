"""Налаштування, які правляться з бота на льоту (без рестарту контейнера)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as env
from app.db.models import Setting

DEFAULTS: dict[str, Any] = {
    "post_slots": ["10:00"],   # локальний час каналу (env.tz)
    "queue_target": env.queue_target,
    "paused": False,
    "level_mix": ["B1", "B2", "C1"],
    "kind_mix": ["idiom", "collocation", "phrasal_verb"],
}


async def get(session: AsyncSession, key: str) -> Any:
    row = await session.get(Setting, key)
    if row is None:
        return DEFAULTS.get(key)
    try:
        return json.loads(row.value)
    except json.JSONDecodeError:
        return DEFAULTS.get(key)


async def set_(session: AsyncSession, key: str, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False)
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=payload))
    else:
        row.value = payload


async def all_settings(session: AsyncSession) -> dict[str, Any]:
    result = dict(DEFAULTS)
    rows = (await session.execute(select(Setting))).scalars().all()
    for row in rows:
        try:
            result[row.key] = json.loads(row.value)
        except json.JSONDecodeError:
            continue
    return result
