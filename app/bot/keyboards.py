"""Інлайн-клавіатури та callback-и."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


class ReviewCb(CallbackData, prefix="rv"):
    action: str
    post_id: int


class SettingsCb(CallbackData, prefix="st"):
    action: str
    value: str = ""


def review_keyboard(post_id: int, has_card: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Схвалити", callback_data=ReviewCb(action="approve", post_id=post_id))
    kb.button(text="🕑 Перенести", callback_data=ReviewCb(action="reschedule", post_id=post_id))
    kb.button(text="✏️ Змінити текст", callback_data=ReviewCb(action="edit", post_id=post_id))
    kb.button(text="🔄 Перегенерувати", callback_data=ReviewCb(action="regen", post_id=post_id))
    if has_card:
        kb.button(text="🎬 Інша гіфка", callback_data=ReviewCb(action="swap", post_id=post_id))
        kb.button(text="🔍 Кандидати", callback_data=ReviewCb(action="cands", post_id=post_id))
    else:
        kb.button(text="🔄 Спробувати ще", callback_data=ReviewCb(action="retry", post_id=post_id))
        kb.button(text="🔍 Кандидати", callback_data=ReviewCb(action="cands", post_id=post_id))
    kb.button(text="❌ Відхилити", callback_data=ReviewCb(action="reject", post_id=post_id))
    kb.button(text="⏭ Відкласти", callback_data=ReviewCb(action="snooze", post_id=post_id))
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup()


def approved_keyboard(post_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🕑 Перенести", callback_data=ReviewCb(action="reschedule", post_id=post_id))
    kb.button(text="🚀 Опублікувати зараз", callback_data=ReviewCb(action="now", post_id=post_id))
    kb.button(text="↩️ Повернути на ревʼю", callback_data=ReviewCb(action="unapprove", post_id=post_id))
    kb.adjust(2, 1)
    return kb.as_markup()


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✖️ Скасувати", callback_data=SettingsCb(action="cancel").pack())]
        ]
    )


def schedule_keyboard(slots: list[str], paused: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Додати слот", callback_data=SettingsCb(action="slot_add"))
    if slots:
        kb.button(text="➖ Прибрати слот", callback_data=SettingsCb(action="slot_del_menu"))
    kb.button(text="🎯 Розмір черги", callback_data=SettingsCb(action="queue_target"))
    kb.button(
        text="▶️ Зняти паузу" if paused else "⏸ Пауза",
        callback_data=SettingsCb(action="toggle_pause"),
    )
    kb.adjust(2, 2)
    return kb.as_markup()


def slot_delete_keyboard(slots: list[str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for slot in slots:
        kb.button(text=f"🗑 {slot}", callback_data=SettingsCb(action="slot_del", value=slot))
    kb.button(text="⬅️ Назад", callback_data=SettingsCb(action="schedule_home"))
    kb.adjust(2)
    return kb.as_markup()
