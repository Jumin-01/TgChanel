"""Оркестрація: фраза → текст поста → гіфка → черга на ревʼю."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.generator import Generator
from app.config import settings
from app.db.models import GifCandidate, Phrase, PhraseKind, Post, PostStatus
from app.media.giphy import GifHit, GiphySource, download
from app.media.render import RenderError, render_card

log = logging.getLogger(__name__)

# Скільки кандидатів пробуємо, поки не отримаємо файл, що реально завантажився
MEDIA_ATTEMPTS = 3


async def used_phrases(session: AsyncSession) -> list[str]:
    rows = await session.execute(select(Phrase.text).order_by(Phrase.created_at))
    return list(rows.scalars().all())


async def create_draft(
    session: AsyncSession,
    generator: Generator,
    phrase_text: str | None = None,
    kind: str = "idiom",
    level: str = "B1",
) -> Post | None:
    """Нова чернетка. phrase_text задають вручну через /add, інакше пропонує AI."""
    origin = "manual" if phrase_text else "ai"

    if phrase_text is None:
        suggestions = await generator.suggest_phrases(await used_phrases(session), n=1)
        if not suggestions:
            log.warning("AI не дав жодної нової фрази")
            return None
        suggestion = suggestions[0]
        phrase_text, kind, level = suggestion.phrase, suggestion.kind, suggestion.level

    phrase_text = phrase_text.strip().lower()
    if await session.scalar(select(Phrase).where(Phrase.text == phrase_text)):
        log.info("Фраза «%s» вже була — пропускаю", phrase_text)
        return None

    phrase = Phrase(text=phrase_text, kind=PhraseKind(kind), level=level, origin=origin)
    session.add(phrase)
    await session.flush()

    content = await generator.build_post(phrase_text, kind, level)
    post = Post(
        # Через phrase= (а не phrase_id=) звʼязок одразу є в памʼяті: без
        # цього post.phrase у виклику з того самого post-обʼєкта без
        # проміжного load_post() падає з MissingGreenlet — лінивий лоад
        # у async-сесії неможливий без явного await.
        phrase=phrase,
        headline=content.headline,
        meaning_uk=content.meaning_uk,
        example_en=content.example_en,
        example_uk=content.example_uk,
        gif_query=content.gif_query,
        status=PostStatus.draft,
        ai_model=generator.model,
    )
    session.add(post)
    await session.flush()
    log.info("Чернетка #%s: «%s» (gif_query=%r)", post.id, phrase_text, content.gif_query)
    return post


async def regenerate_text(
    session: AsyncSession,
    post: Post,
    generator: Generator,
    instruction: str | None = None,
) -> Post:
    """Перегенерувати текст, лишивши гіфку на місці."""
    content = await generator.build_post(
        post.phrase.text, post.phrase.kind.value, post.phrase.level, instruction
    )
    post.headline = content.headline
    post.meaning_uk = content.meaning_uk
    post.example_en = content.example_en
    post.example_uk = content.example_uk
    post.gif_query = content.gif_query
    post.caption_override = None  # ручні правки скидаються разом із текстом
    return post


async def collect_candidates(session: AsyncSession, post: Post) -> int:
    """Пошук гіфок за gif_query, складання їх у чергу кандидатів."""
    query = post.gif_query or post.phrase.text
    hits = await GiphySource().find(query, limit=8)

    known = {c.gif_id for c in await _candidates(session, post.id)}
    added = 0
    for hit in hits:
        if hit.gif_id in known:
            continue
        session.add(
            GifCandidate(
                post_id=post.id,
                gif_id=hit.gif_id,
                media_url=hit.media_url,
                page_url=hit.page_url,
                title=hit.title[:300],
                rank=hit.rank,
            )
        )
        added += 1
    await session.flush()
    log.info("Пост #%s: додано %d кандидатів-гіфок", post.id, added)
    return added


async def attach_media(session: AsyncSession, post: Post) -> bool:
    """Взяти найкращого невипробуваного кандидата й завантажити гіфку."""
    if not await _candidates(session, post.id):
        await collect_candidates(session, post)

    for _ in range(MEDIA_ATTEMPTS):
        candidate = await _next_candidate(session, post.id)
        if candidate is None:
            break
        candidate.tried = True
        await session.flush()
        if await _build_card(post, candidate):
            post.status = PostStatus.pending_review
            post.last_error = None
            return True

    # GIPHY нічого не дав (порожній результат, ключ не працює, мережа впала) —
    # пост однаково має вийти, нехай і з простою текстовою карткою замість гіфки.
    if _build_fallback_card(post):
        post.status = PostStatus.pending_review
        post.last_error = "Гіфку не знайшли — картка текстова"
        return True

    post.status = PostStatus.awaiting_media
    post.attempts += 1
    post.last_error = post.last_error or "GIPHY не дав придатної гіфки"
    log.warning("Пост #%s лишився без гіфки", post.id)
    return False


async def swap_media(session: AsyncSession, post: Post) -> bool:
    """Кнопка «Інша гіфка»: наступний кандидат замість поточного."""
    candidate = await _next_candidate(session, post.id)
    if candidate is None:
        if await collect_candidates(session, post) == 0:
            return False
        candidate = await _next_candidate(session, post.id)
        if candidate is None:
            return False

    candidate.tried = True
    await session.flush()
    return await _build_card(post, candidate)


async def _build_card(post: Post, candidate: GifCandidate) -> bool:
    """Завантажити файл гіфки-кандидата. Немає гіфки — картка-фолбек ставиться окремо."""
    hit = GifHit(candidate.gif_id, candidate.media_url, candidate.page_url, candidate.title)
    downloaded = await download(hit, settings.media_dir / "raw")
    if not downloaded:
        return False

    final = settings.media_dir / f"post{post.id}_{candidate.gif_id}{downloaded.suffix}"
    downloaded.replace(final)

    _drop_old_card(post.card_path, final)
    post.gif_id = candidate.gif_id
    post.gif_page_url = candidate.page_url
    post.card_path = str(final)
    return True


def _build_fallback_card(post: Post) -> bool:
    dest = settings.media_dir / f"post{post.id}_fallback.png"
    try:
        render_card(dest, post.headline or post.phrase.text, post.phrase.kind.value, post.phrase.level)
    except RenderError as exc:
        log.warning("Резервна картка для #%s теж не вийшла: %s", post.id, exc)
        return False
    _drop_old_card(post.card_path, dest)
    post.card_path = str(dest)
    return True


def _drop_old_card(previous: str | None, current: Path) -> None:
    if previous and Path(previous) != current:
        Path(previous).unlink(missing_ok=True)


async def _candidates(session: AsyncSession, post_id: int) -> list[GifCandidate]:
    rows = await session.execute(
        select(GifCandidate).where(GifCandidate.post_id == post_id)
    )
    return list(rows.scalars().all())


async def _next_candidate(session: AsyncSession, post_id: int) -> GifCandidate | None:
    return await session.scalar(
        select(GifCandidate)
        .where(GifCandidate.post_id == post_id, GifCandidate.tried.is_(False))
        .order_by(GifCandidate.rank.desc(), GifCandidate.id)
        .limit(1)
    )


async def load_post(session: AsyncSession, post_id: int) -> Post | None:
    return await session.scalar(
        select(Post).where(Post.id == post_id).options(selectinload(Post.phrase))
    )
