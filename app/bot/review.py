"""Картка ревʼю: показ поста адміну і всі кнопки під ним."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InputMediaAnimation,
    InputMediaPhoto,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.generator import Generator
from app.bot.keyboards import ReviewCb, approved_keyboard, cancel_keyboard, review_keyboard
from app.bot.states import EditPost
from app.config import settings
from app.content import build_caption, build_review_note
from app.db import settings_store as store
from app.db.base import session_scope
from app.db.models import GifCandidate, Post, PostStatus
from app.pipeline import attach_media, load_post, regenerate_text, swap_media
from app.publisher import publish
from app.slots import format_local, next_free_slot, parse_slot, tz

log = logging.getLogger(__name__)

router = Router(name="review")
router.callback_query.filter(F.from_user.id.in_(settings.admins))
router.message.filter(F.from_user.id.in_(settings.admins))

_generator: Generator | None = None


def generator() -> Generator:
    global _generator
    if _generator is None:
        _generator = Generator()
    return _generator


# ----------------------------------------------------------------- показ картки


def card_text(post: Post) -> str:
    return f"{build_caption(post)}\n\n<code>{build_review_note(post)}</code>"


# GIPHY віддає .gif або .mp4 (H.264 без звуку) — обидва Telegram показує як
# анімацію через send_animation. Резервна картка від render_card — .png.
def _is_animation(path: Path) -> bool:
    return path.suffix.lower() in {".gif", ".mp4"}


async def send_card(bot: Bot, post: Post, chat_id: int | None = None) -> None:
    """Надсилає нову картку. Стару, якщо була, прибирає."""
    target = chat_id or post.review_chat_id or (settings.admins[0] if settings.admins else None)
    if target is None:
        log.warning("ADMIN_IDS порожній — нема кому слати пост #%s", post.id)
        return

    await _drop_card(bot, post)

    keyboard = (
        approved_keyboard(post.id)
        if post.status == PostStatus.approved
        else review_keyboard(post.id, has_card=bool(post.card_path))
    )
    card = Path(post.card_path) if post.card_path else None

    if card and card.exists() and _is_animation(card):
        message = await bot.send_animation(
            chat_id=target,
            animation=FSInputFile(card),
            caption=card_text(post),
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    elif card and card.exists():
        message = await bot.send_photo(
            chat_id=target,
            photo=FSInputFile(card),
            caption=card_text(post),
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    else:
        message = await bot.send_message(
            chat_id=target,
            text=card_text(post) + "\n\n⚠️ <i>Картки поки немає</i>",
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    post.review_chat_id = target
    post.review_message_id = message.message_id


async def refresh_card(bot: Bot, post: Post, new_image: bool = False) -> None:
    """Оновлює вже надіслану картку, не засмічуючи чат новими повідомленнями."""
    if not (post.review_chat_id and post.review_message_id):
        await send_card(bot, post)
        return

    keyboard = (
        approved_keyboard(post.id)
        if post.status == PostStatus.approved
        else review_keyboard(post.id, has_card=bool(post.card_path))
    )
    card = Path(post.card_path) if post.card_path else None

    try:
        if new_image and card and card.exists() and _is_animation(card):
            await bot.edit_message_media(
                chat_id=post.review_chat_id,
                message_id=post.review_message_id,
                media=InputMediaAnimation(
                    media=FSInputFile(card), caption=card_text(post), parse_mode="HTML"
                ),
                reply_markup=keyboard,
            )
        elif new_image and card and card.exists():
            await bot.edit_message_media(
                chat_id=post.review_chat_id,
                message_id=post.review_message_id,
                media=InputMediaPhoto(
                    media=FSInputFile(card), caption=card_text(post), parse_mode="HTML"
                ),
                reply_markup=keyboard,
            )
        elif card and card.exists():
            await bot.edit_message_caption(
                chat_id=post.review_chat_id,
                message_id=post.review_message_id,
                caption=card_text(post),
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        else:
            await bot.edit_message_text(
                chat_id=post.review_chat_id,
                message_id=post.review_message_id,
                text=card_text(post) + "\n\n⚠️ <i>Картки поки немає</i>",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
    except Exception as exc:  # noqa: BLE001
        # Тип повідомлення змінився (текст ↔ фото) або воно застаріле — шлемо заново
        log.info("Картку #%s не вдалось оновити (%s), надсилаю нову", post.id, str(exc)[:80])
        await send_card(bot, post)


async def _drop_card(bot: Bot, post: Post) -> None:
    if post.review_chat_id and post.review_message_id:
        try:
            await bot.delete_message(post.review_chat_id, post.review_message_id)
        except Exception:  # noqa: BLE001 — повідомлення могли вже прибрати вручну
            pass


# -------------------------------------------------------------------- кнопки


@router.callback_query(ReviewCb.filter(F.action == "approve"))
async def on_approve(query: CallbackQuery, callback_data: ReviewCb, bot: Bot) -> None:
    async with session_scope() as session:
        post = await load_post(session, callback_data.post_id)
        if post is None:
            await query.answer("Пост зник", show_alert=True)
            return

        slot = await _first_free_slot(session)
        if slot is None:
            await query.answer("Немає жодного слота. Додай його в /schedule", show_alert=True)
            return

        post.status = PostStatus.approved
        post.scheduled_at = slot
        await session.flush()
        await refresh_card(bot, post)
        await query.answer(f"Заплановано на {format_local(slot)}")


@router.callback_query(ReviewCb.filter(F.action == "unapprove"))
async def on_unapprove(query: CallbackQuery, callback_data: ReviewCb, bot: Bot) -> None:
    async with session_scope() as session:
        post = await load_post(session, callback_data.post_id)
        if post is None:
            await query.answer("Пост зник", show_alert=True)
            return
        post.status = PostStatus.pending_review
        post.scheduled_at = None
        await session.flush()
        await refresh_card(bot, post)
        await query.answer("Повернув на ревʼю")


@router.callback_query(ReviewCb.filter(F.action == "reject"))
async def on_reject(query: CallbackQuery, callback_data: ReviewCb, bot: Bot) -> None:
    async with session_scope() as session:
        post = await load_post(session, callback_data.post_id)
        if post is None:
            await query.answer("Пост зник", show_alert=True)
            return
        post.status = PostStatus.rejected
        post.scheduled_at = None
        if post.card_path:
            Path(post.card_path).unlink(missing_ok=True)
            post.card_path = None
        await _drop_card(bot, post)
        post.review_message_id = None
        await session.flush()
    await query.answer("Відхилено")


@router.callback_query(ReviewCb.filter(F.action == "snooze"))
async def on_snooze(query: CallbackQuery, bot: Bot) -> None:
    await query.answer("Відклав — картка лишиться в черзі")


@router.callback_query(ReviewCb.filter(F.action == "now"))
async def on_now(query: CallbackQuery, callback_data: ReviewCb, bot: Bot) -> None:
    await query.answer("Публікую…")
    async with session_scope() as session:
        post = await load_post(session, callback_data.post_id)
        if post is None:
            return
        ok = await publish(bot, session, post)
        await session.flush()
        if ok:
            await _drop_card(bot, post)
            post.review_message_id = None
            await query.message.answer(f"✅ Пост #{post.id} у каналі")
        else:
            await query.message.answer(f"❌ Не вийшло: {post.last_error}")


@router.callback_query(ReviewCb.filter(F.action == "swap"))
async def on_swap(query: CallbackQuery, callback_data: ReviewCb, bot: Bot) -> None:
    await query.answer("Шукаю іншу гіфку…")
    async with session_scope() as session:
        post = await load_post(session, callback_data.post_id)
        if post is None:
            return
        if await swap_media(session, post):
            await session.flush()
            await refresh_card(bot, post, new_image=True)
        else:
            await query.message.answer("Інших придатних гіфок не знайшов.")


@router.callback_query(ReviewCb.filter(F.action == "retry"))
async def on_retry(query: CallbackQuery, callback_data: ReviewCb, bot: Bot) -> None:
    await query.answer("Пробую ще раз…")
    async with session_scope() as session:
        post = await load_post(session, callback_data.post_id)
        if post is None:
            return
        await attach_media(session, post)
        await session.flush()
        await refresh_card(bot, post, new_image=True)


@router.callback_query(ReviewCb.filter(F.action == "cands"))
async def on_candidates(query: CallbackQuery, callback_data: ReviewCb) -> None:
    async with session_scope() as session:
        rows = await session.execute(
            select(GifCandidate)
            .where(GifCandidate.post_id == callback_data.post_id)
            .order_by(GifCandidate.rank.desc(), GifCandidate.id)
        )
        candidates = list(rows.scalars().all())

    if not candidates:
        await query.answer("Кандидатів ще нема", show_alert=True)
        return

    lines = ["<b>Знайдені гіфки</b>"]
    for item in candidates[:10]:
        mark = "✔️" if item.tried else "▫️"
        label = item.title or item.gif_id
        lines.append(f'{mark} <a href="{item.page_url}">{label}</a> · rank {item.rank:g}')
    await query.message.answer("\n".join(lines), parse_mode="HTML")
    await query.answer()


# --------------------------------------------------------- редагування тексту


@router.callback_query(ReviewCb.filter(F.action == "edit"))
async def on_edit(query: CallbackQuery, callback_data: ReviewCb, state: FSMContext) -> None:
    async with session_scope() as session:
        post = await load_post(session, callback_data.post_id)
        if post is None:
            await query.answer("Пост зник", show_alert=True)
            return
        current = build_caption(post)

    await state.set_state(EditPost.caption)
    await state.update_data(post_id=callback_data.post_id)
    await query.message.answer(
        "Надішли новий підпис. Поточний — нижче, зручно скопіювати й правити:",
        reply_markup=cancel_keyboard(),
    )
    await query.message.answer(f"<code>{current}</code>", parse_mode="HTML")
    await query.answer()


@router.message(EditPost.caption)
async def save_caption(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    async with session_scope() as session:
        post = await load_post(session, data["post_id"])
        if post is None:
            await message.answer("Пост зник")
            return
        post.caption_override = message.html_text
        await session.flush()
        await refresh_card(bot, post)
    await message.answer("✏️ Текст оновлено")


@router.callback_query(ReviewCb.filter(F.action == "regen"))
async def on_regen(query: CallbackQuery, callback_data: ReviewCb, state: FSMContext) -> None:
    await state.set_state(EditPost.regen_comment)
    await state.update_data(post_id=callback_data.post_id)
    await query.message.answer(
        "Що змінити? Напиши побажання (наприклад «простіше» або «інший приклад»).\n"
        "Або надішли <code>-</code>, щоб просто перегенерувати.",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await query.answer()


@router.message(EditPost.regen_comment)
async def do_regen(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    instruction = None if message.text.strip() in {"-", "—"} else message.text.strip()

    notice = await message.answer("🔄 Генерую…")
    async with session_scope() as session:
        post = await load_post(session, data["post_id"])
        if post is None:
            await notice.edit_text("Пост зник")
            return
        try:
            await regenerate_text(session, post, generator(), instruction)
        except Exception as exc:  # noqa: BLE001
            await notice.edit_text(f"AI не відповів: {exc}")
            return
        await session.flush()
        await refresh_card(bot, post)
    await notice.edit_text("✅ Текст перегенеровано")


# ------------------------------------------------------------- перенесення часу


@router.callback_query(ReviewCb.filter(F.action == "reschedule"))
async def on_reschedule(query: CallbackQuery, callback_data: ReviewCb, state: FSMContext) -> None:
    await state.set_state(EditPost.reschedule)
    await state.update_data(post_id=callback_data.post_id)
    await query.message.answer(
        "Коли опублікувати? Формат <code>ДД.ММ ГГ:ХХ</code> або просто <code>ГГ:ХХ</code> "
        "(найближчий такий час).",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await query.answer()


@router.message(EditPost.reschedule)
async def do_reschedule(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    moment = _parse_when(message.text or "")
    if moment is None:
        await message.answer("Не зрозумів час. Спробуй <code>18.09 10:00</code>", parse_mode="HTML")
        return

    await state.clear()
    async with session_scope() as session:
        post = await load_post(session, data["post_id"])
        if post is None:
            await message.answer("Пост зник")
            return
        post.scheduled_at = moment
        post.status = PostStatus.approved
        await session.flush()
        await refresh_card(bot, post)
    await message.answer(f"🕑 Заплановано на {format_local(moment)}")


def _parse_when(raw: str) -> datetime | None:
    """«18.09 10:00» або «10:00» у локальному часі каналу → UTC."""
    raw = raw.strip()
    zone = tz()
    now_local = datetime.now(timezone.utc).astimezone(zone)

    if " " in raw:
        day_part, time_part = raw.split(maxsplit=1)
        slot = parse_slot(time_part)
        if slot is None:
            return None
        try:
            day, month = (int(x) for x in day_part.replace("/", ".").split("."))
        except ValueError:
            return None
        year = now_local.year + (1 if month < now_local.month else 0)
        try:
            local = datetime(year, month, day, slot.hour, slot.minute, tzinfo=zone)
        except ValueError:
            return None
        return local.astimezone(timezone.utc)

    slot = parse_slot(raw)
    if slot is None:
        return None
    local = now_local.replace(hour=slot.hour, minute=slot.minute, second=0, microsecond=0)
    if local <= now_local:
        local += timedelta(days=1)
    return local.astimezone(timezone.utc)


async def _first_free_slot(session: AsyncSession) -> datetime | None:
    slots = await store.get(session, "post_slots")
    rows = await session.execute(
        select(Post.scheduled_at).where(Post.status == PostStatus.approved)
    )
    return next_free_slot(list(slots), [t for t in rows.scalars().all() if t])
