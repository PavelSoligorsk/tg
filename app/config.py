"""Централизованная конфигурация приложения."""
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения / .env файле!")

TELEGRAM_API: str = f"https://api.telegram.org/bot{BOT_TOKEN}"

# --- Образовательная платформа ---
EDUCATION_API_URL: str = os.getenv("EDUCATION_API_URL", "")
# API-ключ для доступа к платформе (передаётся в заголовке X-Telegram-Bot-Key)
EDUCATION_API_KEY: str = os.getenv("EDUCATION_API_KEY", "") or BOT_TOKEN

# Chromium
CHROMIUM_PATH: str | None = os.getenv("CHROMIUM_PATH")

# Database (заглушка — реальная БД подключается при необходимости)
DATABASE_URL: str = os.getenv("DATABASE_URL", "")
