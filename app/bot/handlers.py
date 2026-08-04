"""Основные обработчики Telegram-бота на aiogram.

Сценарии:
- /start → whoami → маршрутизация (родитель / учитель / ученик)
- Родитель: меню → оплата / баланс / статистика
- Учитель: инлайн-кнопки подтверждения/отклонения оплат
- Ученик: заглушка
"""

import logging
import re
import uuid
from typing import Optional

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command

from app.bot.api_client import api, PlatformAPI
from app.bot.states import PaymentFlow, RejectReason, SelectStudent
from app.bot.keyboards import (
    parent_menu_keyboard,
    children_selection_keyboard,
    payment_confirm_keyboard,
    reject_reason_keyboard,
    back_to_main_keyboard,
    teacher_menu_keyboard,
    teacher_queue_list_keyboard,
    teacher_review_keyboard,
    stats_nav_keyboard,
    student_menu_keyboard,
    schedule_period_keyboard,
    REJECT_REASONS,
)
from app.bot import redis_queue

logger = logging.getLogger(__name__)

router = Router()

# ═══════════════════════════════════════════════════════════════
# Восстановление пароля (общая кнопка для всех ролей)
# ═══════════════════════════════════════════════════════════════


@router.callback_query(F.data == "forgot_password")
async def cb_forgot_password(callback: CallbackQuery) -> None:
    """Пользователь нажал «Восстановить пароль»."""
    tg_username = callback.from_user.username or ""
    if not tg_username:
        await callback.answer("Не задан @username", show_alert=True)
        return

    await callback.answer()
    result = await api.forgot_password(tg_username)
    await callback.message.edit_text(
        f"🔑 *Сброс пароля*\n\n{result.get('message', 'Ошибка')}",
        parse_mode="Markdown",
    )


# ═══════════════════════════════════════════════════════════════
# Расписание (учитель + ученик)
# ═══════════════════════════════════════════════════════════════


@router.callback_query(F.data.startswith("schedule:"))
async def cb_schedule(callback: CallbackQuery) -> None:
    """Показать расписание (неделя или месяц)."""
    period = callback.data.split(":")[1]
    tg_username = callback.from_user.username or ""
    if not tg_username:
        await callback.answer("Не задан @username", show_alert=True)
        return

    await callback.answer("Загружаю расписание...")
    data = await api.get_schedule(tg_username, period)

    if not data.get("ok"):
        await callback.message.edit_text(
            f"❌ {data.get('message', 'Ошибка загрузки')}",
            parse_mode="Markdown",
            reply_markup=schedule_period_keyboard(),
        )
        return

    lessons = data.get("lessons", [])
    period_label = "неделю" if period == "week" else "месяц"

    if not lessons:
        text = f"📅 *Расписание на {period_label}*\n\nЗанятий нет."
    else:
        lines = [f"📅 *Расписание на {period_label}*\n"]
        for l in lessons:
            lines.append(
                f"{l['status_emoji']} *{l['date']}* {l['time']} — {l['title']}"
            )
        text = "\n".join(lines)

    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=schedule_period_keyboard(),
    )


@router.callback_query(F.data == "back_to_schedule_menu")
async def cb_back_to_schedule_menu(callback: CallbackQuery) -> None:
    """Вернуться в главное меню из расписания."""
    tg_username = callback.from_user.username or ""
    whoami = await api.whoami(tg_username)
    role = whoami.get("role", "")
    if role == "teacher":
        await _show_teacher_menu_edit(callback)
    elif role == "student":
        await _show_student_menu_edit(callback)
    else:
        await callback.answer("Неизвестная роль", show_alert=True)


# ═══════════════════════════════════════════════════════════════
# Домашние задания (ученик)
# ═══════════════════════════════════════════════════════════════


