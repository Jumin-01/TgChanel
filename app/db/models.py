"""ORM-моделі. SQLite через aiosqlite, SQLAlchemy 2.0 style."""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Enum as SAEnum,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


def _enum_column(enum_cls: type[enum.Enum], length: int = 20) -> SAEnum:
    """Зберігаємо значення enum рядком, але читаємо назад як enum.

    Без values_callable SQLAlchemy пише в SQLite ІМЕНА членів, а голий String
    повертає звичайний рядок, і .value на ньому падає.
    """
    return SAEnum(
        enum_cls,
        values_callable=lambda cls: [member.value for member in cls],
        native_enum=False,
        length=length,
    )


class PostStatus(str, enum.Enum):
    draft = "draft"                    # текст згенеровано, гіфки ще нема
    awaiting_media = "awaiting_media"  # GIPHY нічого не дав, чекає ретраю
    pending_review = "pending_review"  # чекає на твоє рішення
    approved = "approved"              # схвалено, стоїть у слоті
    published = "published"
    rejected = "rejected"
    failed = "failed"


class PhraseKind(str, enum.Enum):
    idiom = "idiom"
    collocation = "collocation"
    phrasal_verb = "phrasal_verb"


class Phrase(Base):
    """Реєстр використаних фраз. Єдина задача — щоб AI не повторювався."""

    __tablename__ = "phrases"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    kind: Mapped[PhraseKind] = mapped_column(_enum_column(PhraseKind), default=PhraseKind.idiom)
    level: Mapped[str] = mapped_column(String(4), default="B1")
    origin: Mapped[str] = mapped_column(String(10), default="ai")  # ai | manual
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    posts: Mapped[list["Post"]] = relationship(back_populates="phrase")


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    phrase_id: Mapped[int] = mapped_column(ForeignKey("phrases.id"), index=True)
    phrase: Mapped[Phrase] = relationship(back_populates="posts")

    # --- контент від AI ---
    headline: Mapped[str] = mapped_column(String(200), default="")
    # Живе пояснення фрази українською (стиль Hot Idioms, без мату) — єдиний
    # текстовий блок пояснення, без окремого англійського визначення.
    meaning_uk: Mapped[str] = mapped_column(Text, default="")
    example_en: Mapped[str] = mapped_column(Text, default="")
    example_uk: Mapped[str] = mapped_column(Text, default="")
    # якщо ти відредагував підпис вручну — тут повний готовий текст
    caption_override: Mapped[str | None] = mapped_column(Text, default=None)

    # --- гіфка з GIPHY ---
    gif_query: Mapped[str | None] = mapped_column(String(100), default=None)
    gif_id: Mapped[str | None] = mapped_column(String(50), default=None)
    gif_page_url: Mapped[str | None] = mapped_column(String(500), default=None)
    # локальний файл — .mp4 (як анімація в Telegram) або .gif; None = ще не завантажено
    card_path: Mapped[str | None] = mapped_column(String(500), default=None)

    # --- службове ---
    status: Mapped[PostStatus] = mapped_column(
        _enum_column(PostStatus), default=PostStatus.draft, index=True
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    tg_message_id: Mapped[int | None] = mapped_column(Integer, default=None)
    review_chat_id: Mapped[int | None] = mapped_column(Integer, default=None)
    review_message_id: Mapped[int | None] = mapped_column(Integer, default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    ai_model: Mapped[str | None] = mapped_column(String(60), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    candidates: Mapped[list["GifCandidate"]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )


class GifCandidate(Base):
    """Гіфки, знайдені для запиту gif_query. Живлять кнопку «🎬 Інша гіфка»."""

    __tablename__ = "gif_candidates"
    __table_args__ = (UniqueConstraint("post_id", "gif_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), index=True)
    post: Mapped[Post] = relationship(back_populates="candidates")

    gif_id: Mapped[str] = mapped_column(String(50))
    media_url: Mapped[str] = mapped_column(String(500))
    page_url: Mapped[str] = mapped_column(String(500), default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    rank: Mapped[float] = mapped_column(Float, default=0.0)
    tried: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Setting(Base):
    """Налаштування, що правляться з бота. value — JSON-рядок."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
