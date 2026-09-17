"""Фонові задачі: наповнення черги, публікація за слотами, нагадування."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select

from app.ai.generator import Generator
from app.bot.review import send_card
from app.config import settings
from app.db import settings_store as store
from app.db.base import session_scope
from app.db.models import Post, PostStatus
from app.pipeline import attach_media, create_draft, load_post
from app.publisher import publish
from app.slots import format_local, upcoming_slots

log = logging.getLogger(__name__)

REFILL_MINUTES = 60
RETRY_HOURS = 6
MAX_MEDIA_ATTEMPTS = 4
# За скільки до слота попереджати, що публікувати нічого
REMINDER_LEAD = timedelta(hours=1)
# Як часто можна повторювати той самий сигнал про збій
ALERT_COOLDOWN = timedelta(hours=6)


async def notify_admins(bot: Bot, text: str, dedup_key: str | None = None) -> None:
    """Повідомляє адмінів про збій.

    Фонова задача крутиться щогодини, тож без захисту від повторів одна й та сама
    проблема (скінчились кредити, впав ключ) засипала б приват копіями.
    """
    key = dedup_key or text[:200]
    async with session_scope() as session:
        previous = await store.get(session, "last_alert")
        now = datetime.now(timezone.utc).timestamp()
        if previous and previous.get("key") == key:
            if now - float(previous.get("at", 0)) < ALERT_COOLDOWN.total_seconds():
                return
        await store.set_(session, "last_alert", {"key": key, "at": now})

    for admin in settings.admins:
        try:
            await bot.send_message(admin, text, parse_mode="HTML")
        except Exception:  # noqa: BLE001
            log.warning("Не вдалось попередити адміна %s", admin)


async def refill_queue(bot: Bot) -> None:
    """Добирає пости, поки на ревʼю не набереться потрібна кількість."""
    async with session_scope() as session:
        if await store.get(session, "paused"):
            return
        target = int(await store.get(session, "queue_target"))
        current = await session.scalar(
            select(func.count(Post.id)).where(Post.status == PostStatus.pending_review)
        )
        missing = max(0, target - (current or 0))

    if not missing:
        return
    log.info("Черга: %s із %s, генерую %s", current, target, missing)

    generator = Generator()
    for _ in range(missing):
        async with session_scope() as session:
            try:
                post = await create_draft(session, generator)
            except Exception as exc:  # noqa: BLE001
                log.exception("Не вдалося згенерувати чернетку")
                await notify_admins(bot, f"⚠️ Генерація постів зупинилась:\n<code>{exc}</code>")
                return
            if post is None:
                continue
            await session.flush()
            post_id = post.id

        async with session_scope() as session:
            post = await load_post(session, post_id)
            await attach_media(session, post)
            await session.flush()
            if post.status == PostStatus.pending_review:
                await send_card(bot, post)
                await session.flush()


async def publish_due(bot: Bot) -> None:
    """Публікує все схвалене, чий слот настав."""
    now = datetime.now(timezone.utc)
    async with session_scope() as session:
        if await store.get(session, "paused"):
            return
        rows = await session.execute(
            select(Post.id)
            .where(Post.status == PostStatus.approved, Post.scheduled_at <= now)
            .order_by(Post.scheduled_at)
        )
        due = list(rows.scalars().all())

    for post_id in due:
        async with session_scope() as session:
            post = await load_post(session, post_id)
            if post is None or post.status != PostStatus.approved:
                continue
            ok = await publish(bot, session, post)
            await session.flush()
            if ok and post.review_chat_id and post.review_message_id:
                try:
                    await bot.delete_message(post.review_chat_id, post.review_message_id)
                except Exception:  # noqa: BLE001
                    pass
                post.review_message_id = None
                await session.flush()


async def retry_failed(bot: Bot) -> None:
    """Повертається до постів, які лишилися без гіфки."""
    async with session_scope() as session:
        if await store.get(session, "paused"):
            return
        rows = await session.execute(
            select(Post.id)
            .where(
                Post.status == PostStatus.awaiting_media,
                Post.attempts < MAX_MEDIA_ATTEMPTS,
            )
            .order_by(Post.id)
            .limit(3)
        )
        stuck = list(rows.scalars().all())

    for post_id in stuck:
        async with session_scope() as session:
            post = await load_post(session, post_id)
            if post is None:
                continue
            log.info("Повторна спроба гіфки для #%s (спроба %s)", post.id, post.attempts + 1)
            await attach_media(session, post)
            await session.flush()
            if post.status == PostStatus.pending_review:
                await send_card(bot, post)
                await session.flush()


async def remind_admin(bot: Bot) -> None:
    """За годину до слота попереджає, якщо публікувати нічого."""
    if not settings.admins:
        return

    async with session_scope() as session:
        if await store.get(session, "paused"):
            return
        slots = await store.get(session, "post_slots")
        next_slot = next(upcoming_slots(list(slots)), None)
        if next_slot is None:
            return

        left = next_slot - datetime.now(timezone.utc)
        if not (timedelta(0) < left <= REMINDER_LEAD):
            return

        approved = await session.scalar(
            select(func.count(Post.id)).where(
                Post.status == PostStatus.approved, Post.scheduled_at <= next_slot
            )
        )
        if approved:
            return
        pending = await session.scalar(
            select(func.count(Post.id)).where(Post.status == PostStatus.pending_review)
        )

    text = (
        f"⏰ Через годину слот ({format_local(next_slot)}), а схвалених постів немає.\n"
        + (f"На ревʼю чекає {pending} — /queue" if pending else "Черга порожня.")
    )
    for admin in settings.admins:
        try:
            await bot.send_message(admin, text)
        except Exception:  # noqa: BLE001
            log.warning("Не вдалось нагадати адміну %s", admin)


def build_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.tz)
    scheduler.add_job(publish_due, "interval", minutes=1, args=[bot], id="publish_due")
    scheduler.add_job(
        refill_queue, "interval", minutes=REFILL_MINUTES, args=[bot], id="refill_queue"
    )
    scheduler.add_job(retry_failed, "interval", hours=RETRY_HOURS, args=[bot], id="retry_failed")
    scheduler.add_job(remind_admin, "interval", minutes=10, args=[bot], id="remind_admin")
    return scheduler
