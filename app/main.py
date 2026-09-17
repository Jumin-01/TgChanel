"""Точка входу: бот на polling + планувальник в одному процесі."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot import commands, review
from app.config import settings
from app.db.base import init_db
from app.scheduler import build_scheduler

log = logging.getLogger(__name__)


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def check_config() -> list[str]:
    problems = []
    if not settings.bot_token:
        problems.append("BOT_TOKEN не заданий")
    if not settings.channel_id:
        problems.append("CHANNEL_ID не заданий")
    if not settings.admins:
        problems.append("ADMIN_IDS порожній — нема кому схвалювати пости")
    if not settings.gemini_api_key:
        problems.append("GEMINI_API_KEY не заданий")
    return problems


async def main() -> None:
    setup_logging()

    problems = check_config()
    if problems:
        for item in problems:
            log.error("Конфіг: %s", item)
        raise SystemExit("Заповни .env — приклад у .env.example")

    await init_db()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    await bot.set_my_commands(commands.BOT_COMMANDS)

    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(commands.router)
    dispatcher.include_router(review.router)

    scheduler = build_scheduler(bot)
    scheduler.start()
    log.info("Стартую. Канал: %s, адміни: %s", settings.channel_id, settings.admins)

    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