@router.callback_query(F.data == "my_assignments")
async def cb_my_assignments(callback: CallbackQuery) -> None:
    """Показать назначенные тесты."""
    tg_username = callback.from_user.username or ""
    await callback.answer("Загружаю задания...")
    data = await api.get_my_assignments(tg_username)

    if not data.get("ok"):
        await callback.message.edit_text(
            f"❌ {data.get('message', 'Ошибка')}",
            parse_mode="Markdown",
            reply_markup=student_menu_keyboard(),
        )
        return

    assignments = data.get("assignments", [])
    if not assignments:
        text = "📝 *Мои задания*\n\nНет активных заданий."
    else:
        lines = ["📝 *Мои задания*\n"]
        for a in assignments:
            lines.append(
                f"📌 *{a['title']}*\n"
                f"   📅 Срок: {a['due_date']} {a.get('due', '')}"
            )
        text = "\n".join(lines)

    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=student_menu_keyboard(),
    )


# ═══════════════════════════════════════════════════════════════
# Оплата от ученика
# ═══════════════════════════════════════════════════════════════


@router.callback_query(F.data == "student_pay")
async def cb_student_pay_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Ученик нажал «Отправить оплату»."""
    tg_username = callback.from_user.username or ""
    whoami = await api.whoami(tg_username)
    if whoami.get("role") != "student":
        await callback.answer("Только для учеников", show_alert=True)
        return

    await callback.answer()
    await state.update_data(student_id=whoami.get("id", 0), student_tg_username=tg_username)
    await state.set_state(PaymentFlow.waiting_for_amount)

    await callback.message.edit_text(
        "💳 *Отправка оплаты*\n\n"
        "Введите сумму в BYN (например: `120.50`):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Отмена", callback_data="pay_cancel_student")]
            ]
        ),
    )


@router.callback_query(F.data == "pay_cancel_student")
async def cb_student_pay_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Отмена оплаты учеником."""
    await state.clear()
    await callback.answer("Отменено")
    await _show_student_menu_edit(callback)


# ── Хелперы для меню ──


async def _show_student_menu_edit(callback: CallbackQuery) -> None:
    """Показать меню ученика через edit_message."""
    tg_username = callback.from_user.username or ""
    whoami = await api.whoami(tg_username)
    name = whoami.get("name", tg_username)
    await callback.message.edit_text(
        f"👋 Привет, {name}!\n\nВыберите действие:",
        reply_markup=student_menu_keyboard(),
    )


async def _show_teacher_menu_edit(callback: CallbackQuery) -> None:
    """Показать меню учителя через edit_message."""
    tg_username = callback.from_user.username or ""
    queue_count = 0
    try:
        queue_count = await redis_queue.get_teacher_queue_count(tg_username)
    except Exception:
        pass
    whoami_data = await api.whoami(tg_username)
    name = whoami_data.get("name", tg_username)
    students_count = whoami_data.get("students_count", 0)
    await callback.message.edit_text(
        f"👋 Здравствуйте, {name}!\n\nУ вас {students_count} учеников.",
        reply_markup=teacher_menu_keyboard(queue_count),
    )


# ── Форматирование ──

def _cents_to_byn(amount: int) -> str:
    """Копейки → BYN с точкой."""
    byn = amount / 100.0
    return f"{byn:.2f} BYN"


def _format_balance(student: dict) -> str:
    """Форматирование баланса ученика."""
    balance = student.get("balance", 0)
    return _cents_to_byn(balance)


