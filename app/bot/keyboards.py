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


def reject_reason_keyboard(payment_id: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора причины отклонения."""
    builder = InlineKeyboardBuilder()
    for i, reason in enumerate(REJECT_REASONS):
        cb = f"t_reject_reason:{payment_id}:{i}"
        builder.row(InlineKeyboardButton(text=reason, callback_data=cb))
    builder.row(
        InlineKeyboardButton(
            text="🔙 Отмена",
            callback_data=f"t_reject_cancel:{payment_id}",
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


# ── Учитель: главное меню ──


def teacher_menu_keyboard(queue_count: int = 0) -> InlineKeyboardMarkup:
    """Меню учителя с кнопкой очереди и счётчиком."""
    builder = InlineKeyboardBuilder()
    badge = f" ({queue_count})" if queue_count > 0 else ""
    builder.row(
        InlineKeyboardButton(
            text=f"📋 Непроверенные оплаты{badge}",
            callback_data="teacher_queue",
        )
    )
    return builder.as_markup()


def teacher_queue_list_keyboard(
    payments: list[dict],
) -> InlineKeyboardMarkup:
    """Список непроверенных платежей для учителя."""
    builder = InlineKeyboardBuilder()
    for p in payments:
        pid = p["payment_id"]
        student_tg = p.get("student_tg_username", "—")
        amount_byn = p.get("amount", 0) / 100.0
        label = f"💰 {amount_byn:.2f} BYN — @{student_tg}"
        builder.row(
            InlineKeyboardButton(
                text=label,
                callback_data=f"teacher_view:{pid}",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data="teacher_queue_refresh",
        )
    )
    return builder.as_markup()


# ── Учитель: кнопки под фото платежа ──


def teacher_review_keyboard(payment_id: str) -> InlineKeyboardMarkup:
    """Inline-кнопки ✅ Подтвердить / ❌ Отклонить / ◀️ Назад к списку."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Подтвердить",
            callback_data=f"t_confirm:{payment_id}",
        ),
        InlineKeyboardButton(
            text="❌ Отклонить",
            callback_data=f"t_reject:{payment_id}",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="◀️ Назад к списку",
            callback_data="teacher_queue",
        )
    )
    return builder.as_markup()


def stats_nav_keyboard(
    student_id: int,
    page: int,
    has_next: bool,
    has_prev: bool,
) -> InlineKeyboardMarkup:
    """Навигация по страницам статистики: ◀️ стр N ▶️ + Назад."""
    builder = InlineKeyboardBuilder()
    nav_buttons = []
    if has_prev:
        nav_buttons.append(
            InlineKeyboardButton(
                text="◀️",
                callback_data=f"stats_page:{student_id}:{page - 1}",
            )
        )
    nav_buttons.append(
        InlineKeyboardButton(
            text=f"стр. {page}",
            callback_data="stats_noop",  # неактивная кнопка-индикатор
        )
    )
    if has_next:
        nav_buttons.append(
            InlineKeyboardButton(
                text="▶️",
                callback_data=f"stats_page:{student_id}:{page + 1}",
            )
        )
    builder.row(*nav_buttons)
    builder.row(
        InlineKeyboardButton(
            text="◀️ Назад в меню",
            callback_data=f"back_to_menu:{student_id}",
        )
    )
    return builder.as_markup()
