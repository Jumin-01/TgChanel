"""Складання підпису поста. Один і той самий текст іде і в ревʼю, і в канал."""

from __future__ import annotations

import html

from app.db.models import Post

MAX_CAPTION = 1024  # ліміт Telegram для підпису до фото/анімації


def _esc(text: str) -> str:
    # Telegram у HTML-режимі вимагає екранувати лише < > &; лапки лишаємо як є
    return html.escape((text or "").strip(), quote=False)


def build_caption(post: Post) -> str:
    """HTML-підпис. Якщо редактор правив текст вручну — віддаємо його як є."""
    if post.caption_override:
        return post.caption_override

    parts: list[str] = [f"🇬🇧 <b>{_esc(post.headline or post.phrase.text)}</b>"]

    if post.meaning_uk:
        parts.append(f"\n🇺🇦 {_esc(post.meaning_uk)}")

    if post.example_en:
        block = f"\n💬 <b>{_esc(post.example_en)}</b>"
        if post.example_uk:
            block += f"\n<i>{_esc(post.example_uk)}</i>"
        parts.append(block)

    caption = "\n".join(parts).strip()
    if len(caption) > MAX_CAPTION:
        caption = caption[: MAX_CAPTION - 1].rsplit("\n", 1)[0] + "…"
    return caption


def build_review_note(post: Post) -> str:
    """Службовий рядок під карткою ревʼю. У канал не потрапляє."""
    bits = [f"#{post.id}", post.phrase.kind.value if post.phrase else "", post.phrase.level if post.phrase else ""]
    if post.gif_id:
        bits.append(f"gif:{post.gif_id}")
    else:
        bits.append("без гіфки")
    if post.scheduled_at:
        bits.append(post.scheduled_at.strftime("%d.%m %H:%M"))
    return " · ".join(b for b in bits if b)
