"""Відправка готового поста в канал."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.content import build_caption
from app.db.models import Post, PostStatus

log = logging.getLogger(__name__)


def _is_animation(path: Path) -> bool:
    return path.suffix.lower() in {".gif", ".mp4"}


async def publish(bot: Bot, session: AsyncSession, post: Post) -> bool:
    """Публікує пост. Без картки йде текстом."""
    caption = build_caption(post)
    card = Path(post.card_path) if post.card_path else None

    try:
        if card and card.exists() and _is_animation(card):
            message = await bot.send_animation(
                chat_id=settings.channel_id,
                animation=FSInputFile(card),
                caption=caption,
                parse_mode="HTML",
            )
        elif card and card.exists():
            message = await bot.send_photo(
                chat_id=settings.channel_id,
                photo=FSInputFile(card),
                caption=caption,
                parse_mode="HTML",
            )
        else:
            message = await bot.send_message(
                chat_id=settings.channel_id, text=caption, parse_mode="HTML"
            )
    except Exception as exc:  # noqa: BLE001 — падіння публікації не має гасити воркер
        post.last_error = f"Публікація не вдалась: {exc}"[:500]
        post.status = PostStatus.failed
        log.exception("Пост #%s не опублікувався", post.id)
        return False

    post.status = PostStatus.published
    post.published_at = datetime.now(timezone.utc)
    post.tg_message_id = message.message_id
    post.last_error = None
    log.info("Пост #%s опубліковано (message_id=%s)", post.id, message.message_id)
    return True
