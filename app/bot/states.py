"""FSM-стани для введення тексту."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class EditPost(StatesGroup):
    caption = State()          # чекаємо новий підпис
    regen_comment = State()    # чекаємо коментар для перегенерації
    reschedule = State()       # чекаємо новий час публікації


class EditSettings(StatesGroup):
    slot = State()             # чекаємо час слота "HH:MM"
    queue_target = State()     # чекаємо число
