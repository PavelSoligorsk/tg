"""Инициализация aiogram бота и диспетчера.

Вебхук: FastAPI при получении обновления от Telegram передаёт его
через dispatcher.feed_webhook_update().

FSM-хранилище:
- Если задан REDIS_URL — RedisStorage (для прода)
- Иначе MemoryStorage (для разработки, теряется при рестарте)
"""

from __future__ import annotations

import logging
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import BOT_TOKEN, REDIS_URL

logger = logging.getLogger(__name__)

bot: Optional[Bot] = None
dp: Optional[Dispatcher] = None


def _create_storage() -> BaseStorage:
    """Создаёт FSM-хранилище: Redis при наличии, иначе память."""
    if REDIS_URL:
        try:
            from aiogram.fsm.storage.redis import RedisStorage
            storage = RedisStorage.from_url(REDIS_URL)
            logger.info("FSM storage: Redis")
            return storage
        except Exception as e:
            logger.warning(f"Redis недоступен ({e}), использую MemoryStorage")
    logger.warning("FSM storage: MemoryStorage (состояния теряются при рестарте)")
    return MemoryStorage()


def create_bot_and_dispatcher() -> tuple[Bot, Dispatcher]:
    """Создаёт экземпляры Bot и Dispatcher, подключает роутеры."""
    global bot, dp

    bot_instance = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp_instance = Dispatcher(storage=_create_storage())

    # Подключаем обработчики
    from app.bot.handlers import router as handlers_router
    dp_instance.include_router(handlers_router)

    bot = bot_instance
    dp = dp_instance

    logger.info("aiogram bot and dispatcher initialized")
    return bot_instance, dp_instance
