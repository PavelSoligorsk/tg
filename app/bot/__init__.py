"""Обработчики Telegram-бота для платёжного процесса.

Сценарий:
1. Родитель отправляет чек (фото/скриншот) в бота
2. Бот пересылает в специальный чат учителя (TEACHER_CHAT_ID)
3. Учитель отвечает на пересланное сообщение командой:
   /confirm <сумма> <tg_id_ученика>
   или
   /reject <причина>

Формат команд:
  /confirm 120.50 @student_username
  /confirm 80 @student_username оплата за сентябрь
  /reject не тот чек

После подтверждения бот дёргает REST-эндпоинт образовательной платформы
POST /telegram/confirm-payment, которая зачисляет деньги на баланс ученика.
"""

import logging
import httpx

from app.config import BOT_TOKEN, TELEGRAM_API, EDUCATION_API_URL, EDUCATION_API_KEY
from app.services import payments as payment_svc

logger = logging.getLogger(__name__)

# ID чата, куда пересылаются чеки на проверку
# Задаётся через переменную окружения TEACHER_CHAT_ID
import os
TEACHER_CHAT_ID: int = int(os.getenv("TEACHER_CHAT_ID", "0"))


# ---------------------------------------------------------------------------
#  Хелперы для работы с Telegram API и платформой
# ---------------------------------------------------------------------------

async def _resolve_username(user_id: int) -> str:
    """Резолвим числовой Telegram user_id → @username через getChat."""
    if not user_id:
        return ""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TELEGRAM_API}/getChat",
                params={"chat_id": user_id},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    result = data.get("result", {})
                    username = result.get("username", "")
                    return f"@{username}" if username else str(user_id)
    except Exception as e:
        logger.warning(f"Не удалось резолвить username для {user_id}: {e}")
    return str(user_id)


async def _call_platform_confirm(
    teacher_tg_username: str,
    student_tg_username: str,
    amount: int,          # копейки BYN
    payment_type: str = "per_lesson",
    comment: str = "",
    **kwargs,
) -> dict:
    """Вызывает POST /telegram/confirm-payment на образовательной платформе."""
    if not EDUCATION_API_URL:
        return {"ok": False, "detail": "EDUCATION_API_URL не настроен"}

    url = f"{EDUCATION_API_URL.rstrip('/')}/telegram/confirm-payment"
    payload = {
        "teacher_tg_username": teacher_tg_username,
        "student_tg_username": student_tg_username,
        "amount": amount,
        "payment_type": payment_type,
        "comment": comment,
        **kwargs,
    }
    headers = {
        "X-Telegram-Bot-Key": EDUCATION_API_KEY,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers, timeout=20.0)
            if resp.status_code == 200:
                data = resp.json()
                return {"ok": True, "platform_response": data}
            else:
                logger.error(
                    f"Ошибка платформы ({resp.status_code}): {resp.text}"
                )
                return {
                    "ok": False,
                    "detail": f"Платформа вернула {resp.status_code}: {resp.text[:200]}",
                }
    except httpx.RequestError as e:
        logger.error(f"Ошибка сети при вызове платформы: {e}")
        return {"ok": False, "detail": f"Ошибка связи с платформой: {e}"}


# ---------------------------------------------------------------------------
#  Приём чека от родителя
# ---------------------------------------------------------------------------

async def handle_parent_receipt(chat_id: int, message_id: int, from_user_id: int) -> dict:
    """Родитель отправил фото → сохраняем pending и пересылаем учителю.

    Returns:
        dict с результатом операции.
    """
    # 1. Пересылаем чек в чат учителя (сначала, чтобы получить ID пересланного сообщения)
    forwarded_msg_id = message_id  # fallback
    if TEACHER_CHAT_ID:
        async with httpx.AsyncClient() as client:
            fwd_resp = await client.post(
                f"{TELEGRAM_API}/forwardMessage",
                json={
                    "chat_id": TEACHER_CHAT_ID,
                    "from_chat_id": chat_id,
                    "message_id": message_id,
                },
                timeout=15.0,
            )
            if fwd_resp.status_code == 200:
                fwd_data = fwd_resp.json()
                if fwd_data.get("ok"):
                    forwarded_msg_id = fwd_data["result"]["message_id"]
                    logger.info(
                        f"Чек переслан учителю: original_msg={message_id}, "
                        f"forwarded_msg={forwarded_msg_id}"
                    )
            else:
                logger.error(f"Не удалось переслать чек учителю: {fwd_resp.text}")

            # Отправляем инструкцию учителю (в ответ на пересланное сообщение)
            await client.post(
                f"{TELEGRAM_API}/sendMessage",
                json={
                    "chat_id": TEACHER_CHAT_ID,
                    "reply_to_message_id": forwarded_msg_id,
                    "text": (
                        f"📎 *Новый чек на проверку*\n\n"
                        f"От родителя: `{from_user_id}`\n\n"
                        f"Для подтверждения *ответьте на это фото*:\n"
                        f"`/confirm <сумма> <@username_ученика>`\n\n"
                        f"Для отклонения:\n"
                        f"`/reject <причина>`\n\n"
                        f"Для просмотра статуса:\n"
                        f"`/status`"
                    ),
                    "parse_mode": "Markdown",
                },
                timeout=15.0,
            )

    # 2. Создаём запись в статусе pending (receipt_message_id = ID в чате учителя)
    payment = payment_svc.create_pending(
        receipt_message_id=forwarded_msg_id,
        tg_parent_id=str(from_user_id),
    )

    # 3. Подтверждение родителю
    return {
        "payment_id": payment.id,
        "status": "pending",
    }


