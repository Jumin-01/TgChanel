"""Резервна текстова картка.

Основний варіант поста — гіфка з GIPHY (app/media/giphy.py), підібрана за
змістом/настроєм фрази. Ця картка вмикається, тільки коли GIPHY нічого не
знайшов або API недоступний: пост усе одно має вийти, нехай і без гіфки.
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.config import BASE_DIR

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 1080, 1350
MARGIN = 90
PHRASE_WRAP = 16

# Легкий градієнт: два відтінки одного тону, щоб картка не зливалася в
# нечитабельну пляму й не виглядала кричущою в стрічці.
TOP_COLOR = (30, 41, 82)
BOTTOM_COLOR = (79, 44, 107)
TEXT_COLOR = (255, 255, 255)
BADGE_COLOR = (255, 255, 255, 40)

FONT_CANDIDATES = (
    BASE_DIR / "assets" / "fonts" / "DejaVuSans-Bold.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("C:/Windows/Fonts/segoeuib.ttf"),
)
BADGE_FONT_CANDIDATES = (
    BASE_DIR / "assets" / "fonts" / "DejaVuSans.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/segoeui.ttf"),
)


class RenderError(RuntimeError):
    pass


def _first_existing(candidates: tuple[Path, ...]) -> Path:
    for path in candidates:
        if path.exists():
            return path
    raise RenderError(
        "Не знайдено жодного шрифту. Поклади DejaVuSans(-Bold).ttf у assets/fonts/."
    )


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def _gradient_background() -> Image.Image:
    base = Image.new("RGB", (1, HEIGHT), TOP_COLOR)
    top, bottom = TOP_COLOR, BOTTOM_COLOR
    for y in range(HEIGHT):
        t = y / max(HEIGHT - 1, 1)
        pixel = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        base.putpixel((0, y), pixel)
    return base.resize((WIDTH, HEIGHT))


def _paste_badge(base: Image.Image, text: str, font: ImageFont.FreeTypeFont, y: int) -> None:
    draw = ImageDraw.Draw(base)
    box = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = box[2] - box[0], box[3] - box[1]
    pad_x, pad_y = 34, 16
    w, h = text_w + pad_x * 2, text_h + pad_y * 2
    x = (WIDTH - w) // 2

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=BADGE_COLOR)
    base.paste(overlay, (x, y), overlay)

    draw.text((x + pad_x, y + pad_y - box[1]), text, font=font, fill=TEXT_COLOR)


def _draw_centered_lines(
    draw: ImageDraw.ImageDraw, lines: list[str], font: ImageFont.FreeTypeFont, center_y: int, gap: int
) -> None:
    heights = [draw.textbbox((0, 0), line, font=font)[3] for line in lines]
    total_h = sum(heights) + gap * (len(lines) - 1)
    y = center_y - total_h // 2
    for line, h in zip(lines, heights):
        box = draw.textbbox((0, 0), line, font=font)
        w = box[2] - box[0]
        draw.text(((WIDTH - w) // 2, y - box[1]), line, font=font, fill=TEXT_COLOR)
        y += h + gap


def _fit_phrase(
    draw: ImageDraw.ImageDraw, bold: Path, phrase: str, start_size: int, wrap: int, max_width: int, min_size: int
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Підбирає кегль і перенос рядків, щоб фраза влізла в max_width."""
    size = start_size
    while True:
        font = _font(bold, size)
        lines = textwrap.wrap(phrase.strip(), width=wrap)[:3]
        widest = max((draw.textbbox((0, 0), line, font=font)[2] for line in lines), default=0)
        if widest <= max_width or size <= min_size:
            return font, lines
        size -= 6


def render_card(dest: Path, phrase: str, kind: str, level: str) -> Path:
    """Фраза на градієнтному тлі — коли гіфки нема."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    bold = _first_existing(FONT_CANDIDATES)
    regular = _first_existing(BADGE_FONT_CANDIDATES)

    image = _gradient_background().convert("RGBA")
    draw = ImageDraw.Draw(image)

    _paste_badge(image, f"{kind.upper()} · {level}", _font(regular, 34), y=140)
    phrase_font, lines = _fit_phrase(draw, bold, phrase, 108, PHRASE_WRAP, WIDTH - MARGIN * 2, 48)
    _draw_centered_lines(draw, lines, phrase_font, center_y=HEIGHT // 2, gap=18)

    try:
        image.convert("RGB").save(dest, "PNG", optimize=True)
    except Exception as exc:  # noqa: BLE001
        raise RenderError(f"Не вдалося зберегти картку: {exc}") from exc
    log.info("Картку %s згенеровано (%.1f КБ)", dest.name, dest.stat().st_size / 1024)
    return dest
