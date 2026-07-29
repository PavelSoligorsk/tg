"""FSM состояния для aiogram."""

from aiogram.fsm.state import State, StatesGroup


class PaymentFlow(StatesGroup):
    """Flow отправки оплаты родителем."""

    waiting_for_amount = State()    # ждём сумму
    waiting_for_photo = State()     # ждём фото чека
    confirm = State()               # подтверждение перед отправкой


class RejectReason(StatesGroup):
    """Flow отклонения оплаты учителем."""

    waiting_for_custom_reason = State()  # ждём свой вариант причины


class SelectStudent(StatesGroup):
    """Выбор ученика (если у родителя несколько детей)."""

    waiting_for_selection = State()
