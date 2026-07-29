"""Инициализация aiogram бота и диспетчера.

Вебхук: FastAPI при получении обновления от Telegram передаёт его
через dispatcher.feed_webhook_update().
"""

from __future__ import annotations

import logging
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import BOT_TOKEN

logger = logging.getLogger(__name__)

bot: Optional[Bot] = None
dp: Optional[Dispatcher] = None


def create_bot_and_dispatcher() -> tuple[Bot, Dispatcher]:
    """Создаёт экземпляры Bot и Dispatcher, подключает роутеры."""
    global bot, dp

    bot_instance = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp_instance = Dispatcher(storage=MemoryStorage())

    # Подключаем обработчики
    from app.bot.handlers import router as handlers_router
    dp_instance.include_router(handlers_router)

    bot = bot_instance
    dp = dp_instance

    logger.info("aiogram bot and dispatcher initialized")
    return bot_instance, dp_instance
