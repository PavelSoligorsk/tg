"""Redis-очередь для непроверенных оплат учителя.

Ключи:
  payment:{payment_id}  — hash с деталями платежа
  teacher_queue:{tg_username}  — sorted set payment_id → timestamp
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

import redis.asyncio as aioredis

from app.config import REDIS_URL

logger = logging.getLogger(__name__)

# Глобальный клиент (ленивая инициализация)
_redis: Optional[aioredis.Redis] = None


async def _get_redis() -> aioredis.Redis:
    """Возвращает (и при необходимости создаёт) глобальный Redis-клиент."""
    global _redis
    if _redis is None:
        if not REDIS_URL:
            raise RuntimeError("REDIS_URL не задан — очередь оплат недоступна")
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


# ── Платёж ──


async def push_payment(
    payment_id: str,
    teacher_tg_username: str,
    student_tg_username: str,
    amount: int,
    parent_chat_id: int,
    photo_file_id: str,
    extra: Optional[dict] = None,
) -> None:
    """Сохраняет платёж в Redis и добавляет в очередь учителя."""
    r = await _get_redis()
    ts = time.time()
    data = {
        "payment_id": payment_id,
        "teacher_tg_username": teacher_tg_username,
        "student_tg_username": student_tg_username,
        "amount": amount,
        "parent_chat_id": parent_chat_id,
        "photo_file_id": photo_file_id,
        "created_at": ts,
        **(extra or {}),
    }
    pipe = r.pipeline()
    pipe.hset(f"payment:{payment_id}", mapping={k: json.dumps(v) for k, v in data.items()})
    pipe.zadd(f"teacher_queue:{teacher_tg_username}", {payment_id: ts})
    await pipe.execute()
    logger.info(f"Payment {payment_id} pushed to teacher @{teacher_tg_username} queue")


async def get_payment(payment_id: str) -> Optional[dict]:
    """Возвращает детали одного платежа или None."""
    r = await _get_redis()
    raw = await r.hgetall(f"payment:{payment_id}")
    if not raw:
        return None
    return {k: json.loads(v) for k, v in raw.items()}


async def remove_payment(payment_id: str, teacher_tg_username: str) -> None:
    """Удаляет платёж из Redis (после подтверждения/отклонения)."""
    r = await _get_redis()
    pipe = r.pipeline()
    pipe.delete(f"payment:{payment_id}")
    pipe.zrem(f"teacher_queue:{teacher_tg_username}", payment_id)
    await pipe.execute()
    logger.info(f"Payment {payment_id} removed from queue")


async def get_teacher_queue(tg_username: str) -> list[dict]:
    """Возвращает список непроверенных платежей учителя (новые сверху)."""
    r = await _get_redis()
    payment_ids = await r.zrevrange(f"teacher_queue:{tg_username}", 0, -1)
    if not payment_ids:
        return []

    pipe = r.pipeline()
    for pid in payment_ids:
        pipe.hgetall(f"payment:{pid}")
    results = await pipe.execute()

    payments = []
    for pid, raw in zip(payment_ids, results):
        if raw:
            payments.append({k: json.loads(v) for k, v in raw.items()})
    return payments


async def get_teacher_queue_count(tg_username: str) -> int:
    """Возвращает количество непроверенных платежей."""
    r = await _get_redis()
    return await r.zcard(f"teacher_queue:{tg_username}")
