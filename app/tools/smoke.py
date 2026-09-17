"""Прогін пайплайна без публікації — щоб побачити результат до запуску бота.

    python -m app.tools.smoke --phrases-only
    python -m app.tools.smoke --phrase "bite the bullet" --find-only
    python -m app.tools.smoke --phrase "bite the bullet" --no-ai
    python -m app.tools.smoke --phrase "bite the bullet"

Нічого нікуди не постить: результат — JSON у консолі й гіфка/картинка у data/clips/.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import warnings

warnings.filterwarnings("ignore")

from app.ai.generator import Generator
from app.config import settings
from app.content import build_caption
from app.db.base import init_db, session_scope
from app.db.models import Phrase, PhraseKind, Post, PostStatus
from app.media.giphy import GiphySource
from app.pipeline import attach_media, create_draft, load_post, used_phrases

log = logging.getLogger(__name__)

# Заглушка, щоб ганяти пошук і рендер без звернень до Gemini
STUB = {
    "headline": "{phrase}",
    "meaning_uk": "Тестове пояснення без звернення до AI (--no-ai).",
    "example_en": "This is a smoke test, so the example is not generated.",
    "example_uk": "Це тестовий прогін, приклад не генерувався.",
    "gif_query": "funny reaction",
}


async def run_phrases_only() -> None:
    async with session_scope() as session:
        used = await used_phrases(session)
    suggestions = await Generator().suggest_phrases(used, n=5)
    for item in suggestions:
        print(f"  {item.phrase:32} {item.kind:14} {item.level}  — {item.why_useful}")


async def run_find_only(query: str) -> None:
    hits = await GiphySource().find(query, limit=8)
    print(f"Кандидатів: {len(hits)}\n")
    for hit in hits:
        kind = "mp4" if hit.is_video else "gif"
        print(f"  [rank {hit.rank:g}, {kind}] {hit.title[:60]}")
        print(f"      {hit.media_url}")
        print(f"      сторінка: {hit.page_url}\n")


async def run_full(phrase: str, use_ai: bool) -> None:
    async with session_scope() as session:
        if use_ai:
            post = await create_draft(session, Generator(), phrase_text=phrase)
            if post is None:
                print("Ця фраза вже є в базі — візьми іншу або почисти data/channel.db")
                return
        else:
            post = await _stub_post(session, phrase)
        await session.flush()
        post_id = post.id

    async with session_scope() as session:
        post = await load_post(session, post_id)
        print("--- підпис ---")
        print(build_caption(post))
        print(f"\n--- шукаю гіфку («{post.gif_query}») ---")
        ok = await attach_media(session, post)
        await session.flush()

        if ok:
            print(f"\n✅ Картка готова: {post.card_path}")
            if post.gif_page_url:
                print(f"   гіфка: {post.gif_page_url}")
            print("\n--- фінальний підпис ---")
            print(build_caption(post))
        else:
            print(f"\n❌ Гіфки нема: {post.last_error}")


async def _stub_post(session, phrase: str) -> Post:
    phrase = phrase.strip().lower()
    row = Phrase(text=phrase, kind=PhraseKind.idiom, level="B1", origin="manual")
    session.add(row)
    await session.flush()
    post = Post(
        phrase=row,
        status=PostStatus.draft,
        **{k: v.format(phrase=phrase) for k, v in STUB.items()},
    )
    session.add(post)
    await session.flush()
    return post


def main() -> None:
    parser = argparse.ArgumentParser(description="Прогін пайплайна без публікації")
    parser.add_argument("--phrase", help="Фраза, з якої робимо пост")
    parser.add_argument("--phrases-only", action="store_true", help="Лише попросити AI фрази")
    parser.add_argument(
        "--find-only", action="store_true", help="Лише пошук гіфок за --phrase (як запит GIPHY)"
    )
    parser.add_argument("--no-ai", action="store_true", help="Без Gemini: текст-заглушка")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    async def runner() -> None:
        await init_db()
        if args.phrases_only:
            await run_phrases_only()
        elif not args.phrase:
            parser.error("потрібен --phrase або --phrases-only")
        elif args.find_only:
            await run_find_only(args.phrase)
        else:
            await run_full(args.phrase, use_ai=not args.no_ai)

    asyncio.run(runner())


if __name__ == "__main__":
    main()
