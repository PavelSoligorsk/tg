"""Inline-клавиатуры для Telegram-бота."""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List


# ── Главное меню родителя ──

def parent_menu_keyboard(student_id: int) -> InlineKeyboardMarkup:
    """Меню с 3 опциями для родителя."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💳 Отправить оплату", callback_data=f"pay:{student_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="💰 Баланс", callback_data=f"balance:{student_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data=f"stats:{student_id}"),
    )
    return builder.as_markup()


# ── Выбор ребёнка ──

def children_selection_keyboard(
    children: List[dict],
) -> InlineKeyboardMarkup:
    """Клавиатура для выбора ребёнка (если несколько)."""
    builder = InlineKeyboardBuilder()
    for child in children:
        name = child.get("name", f"ID {child['id']}")
        builder.row(
            InlineKeyboardButton(
                text=f"👤 {name}",
                callback_data=f"select_child:{child['id']}",
            )
        )
    return builder.as_markup()


# ── Подтверждение оплаты родителем ──

def payment_confirm_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения перед отправкой оплаты."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да, отправить", callback_data="pay_confirm:yes"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="pay_confirm:no"),
    )
    return builder.as_markup()


# ── Учитель: подтверждение / отклонение оплаты ──

def teacher_payment_keyboard(
    payment_id: str,
    student_tg_username: str,
    amount: int,
) -> InlineKeyboardMarkup:
    """Inline-кнопки ✅ Подтвердить / ❌ Отклонить под пересланным фото."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Подтвердить",
            callback_data=f"t_confirm:{payment_id}:{student_tg_username}:{amount}",
        ),
        InlineKeyboardButton(
            text="❌ Отклонить",
            callback_data=f"t_reject:{payment_id}:{student_tg_username}",
        ),
    )
    return builder.as_markup()


# ── Причины отклонения ──

REJECT_REASONS = [
    "Неверная сумма",
    "Чек не читается",
    "Чек не соответствует оплате",
    "Повторная оплата",
    "Другое",
]


def reject_reason_keyboard(payment_id: str, student_tg: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора причины отклонения."""
    builder = InlineKeyboardBuilder()
    for i, reason in enumerate(REJECT_REASONS):
        cb = f"t_reject_reason:{payment_id}:{student_tg}:{i}"
        builder.row(InlineKeyboardButton(text=reason, callback_data=cb))
    builder.row(
        InlineKeyboardButton(
            text="🔙 Отмена",
            callback_data=f"t_reject_cancel:{payment_id}:{student_tg}",
        )
    )
    return builder.as_markup()


def back_to_main_keyboard(student_id: int) -> InlineKeyboardMarkup:
    """Кнопка «Назад в меню»."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="◀️ Назад в меню",
            callback_data=f"back_to_menu:{student_id}",
        )
    )
    return builder.as_markup()