# ═══════════════════════════════════════════════════════════════
# /start — точка входа
# ═══════════════════════════════════════════════════════════════


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Определяем роль пользователя и маршрутизируем."""
    await state.clear()

    tg_username = message.from_user.username
    if not tg_username:
        await message.answer(
            "❌ У вашего Telegram-аккаунта нет @username.\n\n"
            "Пожалуйста, задайте username в настройках Telegram и попробуйте снова."
        )
        return

    result = await api.whoami(tg_username)

    if not result.get("found"):
        await message.answer(
            "❌ Вы не зарегистрированы в системе.\n\n"
            "Пожалуйста, обратитесь к администратору для регистрации.\n"
            "📞 Контакты: уточните у вашего преподавателя."
        )
        return

    role = result.get("role")
    name = result.get("name", tg_username)

    if role == "parent":
        await _handle_parent_start(message, result, tg_username)
    elif role == "teacher":
        await _handle_teacher_start(message, result, name)
    elif role == "student":
        await _handle_student_start(message, result, name)
    else:
        await message.answer(
            f"ℹ️ {result.get('message', 'Роль не поддерживается через бота.')}"
        )


# ═══════════════════════════════════════════════════════════════
# Родитель
# ═══════════════════════════════════════════════════════════════


async def _handle_parent_start(message: Message, result: dict, tg_username: str) -> None:
    """Родитель найден — показываем детей или сразу меню."""
    children: list[dict] = result.get("children", [])
    parent_name = result.get("name", tg_username)

    if not children:
        await message.answer(
            f"👋 Здравствуйте, {parent_name}!\n\n"
            "У вас пока нет привязанных учеников.\n"
            "Пожалуйста, обратитесь к преподавателю для привязки."
        )
        return

    if len(children) == 1:
        # Сразу показываем меню
        child = children[0]
        await _show_parent_menu(message, child)
    else:
        # Предлагаем выбрать ребёнка
        await message.answer(
            f"👋 Здравствуйте, {parent_name}!\n\nВыберите ребёнка:",
            reply_markup=children_selection_keyboard(children),
        )


async def _show_parent_menu(message: Message, child: dict) -> None:
    """Показываем меню родителя для конкретного ученика."""
    student_id = child["id"]
    name = child.get("name", f"ID {student_id}")
    balance_str = _format_balance(child)
    teacher = child.get("teacher_name", "—")

    text = (
        f"👤 *{name}*\n"
        f"💰 Баланс: {balance_str}\n"
        f"👨‍🏫 Учитель: {teacher}\n\n"
        f"Выберите действие:"
    )
    await _try_edit_or_answer(
        message, text, parse_mode="Markdown",
        reply_markup=parent_menu_keyboard(student_id),
    )


# ── Выбор ребёнка (callback) ──


@router.callback_query(F.data.startswith("select_child:"))
async def cb_select_child(callback: CallbackQuery) -> None:
    """Родитель выбрал ребёнка из списка."""
    student_id = int(callback.data.split(":")[1])
    await callback.answer()

    # Запрашиваем свежие данные
    balance_data = await api.get_student_balance(student_id, limit=0)
    child = {
        "id": balance_data.get("student_id", student_id),
        "name": balance_data.get("student_name", f"Ученик {student_id}"),
        "balance": balance_data.get("balance", 0),
        "teacher_name": "—",  # кто пришёл — уже в тексте меню
    }
    await _show_parent_menu(callback.message, child)


@router.callback_query(F.data.startswith("back_to_menu:"))
async def cb_back_to_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка «Назад в меню»."""
    await state.clear()
    student_id = int(callback.data.split(":")[1])
    await callback.answer()

    balance_data = await api.get_student_balance(student_id, limit=0)
    child = {
        "id": balance_data.get("student_id", student_id),
        "name": balance_data.get("student_name", f"Ученик {student_id}"),
        "balance": balance_data.get("balance", 0),
        "teacher_name": "—",
    }
    await _show_parent_menu(callback.message, child)


# ── Оплата (родитель) ──


@router.callback_query(F.data.startswith("pay:"))
async def cb_pay_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Родитель нажал «Отправить оплату»."""
    student_id = int(callback.data.split(":")[1])
    await callback.answer()

    # Получаем tg_username ученика
    student_tg = ""
    balance_data = await api.get_student_balance(student_id, limit=0)
    student_name = balance_data.get("student_name", f"Ученик {student_id}")

    # Пытаемся получить tg_username через whoami родителя
    parent_whoami = await api.whoami(callback.from_user.username or "")
    if parent_whoami.get("children"):
        for child in parent_whoami["children"]:
            if child["id"] == student_id:
                student_tg = child.get("tg_username", "")
                break

    await state.update_data(
        student_id=student_id,
        student_tg_username=student_tg or str(student_id),
    )
    await state.set_state(PaymentFlow.waiting_for_amount)

    await callback.message.answer(
        "💳 *Отправка оплаты*\n\n"
        "Введите сумму в BYN (например: `120.50`):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [{"text": "Отмена", "callback_data": f"pay_cancel:{student_id}"}]
            ]
        ),
    )


@router.callback_query(F.data.startswith("pay_cancel:"))
async def cb_pay_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Отмена отправки оплаты."""
    student_id = int(callback.data.split(":")[1])
    await state.clear()
    await callback.answer("Отменено")
    await _navigate_back_to_menu(callback, student_id)


