"""SQLAlchemy-модели (заглушки для будущей БД)."""
# Пока используем in-memory storage. Эта таблица — контракт для будущей миграции.

PAYMENT_STATUSES = {
    "pending": "Ожидает проверки",
    "confirmed": "Подтверждена",
    "rejected": "Отклонена",
}

# Структура записи о платеже (контракт для БД):
# id: str (UUID)
# tg_receipt_message_id: str — message_id сообщения с чеком в чате учителя
# tg_student_id: str — Telegram ID ученика (username или числовой ID)
# tg_parent_id: str — Telegram ID родителя, отправившего чек
# amount: float — сумма платежа в BYN/RUB
# currency: str = "BYN"
# comment: str — комментарий от учителя
# status: str — pending | confirmed | rejected
# reviewed_by_teacher_tg_id: str — ID учителя, проверившего чек
# created_at: datetime
# updated_at: datetime