# ---------------------------------------------------------------------------
#  Команды учителя
# ---------------------------------------------------------------------------

async def handle_teacher_confirm(
    teacher_chat_id: int,
    teacher_user_id: int,
    receipt_message_id: int,
    amount: float,
    tg_student_id: str,
    comment: str = "",
) -> dict:
    """Учитель подтверждает чек командой /confirm.

    Выполняет:
    1. Обновляет запись в локальном in-memory хранилище tg_bot
    2. Вызывает POST /telegram/confirm-payment на образовательной платформе
    3. Уведомляет учителя о результате

    Returns:
        dict с результатом.
    """
    from app.schemas import ConfirmPaymentRequest

    # Ищем платёж
    payment = payment_svc.get_by_receipt_message_id(receipt_message_id)
    if not payment:
        # Создаём новую запись
        payment = payment_svc.create_pending(
            receipt_message_id=receipt_message_id,
            tg_parent_id="",
        )

    updated = payment_svc.confirm(
        payment.id,
        teacher_tg_id=str(teacher_user_id),
        data=ConfirmPaymentRequest(
            receipt_message_id=receipt_message_id,
            amount=amount,
            tg_student_id=tg_student_id,
            comment=comment,
        ),
    )

    if not updated:
        return {"ok": False, "detail": "Не удалось подтвердить платёж"}

    # ── Вызов образовательной платформы ──
    teacher_username = await _resolve_username(teacher_user_id)
    platform_result = None
    if EDUCATION_API_URL:
        # Конвертируем float BYN → копейки (целое число)
        amount_kop = int(round(amount * 100))
        platform_result = await _call_platform_confirm(
            teacher_tg_username=teacher_username,
            student_tg_username=tg_student_id,
            amount=amount_kop,
            payment_type="per_lesson",
            comment=comment,
        )

    # ── Уведомляем учителя ──
    platform_ok = platform_result and platform_result.get("ok")
    platform_info = ""
    if platform_result and platform_ok:
        presp = platform_result.get("platform_response", {})
        platform_info = (
            f"\n\n🔄 *Баланс обновлён:* платформа подтвердила зачисление\n"
            f"ID платежа на платформе: `{presp.get('payment_id', '—')}`"
        )
    elif platform_result and not platform_ok:
        platform_info = (
            f"\n\n⚠️ *Платформа недоступна:* {platform_result.get('detail', 'неизвестная ошибка')}"
        )

    async with httpx.AsyncClient() as client:
        await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": teacher_chat_id,
                "text": (
                    f"✅ *Платёж подтверждён!*\n\n"
                    f"ID: `{updated.id}`\n"
                    f"Сумма: *{updated.amount}* {updated.currency}\n"
                    f"Ученик: `{updated.tg_student_id}`\n"
                    f"Статус: `{updated.status.value}`"
                    f"{platform_info}"
                ),
                "parse_mode": "Markdown",
                "reply_to_message_id": receipt_message_id,
            },
            timeout=15.0,
        )

    return {
        "ok": True,
        "payment": updated,
        "platform_result": platform_result,
    }


async def handle_teacher_reject(
    teacher_chat_id: int,
    teacher_user_id: int,
    receipt_message_id: int,
    comment: str = "",
) -> dict:
    """Учитель отклоняет чек командой /reject.

    Returns:
        dict с результатом.
    """
    from app.schemas import RejectPaymentRequest

    payment = payment_svc.get_by_receipt_message_id(receipt_message_id)
    if not payment:
        return {"ok": False, "detail": "Платёж не найден"}

    updated = payment_svc.reject(
        payment.id,
        teacher_tg_id=str(teacher_user_id),
        data=RejectPaymentRequest(
            receipt_message_id=receipt_message_id,
            comment=comment,
        ),
    )

    if not updated:
        return {"ok": False, "detail": "Не удалось отклонить платёж"}

    # Уведомляем учителя
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": teacher_chat_id,
                "text": (
                    f"❌ *Платёж отклонён*\n\n"
                    f"ID: `{updated.id}`\n"
                    f"Причина: {updated.comment or 'не указана'}"
                ),
                "parse_mode": "Markdown",
                "reply_to_message_id": receipt_message_id,
            },
            timeout=15.0,
        )

    return {"ok": True, "payment": updated}


async def handle_teacher_status(
    teacher_chat_id: int,
    teacher_user_id: int,
    receipt_message_id: int,
) -> dict:
    """Учитель запрашивает статус платежа командой /status."""
    payment = payment_svc.get_by_receipt_message_id(receipt_message_id)
    if not payment:
        return {"ok": False, "detail": "Платёж не найден"}

    status_labels = {
        "pending": "⏳ Ожидает проверки",
        "confirmed": "✅ Подтверждён",
        "rejected": "❌ Отклонён",
    }

    async with httpx.AsyncClient() as client:
        await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": teacher_chat_id,
                "text": (
                    f"📋 *Статус платежа*\n\n"
                    f"ID: `{payment.id}`\n"
                    f"Сумма: *{payment.amount}* {payment.currency}\n"
                    f"Ученик: `{payment.tg_student_id or 'не указан'}`\n"
                    f"Статус: {status_labels.get(payment.status.value, payment.status.value)}\n"
                    f"Комментарий: {payment.comment or '—'}"
                ),
                "parse_mode": "Markdown",
                "reply_to_message_id": receipt_message_id,
            },
            timeout=15.0,
        )

    return {"ok": True, "payment": payment}
