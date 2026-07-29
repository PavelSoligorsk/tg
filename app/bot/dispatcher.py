"""Основной диспетчер aiogram — инициализация бота и dp."""

import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import BOT_TOKEN

logger = logging.getLogger(__name__)

# Глобальные объекты (инициализируются в lifespan)
bot: Bot | None = None
dp: Dispatcher | None = None


def create_bot() -> Bot:
    return Bot(token=BOT_TOKEN)


def create_dispatcher() -> Dispatcher:
    storage = MemoryStorage()
    return Dispatcher(storage=storage)


def setup_handlers(d: Dispatcher) -> None:
    """Подключаем все обработчики к диспетчеру."""
    from app.bot.handlers import router

    d.include_router(router)
