"""Сервис платежей — in-memory storage (заглушка)."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from threading import Lock

from app.schemas import (
    PaymentResponse,
    PaymentStatus,
    PaymentStatsResponse,
    PaymentListResponse,
    ConfirmPaymentRequest,
    RejectPaymentRequest,
    PaymentFilter,
)

# -- In-memory хранилище -----------------------------------------------------
_lock = Lock()
_store: dict[str, dict] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# -- CRUD --------------------------------------------------------------------

def create_pending(
    *,
    receipt_message_id: int,
    tg_parent_id: str,
) -> PaymentResponse:
    """Родитель отправил чек → создаём запись в статусе pending."""
    payment_id = uuid.uuid4().hex[:12]
    now = _now()
    record = {
        "id": payment_id,
        "receipt_message_id": receipt_message_id,
        "tg_student_id": "",          # учитель укажет позже
        "tg_parent_id": tg_parent_id,
        "amount": 0.0,                # учитель укажет позже
        "currency": "BYN",
        "comment": "",
        "status": PaymentStatus.pending.value,
        "reviewed_by_teacher_tg_id": None,
        "created_at": now,
        "updated_at": now,
    }
    with _lock:
        _store[payment_id] = record
    return PaymentResponse.model_validate(record)


def confirm(
    payment_id: str,
    *,
    teacher_tg_id: str,
    data: ConfirmPaymentRequest,
) -> PaymentResponse | None:
    """Учитель подтверждает платёж."""
    with _lock:
        rec = _store.get(payment_id)
        if rec is None:
            return None
        rec["amount"] = data.amount
        rec["tg_student_id"] = data.tg_student_id
        rec["comment"] = data.comment
        rec["status"] = PaymentStatus.confirmed.value
        rec["reviewed_by_teacher_tg_id"] = teacher_tg_id
        rec["updated_at"] = _now()
        return PaymentResponse.model_validate(rec)


def reject(
    payment_id: str,
    *,
    teacher_tg_id: str,
    data: RejectPaymentRequest,
) -> PaymentResponse | None:
    """Учитель отклоняет платёж."""
    with _lock:
        rec = _store.get(payment_id)
        if rec is None:
            return None
        rec["comment"] = data.comment
        rec["status"] = PaymentStatus.rejected.value
        rec["reviewed_by_teacher_tg_id"] = teacher_tg_id
        rec["updated_at"] = _now()
        return PaymentResponse.model_validate(rec)


def get_by_id(payment_id: str) -> PaymentResponse | None:
    with _lock:
        rec = _store.get(payment_id)
    return PaymentResponse.model_validate(rec) if rec else None


def get_by_receipt_message_id(receipt_message_id: int) -> PaymentResponse | None:
    with _lock:
        for rec in _store.values():
            if rec["receipt_message_id"] == receipt_message_id:
                return PaymentResponse.model_validate(rec)
    return None


def list_payments(f: PaymentFilter) -> PaymentListResponse:
    with _lock:
        items = list(_store.values())

    if f.tg_student_id:
        items = [i for i in items if i["tg_student_id"] == f.tg_student_id]
    if f.status:
        items = [i for i in items if i["status"] == f.status.value]

    items.sort(key=lambda r: r["created_at"], reverse=True)
    total = len(items)
    page = items[f.offset : f.offset + f.limit]

    return PaymentListResponse(
        total=total,
        items=[PaymentResponse.model_validate(r) for r in page],
    )


def get_stats(tg_student_id: str) -> PaymentStatsResponse:
    with _lock:
        student_payments = [
            r for r in _store.values() if r["tg_student_id"] == tg_student_id
        ]

    total_paid = sum(r["amount"] for r in student_payments if r["status"] == PaymentStatus.confirmed.value)
    total_pending = sum(r["amount"] for r in student_payments if r["status"] == PaymentStatus.pending.value)
    total_rejected = sum(r["amount"] for r in student_payments if r["status"] == PaymentStatus.rejected.value)

    return PaymentStatsResponse(
        tg_student_id=tg_student_id,
        total_paid=total_paid,
        total_pending=total_pending,
        total_rejected=total_rejected,
        payments_count=len(student_payments),
    )
