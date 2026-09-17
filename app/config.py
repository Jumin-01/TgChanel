"""Конфігурація з .env. Усе, що змінюється рідко й потребує рестарту.

Те, що налаштовується на льоту (слоти публікації, розмір черги, пауза),
живе в БД — див. app/db/settings_store.py.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Telegram ----
    bot_token: str = ""
    channel_id: str = ""
    admin_ids: str = ""
    tz: str = "Europe/Kyiv"

    # ---- AI ----
    gemini_api_key: str = ""
    ai_model: str = "gemini-3.8-flash"
    # Запасні моделі: Gemini на безкоштовному тарифі часто віддає 503 по
    # конкретній моделі, тоді сусідня в ту саму хвилину працює
    ai_model_fallbacks: str = "gemini-3.6-flash,gemini-3.5-flash"

    # ---- Гіфка (GIPHY) ----
    giphy_api_key: str = ""

    # ---- Поведінка ----
    queue_target: int = 3
    log_level: str = "INFO"

    @property
    def ai_fallbacks(self) -> list[str]:
        return [m.strip() for m in self.ai_model_fallbacks.split(",") if m.strip()]

    @property
    def admins(self) -> list[int]:
        return [int(x) for x in re.split(r"[,\s]+", self.admin_ids.strip()) if x]

    @property
    def data_dir(self) -> Path:
        p = BASE_DIR / "data"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def media_dir(self) -> Path:
        p = self.data_dir / "media"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{(self.data_dir / 'channel.db').as_posix()}"

    @property
    def font_path(self) -> Path:
        return BASE_DIR / "assets" / "fonts" / "DejaVuSans-Bold.ttf"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
