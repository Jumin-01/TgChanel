"""Схеми структурованого виводу AI. Валідуються SDK через output_format."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["idiom", "collocation", "phrasal_verb"]
Level = Literal["A2", "B1", "B2", "C1"]


class PhraseSuggestion(BaseModel):
    phrase: str = Field(description="The phrase in its base dictionary form, lowercase")
    kind: Kind
    level: Level
    why_useful: str = Field(description="One short line: why a learner needs this")


class PhraseBatch(BaseModel):
    phrases: list[PhraseSuggestion]


class PostContent(BaseModel):
    headline: str = Field(description="The phrase exactly as it should be shown in the post")
    meaning_uk: str = Field(
        description=(
            "Живе пояснення фрази українською, 1-3 речення. Не словникова стаття — "
            "так, ніби пояснюєш другу: образно, з легкою іронією, без мату."
        )
    )
    example_en: str = Field(description="One natural spoken sentence, 8-18 words")
    example_uk: str = Field(description="Природний український переклад прикладу")
    gif_query: str = Field(
        description=(
            "1-3 English words to search a reaction-gif site for a GIF matching the "
            "phrase's mood or action — not the phrase itself, but the feeling/action "
            "it describes (e.g. for 'cut corners': 'lazy shortcut' or 'rushing job')"
        )
    )
