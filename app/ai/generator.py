"""Генерація контенту через Gemini (structured outputs)."""

from __future__ import annotations

import asyncio
import logging

from google import genai
from google.genai import errors, types

from app.ai import prompts
from app.ai.schemas import PhraseBatch, PhraseSuggestion, PostContent
from app.config import settings

log = logging.getLogger(__name__)

MAX_TOKENS = 8000
RETRIES = 5
RETRY_BASE_DELAY = 2.0
# 503 «high demand» у Gemini трапляється регулярно й минає сам
RETRYABLE_CODES = {500, 502, 503, 504}


class GenerationError(RuntimeError):
    """Зрозуміла людині причина, чому AI не відповів.

    retryable каже, чи має сенс узяти іншу модель: перевантаження й вичерпана
    квота стосуються конкретної моделі, а битий ключ — ні.
    """

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _explain(exc: Exception, model: str) -> GenerationError:
    """Перекладає типові помилки API в те, що можна показати в боті."""
    if isinstance(exc, errors.APIError):
        code = getattr(exc, "code", None)
        message = str(getattr(exc, "message", exc))
        if code in (401, 403):
            return GenerationError("GEMINI_API_KEY недійсний або без доступу до моделі.")
        if code == 404:
            return GenerationError(f"Модель «{model}» не знайдена — перевір AI_MODEL.", True)
        if code == 429:
            return GenerationError(
                "Вичерпано квоту Gemini. Або зачекай, або підключи білінг у "
                "Google AI Studio.",
                True,
            )
        if code and code >= 500:
            return GenerationError(f"Модель «{model}» перевантажена.", True)
        return GenerationError(message[:300])
    return GenerationError(str(exc)[:300])


class Generator:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.model = model or settings.ai_model
        self.fallbacks = [m for m in settings.ai_fallbacks if m != self.model]
        self.client = genai.Client(api_key=api_key or settings.gemini_api_key)

    async def _generate(self, system: str, user: str, schema: type):
        """Структурована відповідь: спершу основна модель, потім запасні.

        Gemini на безкоштовному тарифі регулярно віддає 503 «high demand», і то
        по конкретній моделі: сусідня в ту саму хвилину відповідає нормально.
        Тому крім ретраїв є ще перебір моделей.
        """
        last: GenerationError | None = None
        for model in [self.model, *self.fallbacks]:
            try:
                return await self._call(model, system, user, schema)
            except GenerationError as exc:
                last = exc
                if not exc.retryable:
                    raise
                log.warning("Модель %s недоступна (%s), пробую наступну", model, exc)
        raise last or GenerationError("Жодна модель не відповіла")

    async def _call(self, model: str, system: str, user: str, schema: type):
        """Виклик однієї моделі з ретраями на 5xx."""
        for attempt in range(RETRIES):
            try:
                response = await self.client.aio.models.generate_content(
                    model=model,
                    contents=user,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        response_mime_type="application/json",
                        response_schema=schema,
                        max_output_tokens=MAX_TOKENS,
                        # Інструментів ми не даємо, тож автовиклик функцій зайвий
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(
                            disable=True
                        ),
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                code = getattr(exc, "code", None)
                if code not in RETRYABLE_CODES or attempt == RETRIES - 1:
                    raise _explain(exc, model) from exc
                delay = RETRY_BASE_DELAY * (2**attempt)
                log.info("Модель %s віддала %s, повтор через %.0f с", model, code, delay)
                await asyncio.sleep(delay)
                continue

            parsed = response.parsed
            if parsed is None:
                # Модель могла впертися в ліміт токенів або спрацював фільтр безпеки
                reason = getattr(response, "prompt_feedback", None)
                raise GenerationError(
                    f"Модель «{model}» повернула відповідь, яку не вдалося розібрати. "
                    f"{reason or ''}".strip(),
                    retryable=True,
                )
            return parsed

        raise GenerationError(f"Модель «{model}» не відповіла", retryable=True)

    async def suggest_phrases(
        self,
        used: list[str],
        n: int = 5,
        kinds: list[str] | None = None,
        levels: list[str] | None = None,
    ) -> list[PhraseSuggestion]:
        """Нові фрази, яких ще не було в каналі."""
        # Беремо лише хвіст історії: повний список з часом роздує промпт.
        recent = used[-400:]
        user = prompts.PHRASES_USER.format(
            n=n,
            used="\n".join(f"- {p}" for p in recent) or "(nothing yet)",
            kinds=", ".join(kinds or ["idiom", "collocation", "phrasal_verb"]),
            levels=", ".join(levels or ["B1", "B2", "C1"]),
        )
        batch: PhraseBatch = await self._generate(prompts.PHRASES_SYSTEM, user, PhraseBatch)
        seen = {p.lower() for p in used}
        fresh = [p for p in batch.phrases if p.phrase.lower().strip() not in seen]
        log.info("AI запропонував %d фраз, нових %d", len(batch.phrases), len(fresh))
        return fresh

    async def build_post(
        self,
        phrase: str,
        kind: str = "idiom",
        level: str = "B1",
        instruction: str | None = None,
    ) -> PostContent:
        """Повний контент поста. instruction — коментар редактора при перегенерації."""
        user = prompts.POST_USER.format(phrase=phrase, kind=kind, level=level)
        if instruction:
            user += prompts.POST_REGENERATE_SUFFIX.format(instruction=instruction)
        return await self._generate(prompts.POST_SYSTEM, user, PostContent)