@router.message(PaymentFlow.waiting_for_amount, F.text)
async def pay_amount_received(message: Message, state: FSMContext) -> None:
    """Получили сумму, просим фото чека."""
    text = message.text.strip().replace(",", ".")
    try:
        amount_byn = float(text)
        if amount_byn <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректную сумму (например: `120.50`)", parse_mode="Markdown")
        return

    amount_kop = int(round(amount_byn * 100))
    await state.update_data(amount=amount_kop, amount_byn=amount_byn)
    await state.set_state(PaymentFlow.waiting_for_photo)

    await message.answer(
        f"📎 Пришлите фото или скриншот чека на сумму *{amount_byn:.2f} BYN*:",
        parse_mode="Markdown",
    )


@router.message(PaymentFlow.waiting_for_photo, F.photo)
async def pay_photo_received(message: Message, state: FSMContext, bot: Bot) -> None:
    """Получили фото чека — показываем подтверждение."""
    data = await state.get_data()
    amount_byn = data.get("amount_byn", 0)
    student_id = data.get("student_id", 0)

    # Сохраняем фото в state
    await state.update_data(photo_file_id=message.photo[-1].file_id)
    await state.set_state(PaymentFlow.confirm)

    balance_data = await api.get_student_balance(student_id, limit=0)
    student_name = balance_data.get("student_name", f"Ученик {student_id}")

    await message.answer(
        f"📋 *Подтверждение оплаты*\n\n"
        f"👤 Ученик: {student_name}\n"
        f"💵 Сумма: *{amount_byn:.2f} BYN*\n\n"
        f"Всё верно?",
        parse_mode="Markdown",
        reply_markup=payment_confirm_keyboard(),
    )


