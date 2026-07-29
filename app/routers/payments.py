"""REST API для платежей — эндпоинты для внешнего сервиса."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ConfirmPaymentRequest,
    RejectPaymentRequest,
    PaymentFilter,
    PaymentResponse,
    PaymentListResponse,
    PaymentStatsResponse,
    PaymentStatus,
    MessageResponse,
)
from app.services import payments as payment_svc

router = APIRouter(prefix="/api/payments", tags=["payments"])


# ---------------------------------------------------------------------------
#  ВНЕШНИЕ ЭНДПОЙНТЫ (для веб-интерфейса / админки)
# ---------------------------------------------------------------------------

@router.get("/", response_model=PaymentListResponse, summary="Список платежей")
def list_payments(
    tg_student_id: str | None = Query(None, description="Фильтр по Telegram ID ученика"),
    status: PaymentStatus | None = Query(None, description="Фильтр по статусу"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Возвращает список платежей с пагинацией и фильтрацией."""
    f = PaymentFilter(tg_student_id=tg_student_id, status=status, limit=limit, offset=offset)
    return payment_svc.list_payments(f)


@router.get("/{payment_id}", response_model=PaymentResponse, summary="Детали платежа")
def get_payment(payment_id: str):
    """Получить одну запись о платеже по ID."""
    p = payment_svc.get_by_id(payment_id)
    if not p:
        raise HTTPException(status_code=404, detail="Платёж не найден")
    return p


@router.get(
    "/student/{tg_student_id}/stats",
    response_model=PaymentStatsResponse,
    summary="Статистика ученика",
)
def get_student_stats(tg_student_id: str):
    """Сводка по всем платежам ученика."""
    return payment_svc.get_stats(tg_student_id)


# ---------------------------------------------------------------------------
#  ДЕЙСТВИЯ УЧИТЕЛЯ (эти эндпоинты может вызывать веб-интерфейс)
# ---------------------------------------------------------------------------

@router.post("/confirm", response_model=PaymentResponse, summary="Подтвердить платёж")
def confirm_payment(
    data: ConfirmPaymentRequest,
    teacher_tg_id: str = Query(..., description="Telegram ID учителя"),
):
    """
    Учитель подтверждает платёж:
    - Указывает сумму
    - Указывает Telegram ID ученика
    - Платёж переводится в статус confirmed
    """
    # Ищем платёж по receipt_message_id
    existing = payment_svc.get_by_receipt_message_id(data.receipt_message_id)
    if existing and existing.status != PaymentStatus.pending:
        raise HTTPException(status_code=409, detail=f"Платёж уже в статусе {existing.status.value}")

    if existing:
        payment_id = existing.id
    else:
        # Если записи нет — создаём (на случай, если родитель ещё не отправил)
        p = payment_svc.create_pending(
            receipt_message_id=data.receipt_message_id,
            tg_parent_id="",
        )
        payment_id = p.id

    updated = payment_svc.confirm(payment_id, teacher_tg_id=teacher_tg_id, data=data)
    if not updated:
        raise HTTPException(status_code=500, detail="Не удалось обновить платёж")
    return updated


@router.post("/reject", response_model=PaymentResponse, summary="Отклонить платёж")
def reject_payment(
    data: RejectPaymentRequest,
    teacher_tg_id: str = Query(..., description="Telegram ID учителя"),
):
    """Учитель отклоняет платёж с комментарием."""
    existing = payment_svc.get_by_receipt_message_id(data.receipt_message_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Платёж не найден")
    if existing.status != PaymentStatus.pending:
        raise HTTPException(status_code=409, detail=f"Платёж уже в статусе {existing.status.value}")

    updated = payment_svc.reject(existing.id, teacher_tg_id=teacher_tg_id, data=data)
    if not updated:
        raise HTTPException(status_code=500, detail="Не удалось обновить платёж")
    return updated


# ---------------------------------------------------------------------------
#  WEBHOOK ОТ ТЕЛЕГРАММА (приём чека от родителя)
# ---------------------------------------------------------------------------

@router.post("/webhook/receipt", response_model=PaymentResponse, summary="Принять чек от родителя")
def receive_receipt(
    receipt_message_id: int = Query(..., description="message_id сообщения с чеком"),
    tg_parent_id: str = Query(..., description="Telegram ID родителя"),
):
    """
    Вызывается когда родитель отправляет чек в бота.
    Создаёт запись в статусе pending.
    """
    return payment_svc.create_pending(
        receipt_message_id=receipt_message_id,
        tg_parent_id=tg_parent_id,
    )
