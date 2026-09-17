"""Пошук гіфки за змістом/настроєм фрази через GIPHY.

На відміну від старого YouTube-пайплайна (пошук буквального промовляння
фрази в субтитрах), тут шукаємо за ключовим словом настрою/дії, яке підбирає
AI (PostContent.gif_query) — так само, як шукав би людина реакцію-гіфку
вручну. Джерело зовсім інше й набагато надійніше: жодних бот-стін YouTube,
стабільний безкоштовний ліміт, офіційний дозвіл на вбудовування контенту.

Умова використання GIPHY API — видима позначка «Powered by GIPHY»
(app/content.py додає її в підпис автоматично).
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from app.config import settings

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.giphy.com/v1/gifs/search"
# pg-13: без відверто сексуального чи екстремально жорстокого контенту,
# достатньо вільно для реакцій-гіфок
RATING = "pg-13"
TIMEOUT = 15


class GifHit:
    def __init__(self, gif_id: str, media_url: str, page_url: str, title: str, rank: float = 0.0) -> None:
        self.gif_id = gif_id
        self.media_url = media_url  # пряме посилання на файл (mp4 або gif)
        self.page_url = page_url    # сторінка на giphy.com — для атрибуції
        self.title = title
        self.rank = rank

    @property
    def is_video(self) -> bool:
        return self.media_url.endswith(".mp4")


def _pick_rendition(images: dict) -> tuple[str, float] | tuple[None, None]:
    """Найкраща версія файлу: невеликий mp4 (Telegram любить його як анімацію),
    інакше — gif. Порядок — від найкращого компромісу розмір/якість."""
    order = [
        ("downsized", "mp4"),
        ("fixed_height", "mp4"),
        ("downsized", "url"),
        ("fixed_height", "url"),
        ("original", "mp4"),
        ("original", "url"),
    ]
    for rendition, field in order:
        data = images.get(rendition) or {}
        url = data.get(field)
        if url:
            return url, float(data.get("size") or data.get("mp4_size") or 0)
    return None, None


class GiphySource:
    name = "giphy"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.giphy_api_key

    async def find(self, query: str, limit: int = 8) -> list[GifHit]:
        if not self.api_key:
            log.warning("GIPHY_API_KEY порожній — гіфку підібрати нема чим")
            return []

        params = {
            "api_key": self.api_key,
            "q": query,
            "limit": limit,
            "rating": RATING,
            "lang": "en",
        }
        async with httpx.AsyncClient(timeout=TIMEOUT) as http:
            try:
                response = await http.get(SEARCH_URL, params=params)
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                log.warning("GIPHY не відповів на «%s»: %s", query, exc)
                return []

        hits: list[GifHit] = []
        for item in payload.get("data") or []:
            url, size = _pick_rendition(item.get("images") or {})
            if not url:
                continue
            hits.append(
                GifHit(
                    gif_id=str(item.get("id")),
                    media_url=url,
                    page_url=item.get("url", ""),
                    title=item.get("title", "") or query,
                    # Менші файли — швидше качаються й надійніше проходять у Telegram
                    rank=-(size or 0),
                )
            )
        hits.sort(key=lambda h: h.rank, reverse=True)
        log.info("GIPHY «%s»: %d гіфок", query, len(hits))
        return hits


async def download(hit: GifHit, dest_dir: Path) -> Path | None:
    """Качає файл гіфки на диск. Повертає шлях або None, якщо не вийшло."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".mp4" if hit.is_video else ".gif"
    dest = dest_dir / f"{hit.gif_id}{suffix}"

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
        try:
            response = await http.get(hit.media_url)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("Не вдалося завантажити гіфку %s: %s", hit.gif_id, exc)
            return None

    dest.write_bytes(response.content)
    return dest
