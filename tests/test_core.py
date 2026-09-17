"""Офлайн-тести чистої логіки: вибір гіфки, слоти, підпис.

Мережі й ключів не потребують: python -m pytest -q
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.media.giphy import _pick_rendition
from app.slots import next_free_slot, normalize_slots, parse_slot

# ------------------------------------------------------------------- GIPHY


def test_pick_rendition_prefers_small_mp4() -> None:
    images = {
        "downsized": {"mp4": "https://x/downsized.mp4", "mp4_size": "500000"},
        "fixed_height": {"mp4": "https://x/fixed.mp4", "mp4_size": "300000"},
        "original": {"mp4": "https://x/original.mp4", "mp4_size": "9000000"},
    }
    url, size = _pick_rendition(images)
    assert url == "https://x/downsized.mp4"
    assert size == 500000.0


def test_pick_rendition_falls_back_to_gif_url() -> None:
    images = {"downsized": {"url": "https://x/downsized.gif", "size": "120000"}}
    url, size = _pick_rendition(images)
    assert url == "https://x/downsized.gif"
    assert size == 120000.0


def test_pick_rendition_empty_images() -> None:
    assert _pick_rendition({}) == (None, None)


# -------------------------------------------------------------------- слоти


def test_parse_and_normalize_slots() -> None:
    assert parse_slot("10:00").hour == 10
    assert parse_slot("сміття") is None
    assert normalize_slots(["19:30", "10:00", "10:00", "nope"]) == ["10:00", "19:30"]


def test_next_free_slot_skips_taken() -> None:
    now = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)  # 11:00 за Києвом
    first = next_free_slot(["10:00", "19:30"], taken=[], after=now)
    second = next_free_slot(["10:00", "19:30"], taken=[first], after=now)
    third = next_free_slot(["10:00", "19:30"], taken=[first, second], after=now)

    assert first < second < third
    assert len({first, second, third}) == 3


def test_next_free_slot_without_slots() -> None:
    assert next_free_slot([], taken=[]) is None


# ------------------------------------------------------------------- підпис


@pytest.mark.asyncio
async def test_caption_contains_all_parts() -> None:
    from app.content import build_caption
    from app.db.models import Phrase, PhraseKind, Post, PostStatus

    phrase = Phrase(text="bite the bullet", kind=PhraseKind.idiom, level="B2")
    post = Post(
        phrase_id=1,
        headline="bite the bullet",
        meaning_uk="Зважитися на неприємне, яке довго відкладав.",
        example_en="I had to bite the bullet and call her.",
        example_uk="Довелося зважитись і подзвонити їй.",
        gif_id="abc123",
        gif_page_url="https://giphy.com/gifs/abc123",
        status=PostStatus.pending_review,
    )
    post.phrase = phrase

    caption = build_caption(post)
    assert "bite the bullet" in caption
    assert "Зважитися на неприємне, яке довго відкладав." in caption
    assert "Powered by GIPHY" not in caption  # прибрано за рішенням користувача
    assert "#" not in caption  # тегів більше нема
    assert len(caption) <= 1024


def test_caption_override_wins() -> None:
    from app.content import build_caption
    from app.db.models import Post

    post = Post(phrase_id=1, headline="x", caption_override="мій власний текст")
    assert build_caption(post) == "мій власний текст"
