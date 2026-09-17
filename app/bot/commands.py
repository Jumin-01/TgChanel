"""Команди бота: черга, розклад, ручні фрази, статистика."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import BotCommand, CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.keyboards import SettingsCb, schedule_keyboard, slot_delete_keyboard
from app.bot.review import generator, send_card
from app.bot.states import EditSettings
from app.config import settings
from app.db import settings_store as store
from app.db.base import session_scope
from app.db.models import Post, PostStatus
from app.pipeline import attach_media, create_draft, load_post
from app.slots import format_local, next_free_slot, normalize_slots, parse_slot

log = logging.getLogger(__name__)

router = Router(name="commands")
router.message.filter(F.from_user.id.in_(settings.admins))
router.callback_query.filter(F.from_user.id.in_(settings.admins))

# Список для меню команд Telegram (кнопка "Меню" біля поля вводу) — виставляється
# один раз при старті бота через bot.set_my_commands(), див. app/main.py.
BOT_COMMANDS = [
    BotCommand(command="queue", description="Черга: що чекає на рішення"),
    BotCommand(command="random", description="Згенерувати пост із випадковою фразою"),
    BotCommand(command="add", description="Пост із конкретної фрази: /add spill the beans"),
    BotCommand(command="schedule", description="Розклад публікацій"),
    BotCommand(command="stats", description="Статистика"),
    BotCommand(command="pause", description="Пауза / зняття паузи"),
    BotCommand(command="start", description="Довідка"),
]


@router.message(Command("start", "help"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "<b>Автопостинг ідіом</b>\n\n"
        "Я сам вигадую фрази, знаходжу відео, де їх вимовляють, і приношу тобі "
        "готовий пост. У канал нічого не потрапляє без твого ✅.\n\n"
        "/queue — що чекає на рішення\n"
        "/schedule — розклад публікацій\n"
        "/random — згенерувати пост із випадковою фразою\n"
        "/add &lt;фраза&gt; — зробити пост із конкретної фрази\n"
        "/stats — статистика\n"
        "/pause — пауза й зняття паузи",
        parse_mode="HTML",
    )


@router.message(Command("queue"))
async def cmd_queue(message: Message, bot: Bot) -> None:
    async with session_scope() as session:
        pending = await _posts(session, PostStatus.pending_review)
        approved = await _posts(session, PostStatus.approved)
        stuck = await _posts(session, PostStatus.awaiting_media)

        if not (pending or approved or stuck):
            await message.answer("Черга порожня. Наступне поповнення — за розкладом.")
            return

        lines = []
        if approved:
            lines.append("<b>Схвалені</b>")
            lines += [f"  #{p.id} {p.phrase.text} → {format_local(p.scheduled_at)}" for p in approved]
        if pending:
            lines.append("<b>Чекають рішення</b>")
            lines += [f"  #{p.id} {p.phrase.text}" for p in pending]
        if stuck:
            lines.append("<b>Без гіфки</b>")
            lines += [f"  #{p.id} {p.phrase.text}" for p in stuck]
        await message.answer("\n".join(lines), parse_mode="HTML")

        # Картки показуємо лише для тих, що ще не висять у чаті
        for post in pending:
            if not post.review_message_id:
                await send_card(bot, post, chat_id=message.chat.id)
        await session.flush()


@router.message(Command("add"))
async def cmd_add(message: Message, command: CommandObject, bot: Bot) -> None:
    phrase = (command.args or "").strip()
    if not phrase:
        await message.answer("Формат: <code>/add spill the beans</code>", parse_mode="HTML")
        return
    await _generate_post(message, bot, phrase_text=phrase)


@router.message(Command("random"))
async def cmd_random(message: Message, bot: Bot) -> None:
    await _generate_post(message, bot, phrase_text=None)


async def _generate_post(message: Message, bot: Bot, phrase_text: str | None) -> None:
    """Спільна логіка /add і /random: текст → гіфка → картка в чат."""
    notice = await message.answer(
        f"Роблю пост із «{phrase_text}»…" if phrase_text else "Придумую фразу й роблю пост…"
    )
    async with session_scope() as session:
        try:
            post = await create_draft(session, generator(), phrase_text=phrase_text)
        except Exception as exc:  # noqa: BLE001
            await notice.edit_text(f"AI не відповів: {exc}")
            return
        if post is None:
            msg = "Ця фраза вже була в каналі." if phrase_text else "AI не запропонував нової фрази — спробуй ще раз."
            await notice.edit_text(msg)
            return
        await session.flush()
        await notice.edit_text(f"«{post.phrase.text}» — текст готовий, шукаю гіфку…")
        await attach_media(session, post)
        await session.flush()
        post = await load_post(session, post.id)
        await send_card(bot, post, chat_id=message.chat.id)
        await session.flush()
    await notice.delete()


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    async with session_scope() as session:
        rows = await session.execute(select(Post.status, func.count(Post.id)).group_by(Post.status))
        counts = {status: count for status, count in rows.all()}
        conf = await store.all_settings(session)

    labels = {
        PostStatus.published: "опубліковано",
        PostStatus.approved: "схвалено",
        PostStatus.pending_review: "на ревʼю",
        PostStatus.awaiting_media: "без гіфки",
        PostStatus.rejected: "відхилено",
        PostStatus.failed: "помилки",
        PostStatus.draft: "чернетки",
    }
    lines = [f"{label}: <b>{counts.get(status, 0)}</b>" for status, label in labels.items()]
    lines.append(f"\nслоти: {', '.join(conf['post_slots']) or '—'} ({settings.tz})")
    lines.append(f"ціль черги: {conf['queue_target']}")
    lines.append(f"пауза: {'так' if conf['paused'] else 'ні'}")
    await message.answer("\n".join(lines), parse_mode="HTML")


# ------------------------------------------------------------------- розклад


async def schedule_text(session: AsyncSession) -> str:
    conf = await store.all_settings(session)
    pending = len(await _posts(session, PostStatus.pending_review))
    approved = await _posts(session, PostStatus.approved)
    nearest = format_local(approved[0].scheduled_at) if approved else "—"
    return (
        "<b>📅 Розклад публікацій</b>\n"
        f"Слоти: <b>{', '.join(conf['post_slots']) or 'жодного'}</b> ({settings.tz})\n"
        f"Черга: {pending} на ревʼю, {len(approved)} схвалених\n"
        f"Найближча публікація: {nearest}\n"
        f"Ціль черги: {conf['queue_target']}"
        + ("\n\n⏸ <b>Пауза увімкнена</b>" if conf["paused"] else "")
    )


@router.message(Command("schedule"))
async def cmd_schedule(message: Message) -> None:
    async with session_scope() as session:
        text = await schedule_text(session)
        conf = await store.all_settings(session)
    await message.answer(
        text, parse_mode="HTML", reply_markup=schedule_keyboard(conf["post_slots"], conf["paused"])
    )


@router.message(Command("pause"))
async def cmd_pause(message: Message) -> None:
    async with session_scope() as session:
        paused = not await store.get(session, "paused")
        await store.set_(session, "paused", paused)
    await message.answer("⏸ Пауза увімкнена" if paused else "▶️ Працюю далі")


@router.callback_query(SettingsCb.filter(F.action == "schedule_home"))
async def cb_schedule_home(query: CallbackQuery) -> None:
    await _redraw_schedule(query)
    await query.answer()


@router.callback_query(SettingsCb.filter(F.action == "toggle_pause"))
async def cb_toggle_pause(query: CallbackQuery) -> None:
    async with session_scope() as session:
        paused = not await store.get(session, "paused")
        await store.set_(session, "paused", paused)
    await _redraw_schedule(query)
    await query.answer("Пауза увімкнена" if paused else "Пауза знята")


@router.callback_query(SettingsCb.filter(F.action == "slot_add"))
async def cb_slot_add(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(EditSettings.slot)
    await query.message.answer("Надішли час слота у форматі <code>ГГ:ХХ</code>", parse_mode="HTML")
    await query.answer()


@router.message(EditSettings.slot)
async def save_slot(message: Message, state: FSMContext) -> None:
    if parse_slot(message.text or "") is None:
        await message.answer("Не той формат. Приклад: <code>19:30</code>", parse_mode="HTML")
        return
    await state.clear()
    async with session_scope() as session:
        slots = normalize_slots([*await store.get(session, "post_slots"), message.text])
        await store.set_(session, "post_slots", slots)
        await _reflow(session, slots)
    await message.answer(f"Слоти: {', '.join(slots)}")


@router.callback_query(SettingsCb.filter(F.action == "slot_del_menu"))
async def cb_slot_del_menu(query: CallbackQuery) -> None:
    async with session_scope() as session:
        slots = await store.get(session, "post_slots")
    await query.message.edit_reply_markup(reply_markup=slot_delete_keyboard(slots))
    await query.answer()


@router.callback_query(SettingsCb.filter(F.action == "slot_del"))
async def cb_slot_del(query: CallbackQuery, callback_data: SettingsCb) -> None:
    async with session_scope() as session:
        slots = [s for s in await store.get(session, "post_slots") if s != callback_data.value]
        await store.set_(session, "post_slots", slots)
        await _reflow(session, slots)
    await _redraw_schedule(query)
    await query.answer(f"Слот {callback_data.value} прибрано")


@router.callback_query(SettingsCb.filter(F.action == "queue_target"))
async def cb_queue_target(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(EditSettings.queue_target)
    await query.message.answer("Скільки постів тримати на ревʼю? Надішли число.")
    await query.answer()


@router.message(EditSettings.queue_target)
async def save_queue_target(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 20:
        await message.answer("Потрібне число від 1 до 20.")
        return
    await state.clear()
    async with session_scope() as session:
        await store.set_(session, "queue_target", int(raw))
    await message.answer(f"Ціль черги: {raw}")


@router.callback_query(SettingsCb.filter(F.action == "cancel"))
async def cb_cancel(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await query.message.edit_text("Скасовано")
    await query.answer()


# ------------------------------------------------------------------- службове


async def _redraw_schedule(query: CallbackQuery) -> None:
    async with session_scope() as session:
        text = await schedule_text(session)
        conf = await store.all_settings(session)
    try:
        await query.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=schedule_keyboard(conf["post_slots"], conf["paused"]),
        )
    except Exception:  # noqa: BLE001 — Telegram сварить, якщо текст не змінився
        pass


async def _reflow(session: AsyncSession, slots: list[str]) -> None:
    """Після зміни слотів переставляємо схвалені пости, щоб не було дір."""
    approved = await _posts(session, PostStatus.approved)
    taken: list = []
    for post in approved:
        moment = next_free_slot(slots, taken)
        post.scheduled_at = moment
        if moment:
            taken.append(moment)
    await session.flush()


async def _posts(session: AsyncSession, status: PostStatus) -> list[Post]:
    rows = await session.execute(
        select(Post)
        .where(Post.status == status)
        .options(selectinload(Post.phrase))
        .order_by(Post.scheduled_at.is_(None), Post.scheduled_at, Post.id)
    )
    return list(rows.scalars().all())