@router.callback_query(PaymentFlow.confirm, F.data.startswith("pay_confirm:"))
async def cb_pay_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Подтверждение или отмена оплаты."""
    choice = callback.data.split(":")[1]
    data = await state.get_data()
    student_id = data.get("student_id", 0)
    amount_byn = data.get("amount_byn", 0)
    amount_kop = data.get("amount", 0)
    photo_file_id = data.get("photo_file_id")

    if choice == "no":
        await state.clear()
        await callback.answer("Отменено")
        await callback.message.edit_text("❌ Отправка оплаты отменена.")
        await _navigate_back_to_menu(callback, student_id)
        return

    await callback.answer("Отправляем...")

    # Генерируем ID платежа
    payment_id = str(uuid.uuid4())[:8]

    # Получаем tg_username ученика (не DB id!)
    student_tg_username = data.get("student_tg_username", str(student_id))

    # Находим учителя через платформу
    teacher_chat_data = await api.get_teacher_chat(student_id)
    teacher_tg = teacher_chat_data.get("teacher_tg_username", "")

    if not teacher_tg:
        await callback.message.answer(
            "❌ У этого ученика нет привязанного учителя.\n\n"
            "Пожалуйста, обратитесь к администратору."
        )
        await state.clear()
        return

    # Сохраняем платёж в Redis-очередь учителя
    try:
        await redis_queue.push_payment(
            payment_id=payment_id,
            teacher_tg_username=teacher_tg,
            student_tg_username=student_tg_username,
            amount=amount_kop,
            parent_chat_id=callback.message.chat.id,
            photo_file_id=photo_file_id,
        )
    except Exception as e:
        logger.error(f"Ошибка сохранения платежа в Redis: {e}")
        await callback.message.answer(
            "❌ Не удалось сохранить платёж. Попробуйте позже."
        )
        await state.clear()
        return

    await callback.message.edit_text(
        f"✅ Чек отправлен на проверку!\n\n"
        f"ID: `{payment_id}`\n"
        f"Сумма: *{amount_byn:.2f} BYN*\n\n"
        f"Учитель проверит оплату и подтвердит её.",
        parse_mode="Markdown",
    )
    await state.clear()

    # Показываем меню обратно
    balance_data = await api.get_student_balance(student_id, limit=0)
    child = {
        "id": student_id,
        "name": balance_data.get("student_name", f"Ученик {student_id}"),
        "balance": balance_data.get("balance", 0),
        "teacher_name": "—",
    }
    await _show_parent_menu(callback.message, child)


# ── Баланс ──


@router.callback_query(F.data.startswith("balance:"))
async def cb_balance(callback: CallbackQuery) -> None:
    """Родитель смотрит баланс ученика."""
    student_id = int(callback.data.split(":")[1])
    await callback.answer()

    data = await api.get_student_balance(student_id, limit=5)
    if "error" in data:
        await callback.message.answer(f"❌ Ошибка получения баланса: {data['error']}")
        return

    student_name = data.get("student_name", f"Ученик {student_id}")
    balance = data.get("balance", 0)
    operations: list[dict] = data.get("last_operations", [])

    lines = [
        f"💰 *Баланс: {_cents_to_byn(balance)}*\n",
    ]

    if operations:
        lines.append("*Последние операции:*")
        for op in operations:
            sign = "🟢" if op["type"] == "deposit" else "🔴"
            op_type = "Пополнение" if op["type"] == "deposit" else "Списание"
            comment = f" — {op['comment']}" if op.get("comment") else ""
            dt_str = ""
            if op.get("created_at"):
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(str(op["created_at"]).replace("Z", "+00:00"))
                    dt_str = dt.strftime(" %d.%m.%Y")
                except Exception:
                    pass
            lines.append(
                f"{sign} {op_type}: {_cents_to_byn(op['amount'])}{dt_str}{comment}"
            )
    else:
        lines.append("Операций пока нет.")

    await _try_edit_or_answer(
        callback.message,
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=back_to_main_keyboard(student_id),
    )


# ── Статистика ──


def _format_payment_op(op: dict) -> str:
    """Форматирует одну операцию для вывода."""
    sign = "🟢" if op["type"] == "deposit" else "🔴"
    op_type = "Пополнение" if op["type"] == "deposit" else "Списание"
    comment = f" — {op['comment']}" if op.get("comment") else ""
    dt_str = ""
    if op.get("created_at"):
        try:
            from datetime import datetime as dt
            d = dt.fromisoformat(str(op["created_at"]).replace("Z", "+00:00"))
            dt_str = d.strftime(" %d.%m")
        except Exception:
            pass
    return f"{sign} {op_type}: {_cents_to_byn(op['amount'])}{dt_str}{comment}"


@router.callback_query(F.data.startswith("stats:"))
async def cb_stats(callback: CallbackQuery) -> None:
    """Статистика оплат ученика (сводка + пагинация)."""
    parts = callback.data.split(":")
    student_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 1
    await callback.answer()

    data = await api.get_payment_stats(student_id, page=page, page_size=5)
    if "error" in data:
        await callback.answer(f"Ошибка: {data['error']}", show_alert=True)
        return

    student_name = data.get("student_name", f"Ученик {student_id}")
    balance = data.get("balance", 0)
    total_dep = data.get("total_deposited", 0)
    total_spent = data.get("total_spent", 0)
    payments = data.get("payments", [])

    lines = [
        f"📊 *Статистика — {student_name}*",
        "",
        f"💰 Текущий баланс: *{_cents_to_byn(balance)}*",
        f"🟢 Всего пополнено: *{_cents_to_byn(total_dep)}*",
        f"🔴 Всего списано: *{_cents_to_byn(total_spent)}*",
        "",
    ]

    if payments:
        lines.append(f"*Операции (стр. {data['page']}/{data['total_pages']}):*")
        for op in payments:
            lines.append(_format_payment_op(op))
    else:
        lines.append("Операций пока нет.")

    await _try_edit_or_answer(
        callback.message,
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=stats_nav_keyboard(
            student_id=student_id,
            page=data["page"],
            has_next=data["has_next"],
            has_prev=data["has_prev"],
        ),
    )


@router.callback_query(F.data.startswith("stats_page:"))
async def cb_stats_page(callback: CallbackQuery) -> None:
    """Навигация по страницам статистики."""
    # stats_page:student_id:new_page
    parts = callback.data.split(":")
    student_id = int(parts[1])
    page = int(parts[2])
    # Перенаправляем в cb_stats с новой страницей
    callback.data = f"stats:{student_id}:{page}"
    await cb_stats(callback)


# ═══════════════════════════════════════════════════════════════
# Учитель
# ═══════════════════════════════════════════════════════════════


async def _handle_teacher_start(message: Message, result: dict, name: str) -> None:
    """Приветствие учителя. Сохраняем tg_chat_id на бэкенде и показываем меню."""
    students_count = result.get("students_count", 0)
    tg_username = result.get("tg_username", message.from_user.username or "")

    # Сохраняем chat_id учителя на платформе
    if tg_username:
        reg_result = await api.register_chat(tg_username, message.chat.id)
        if reg_result.get("found"):
            logger.info(f"Chat registered for teacher @{tg_username}: chat_id={message.chat.id}")
        else:
            logger.warning(f"Failed to register chat for teacher @{tg_username}: {reg_result}")

    # Считаем непроверенные оплаты
    queue_count = 0
    try:
        queue_count = await redis_queue.get_teacher_queue_count(tg_username)
    except Exception as e:
        logger.warning(f"Не удалось получить очередь из Redis: {e}")

    await message.answer(
        f"👋 Здравствуйте, {name}!\n\n"
        f"У вас {students_count} учеников.",
        reply_markup=teacher_menu_keyboard(queue_count),
    )


# ── Учитель: просмотр очереди ──


@router.callback_query(F.data == "teacher_queue")
async def cb_teacher_queue(callback: CallbackQuery) -> None:
    """Учитель открыл список непроверенных оплат."""
    tg_username = callback.from_user.username or ""
    if not tg_username:
        await callback.answer("Не задан @username", show_alert=True)
        return
    await callback.answer()

    try:
        payments = await redis_queue.get_teacher_queue(tg_username)
    except Exception as e:
        logger.error(f"Ошибка чтения очереди: {e}")
        await _try_edit_or_answer(
            callback.message,
            "❌ Не удалось загрузить очередь оплат. Попробуйте позже.",
            reply_markup=teacher_menu_keyboard(0),
        )
        return

    if not payments:
        await _try_edit_or_answer(
            callback.message,
            "📋 *Непроверенных оплат нет.*\n\nНовых чеков пока не поступало.",
            parse_mode="Markdown",
            reply_markup=teacher_menu_keyboard(0),
        )
        return

    await _try_edit_or_answer(
        callback.message,
        f"📋 *Непроверенные оплаты* ({len(payments)}):\n\nВыберите платёж для проверки:",
        parse_mode="Markdown",
        reply_markup=teacher_queue_list_keyboard(payments),
    )


@router.callback_query(F.data == "teacher_queue_refresh")
async def cb_teacher_queue_refresh(callback: CallbackQuery) -> None:
    """Обновить список очереди."""
    await cb_teacher_queue(callback)


# ── Учитель: просмотр одного платежа ──


@router.callback_query(F.data.startswith("teacher_view:"))
async def cb_teacher_view(callback: CallbackQuery, bot: Bot) -> None:
    """Учитель выбрал конкретный платёж — показываем фото и кнопки."""
    payment_id = callback.data.split(":")[1]
    await callback.answer()

    try:
        payment = await redis_queue.get_payment(payment_id)
    except Exception as e:
        logger.error(f"Ошибка чтения платежа {payment_id}: {e}")
        await callback.answer("Ошибка загрузки платежа", show_alert=True)
        return

    if not payment:
        await callback.answer("Платёж уже обработан или не найден", show_alert=True)
        return

    student_tg = payment.get("student_tg_username", "—")
    amount = int(payment.get("amount", 0))
    photo_id = payment.get("photo_file_id", "")
    amount_byn = amount / 100.0

    caption = (
        f"📎 *Чек на проверку*\n\n"
        f"ID: `{payment_id}`\n"
        f"Сумма: *{amount_byn:.2f} BYN*\n"
        f"Ученик: @{student_tg}\n\n"
        f"Выберите действие:"
    )

    try:
        sent = await bot.send_photo(
            chat_id=callback.message.chat.id,
            photo=photo_id,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=teacher_review_keyboard(payment_id),
        )
        # Сохраняем ID сообщения с фото для последующего edit_caption
        payment["teacher_msg_id"] = sent.message_id
    except Exception as e:
        logger.error(f"Не удалось отправить фото чека: {e}")
        await callback.answer("Не удалось загрузить фото чека", show_alert=True)


# ── Учитель: подтверждение оплаты ──


@router.callback_query(F.data.startswith("t_confirm:"))
async def cb_teacher_confirm(callback: CallbackQuery, bot: Bot) -> None:
    """Учитель нажал ✅ Подтвердить."""
    payment_id = callback.data.split(":")[1]

    # Загружаем платёж из Redis
    try:
        payment = await redis_queue.get_payment(payment_id)
    except Exception as e:
        logger.error(f"Ошибка чтения платежа {payment_id}: {e}")
        await callback.answer("Ошибка загрузки платежа", show_alert=True)
        return

    if not payment:
        await callback.answer("Платёж уже обработан", show_alert=True)
        return

    student_tg_username = payment.get("student_tg_username", "")
    amount = int(payment.get("amount", 0))
    teacher_tg = payment.get("teacher_tg_username", callback.from_user.username or "")
    teacher_username = callback.from_user.username or str(callback.from_user.id)
    parent_chat_id = int(payment.get("parent_chat_id", 0))

    # Вызываем платформу
    result = await api.confirm_payment(
        teacher_tg_username=f"@{teacher_username}",
        student_tg_username=student_tg_username.lstrip("@"),
        amount=amount,
        payment_type="per_lesson",
        comment=f"Подтверждено учителем @{teacher_username} через бота",
    )

    if result.get("error"):
        await callback.answer(f"Ошибка: {result['error']}", show_alert=True)
        return

    await callback.answer("✅ Подтверждено!")

    # Обновляем сообщение с фото
    new_caption = (
        f"✅ *Платёж подтверждён!*\n\n"
        f"ID: `{payment_id}`\n"
        f"Сумма: *{_cents_to_byn(amount)}*\n"
        f"Ученик: `{student_tg_username}`\n"
        f"Кто подтвердил: @{teacher_username}"
    )
    try:
        await callback.message.edit_caption(
            caption=new_caption,
            parse_mode="Markdown",
            reply_markup=None,
        )
    except Exception:
        pass

    # Уведомляем родителя
    if parent_chat_id:
        try:
            await bot.send_message(
                chat_id=parent_chat_id,
                text=(
                    f"✅ *Оплата подтверждена!*\n\n"
                    f"ID: `{payment_id}`\n"
                    f"Сумма: *{_cents_to_byn(amount)}*\n"
                    f"Баланс пополнен."
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"Не удалось уведомить родителя: {e}")

    # Убираем из Redis
    try:
        await redis_queue.remove_payment(payment_id, teacher_tg)
    except Exception as e:
        logger.error(f"Ошибка удаления платежа из Redis: {e}")

    # Возвращаемся к очереди
    await cb_teacher_queue(callback)


# ── Учитель: отклонение оплаты ──


@router.callback_query(F.data.startswith("t_reject:"))
async def cb_teacher_reject_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Учитель нажал ❌ Отклонить — показываем причины."""
    payment_id = callback.data.split(":")[1]

    await state.update_data(reject_payment_id=payment_id)

    await callback.answer()
    await callback.message.answer(
        "Выберите причину отклонения:",
        reply_markup=reject_reason_keyboard(payment_id),
    )


