"""Pydantic-схемы для API оплат."""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class PaymentStatus(str, Enum):
    pending = "pending"
    confirmed = "confirmed"
    rejected = "rejected"


# --- Запросы ---

class ConfirmPaymentRequest(BaseModel):
    """Учитель подтверждает (или отклоняет) платёж."""
    receipt_message_id: int = Field(..., description="message_id сообщения с чеком")
    amount: float = Field(..., gt=0, description="Сумма платежа")
    tg_student_id: str = Field(..., min_length=1, description="Telegram ID ученика (username или числовой ID)")
    comment: str = Field(default="", description="Комментарий от учителя")


class RejectPaymentRequest(BaseModel):
    """Учитель отклоняет платёж."""
    receipt_message_id: int = Field(..., description="message_id сообщения с чеком")
    comment: str = Field(default="", description="Причина отклонения")


class PaymentFilter(BaseModel):
    """Фильтр для выборки платежей."""
    tg_student_id: str | None = None
    status: PaymentStatus | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


# --- Ответы ---

class PaymentResponse(BaseModel):
    """Одна запись о платеже."""
    id: str
    receipt_message_id: int
    tg_student_id: str
    tg_parent_id: str | None = None
    amount: float
    currency: str = "BYN"
    comment: str = ""
    status: PaymentStatus
    reviewed_by_teacher_tg_id: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PaymentListResponse(BaseModel):
    """Список платежей."""
    total: int
    items: list[PaymentResponse]


class PaymentStatsResponse(BaseModel):
    """Сводка по платежам ученика."""
    tg_student_id: str
    total_paid: float
    total_pending: float
    total_rejected: float
    payments_count: int


class MessageResponse(BaseModel):
    """Универсальный ответ."""
    ok: bool = True
    detail: str = ""