@router.callback_query(F.data.startswith("t_reject_reason:"))
async def cb_teacher_reject_reason(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Учитель выбрал причину отклонения."""
    parts = callback.data.split(":")
    payment_id = parts[1]
    reason_idx = int(parts[2])
    reason = REJECT_REASONS[reason_idx]

    if reason == "Другое":
        await state.set_state(RejectReason.waiting_for_custom_reason)
        await callback.answer()
        await callback.message.answer(
            "📝 Введите причину отклонения текстом:",
        )
        return

    await _execute_reject(callback, payment_id, reason, state, bot)


@router.message(RejectReason.waiting_for_custom_reason)
async def teacher_custom_reject_reason(message: Message, state: FSMContext, bot: Bot) -> None:
    """Учитель ввёл свою причину отклонения."""
    data = await state.get_data()
    payment_id = data.get("reject_payment_id", "")
    reason = message.text.strip() or "Без причины"

    await _execute_reject_from_message(message, payment_id, reason, state, bot)


@router.callback_query(F.data.startswith("t_reject_cancel:"))
async def cb_teacher_reject_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Учитель передумал отклонять."""
    await state.clear()
    await callback.answer("Отмена")
    try:
        await callback.message.delete()
    except Exception:
        pass


async def _execute_reject(
    callback: CallbackQuery,
    payment_id: str,
    reason: str,
    state: FSMContext,
    bot: Bot,
) -> None:
    """Общая логика отклонения (из callback)."""
    await _do_reject(callback.from_user.username or str(callback.from_user.id), 
                     payment_id, reason, state, bot)
    
    await callback.answer("Отклонено")
    try:
        await callback.message.delete()
    except Exception:
        pass
    
    # Возвращаемся к очереди
    await cb_teacher_queue(callback)


async def _execute_reject_from_message(
    message: Message,
    payment_id: str,
    reason: str,
    state: FSMContext,
    bot: Bot,
) -> None:
    """Отклонение из текстового сообщения."""
    await _do_reject(message.from_user.username or str(message.from_user.id),
                     payment_id, reason, state, bot)

    await message.answer(f"❌ Платёж `{payment_id}` отклонён. Причина: {reason}", parse_mode="Markdown")


async def _do_reject(
    teacher_username: str,
    payment_id: str,
    reason: str,
    state: FSMContext,
    bot: Bot,
) -> None:
    """Выполнить отклонение: API + Redis + уведомление родителю."""
    try:
        payment = await redis_queue.get_payment(payment_id)
    except Exception:
        payment = None

    student_tg = payment.get("student_tg_username", "") if payment else ""
    teacher_tg = payment.get("teacher_tg_username", teacher_username) if payment else teacher_username
    parent_chat_id = int(payment.get("parent_chat_id", 0)) if payment else 0

    await api.reject_payment(
        teacher_tg_username=f"@{teacher_username}",
        student_tg_username=student_tg.lstrip("@"),
        comment=reason,
    )

    # Уведомляем родителя
    if parent_chat_id:
        try:
            await bot.send_message(
                chat_id=parent_chat_id,
                text=(
                    f"❌ *Оплата отклонена*\n\n"
                    f"ID: `{payment_id}`\n"
                    f"Причина: {reason}"
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"Не удалось уведомить родителя: {e}")

    # Убираем из Redis
    try:
        await redis_queue.remove_payment(payment_id, teacher_tg)
    except Exception as e:
        logger.error(f"Ошибка удаления платежа из Redis: {e}")

    await state.clear()


# ═══════════════════════════════════════════════════════════════
# Ученик
# ═══════════════════════════════════════════════════════════════


async def _handle_student_start(message: Message, result: dict, name: str) -> None:
    """Приветствие ученика. Сохраняем tg_chat_id и показываем меню."""
    tg_username = result.get("tg_username", message.from_user.username or "")

    # Сохраняем chat_id ученика на платформе (для уведомлений и восстановления пароля)
    if tg_username:
        reg_result = await api.register_chat(tg_username, message.chat.id)
        if reg_result.get("found"):
            logger.info(f"Chat registered for student @{tg_username}: chat_id={message.chat.id}")
        else:
            logger.warning(f"Failed to register chat for student @{tg_username}: {reg_result}")

    await message.answer(
        f"👋 Привет, {name}!\n\nВыберите действие:",
        reply_markup=student_menu_keyboard(),
    )


# ═══════════════════════════════════════════════════════════════
# Хелперы
# ═══════════════════════════════════════════════════════════════


async def _try_edit_or_answer(
    message: Message, text: str, *, parse_mode: str = None, reply_markup=None
) -> None:
    """Пробует отредактировать сообщение; если не выходит — шлёт новое.

    Используется для кнопок «Назад» и навигации, чтобы не плодить окна.
    """
    try:
        await message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception:
        await message.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)


async def _navigate_back_to_menu(callback_or_msg: CallbackQuery, student_id: int) -> None:
    """Возврат в меню после действия."""
    # Просто шлём новое сообщение с меню
    balance_data = await api.get_student_balance(student_id, limit=0)
    child = {
        "id": student_id,
        "name": balance_data.get("student_name", f"Ученик {student_id}"),
        "balance": balance_data.get("balance", 0),
        "teacher_name": "—",
    }
    await _show_parent_menu(callback_or_msg.message, child)
