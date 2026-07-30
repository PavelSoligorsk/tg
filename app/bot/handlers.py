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
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command

from app.bot.api_client import api, PlatformAPI
from app.bot.states import PaymentFlow, RejectReason, SelectStudent
from app.bot.keyboards import (
    parent_menu_keyboard,
    children_selection_keyboard,
    payment_confirm_keyboard,
    teacher_payment_keyboard,
    reject_reason_keyboard,
    back_to_main_keyboard,
    REJECT_REASONS,
)

logger = logging.getLogger(__name__)

router = Router()

# ── Временное хранилище платёжных запросов (pending перед подтверждением) ──
# {payment_id: {student_tg, amount, parent_msg_id, parent_chat_id}}
_pending_payments: dict[str, dict] = {}

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
    await message.answer(
        text, reply_markup=parent_menu_keyboard(student_id), parse_mode="Markdown"
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

    # Ищем chat_id учителя ученика через платформу
    teacher_chat_data = await api.get_teacher_chat(student_id)
    if not teacher_chat_data.get("found") or not teacher_chat_data.get("chat_id"):
        await callback.message.answer(
            "❌ Учитель ещё не активировал бота.\n\n"
            "Пожалуйста, попросите вашего преподавателя написать /start этому боту."
        )
        await state.clear()
        return

    teacher_chat_id = teacher_chat_data["chat_id"]

    # Пересылаем фото учителю
    try:
        sent = await bot.send_photo(
            chat_id=teacher_chat_id,
            photo=photo_file_id,
            caption=(
                f"📎 *Новый чек на проверку*\n\n"
                f"ID: `{payment_id}`\n"
                f"Сумма: *{amount_byn:.2f} BYN*\n"
                f"Родитель: @{callback.from_user.username}\n"
            ),
            parse_mode="Markdown",
            reply_markup=teacher_payment_keyboard(
                payment_id=payment_id,
                student_tg_username=student_tg_username,
                amount=amount_kop,
            ),
        )
        teacher_msg_id = sent.message_id
    except Exception as e:
        logger.error(f"Ошибка пересылки чека учителю: {e}")
        await callback.message.answer("❌ Не удалось отправить чек. Попробуйте позже.")
        await state.clear()
        return

    # Сохраняем платёж
    _pending_payments[payment_id] = {
        "student_tg_username": student_tg_username,
        "amount": amount_kop,
        "parent_chat_id": callback.message.chat.id,
        "parent_msg_id": callback.message.message_id,
        "photo_file_id": photo_file_id,
        "teacher_msg_id": teacher_msg_id,
        "payment_id": payment_id,
    }

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

    await callback.message.answer(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=back_to_main_keyboard(student_id),
    )


# ── Статистика ──


@router.callback_query(F.data.startswith("stats:"))
async def cb_stats(callback: CallbackQuery) -> None:
    """Заглушка статистики."""
    student_id = int(callback.data.split(":")[1])
    await callback.answer()

    await callback.message.answer(
        "📊 *Статистика*\n\nСкоро появится! 🚀\n",
        parse_mode="Markdown",
        reply_markup=back_to_main_keyboard(student_id),
    )


# ═══════════════════════════════════════════════════════════════
# Учитель
# ═══════════════════════════════════════════════════════════════


async def _handle_teacher_start(message: Message, result: dict, name: str) -> None:
    """Приветствие учителя. Сохраняем tg_chat_id на бэкенде."""
    students_count = result.get("students_count", 0)
    tg_username = result.get("tg_username", message.from_user.username or "")

    # Сохраняем chat_id учителя на платформе для маршрутизации чеков
    if tg_username:
        reg_result = await api.register_chat(tg_username, message.chat.id)
        if reg_result.get("found"):
            logger.info(f"Chat registered for teacher @{tg_username}: chat_id={message.chat.id}")
        else:
            logger.warning(f"Failed to register chat for teacher @{tg_username}: {reg_result}")

    await message.answer(
        f"👋 Здравствуйте, {name}!\n\n"
        f"У вас {students_count} учеников.\n\n"
        f"Когда родитель пришлёт чек, вы получите уведомление "
        f"с кнопками ✅ Подтвердить и ❌ Отклонить."
    )


# ── Учитель: подтверждение оплаты ──


@router.callback_query(F.data.startswith("t_confirm:"))
async def cb_teacher_confirm(callback: CallbackQuery, bot: Bot) -> None:
    """Учитель нажал ✅ Подтвердить."""
    parts = callback.data.split(":")
    payment_id = parts[1]
    student_tg_username = parts[2]
    amount = int(parts[3])

    payment_info = _pending_payments.get(payment_id)

    # Получаем username учителя
    teacher_username = callback.from_user.username or str(callback.from_user.id)

    # Вызываем платформу
    result = await api.confirm_payment(
        teacher_tg_username=f"@{teacher_username}",
        student_tg_username=f"@{student_tg_username}",
        amount=amount,
        payment_type="per_lesson",
        comment=f"Подтверждено учителем @{teacher_username} через бота",
    )

    if result.get("error"):
        await callback.answer(f"Ошибка: {result['error']}", show_alert=True)
        return

    await callback.answer("✅ Подтверждено!")

    # Обновляем сообщение учителю
    new_text = (
        f"✅ *Платёж подтверждён!*\n\n"
        f"ID: `{payment_id}`\n"
        f"Сумма: *{_cents_to_byn(amount)}*\n"
        f"Ученик: `{student_tg_username}`\n"
        f"Кто подтвердил: @{teacher_username}"
    )
    await callback.message.edit_caption(
        caption=new_text,
        parse_mode="Markdown",
        reply_markup=None,
    )

    # Уведомляем родителя
    if payment_info:
        parent_chat_id = payment_info.get("parent_chat_id")
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

    # Убираем из pending
    _pending_payments.pop(payment_id, None)


# ── Учитель: отклонение оплаты ──


@router.callback_query(F.data.startswith("t_reject:"))
async def cb_teacher_reject_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Учитель нажал ❌ Отклонить — показываем причины."""
    parts = callback.data.split(":")
    payment_id = parts[1]
    student_tg = parts[2]

    await state.update_data(reject_payment_id=payment_id, reject_student_tg=student_tg)

    await callback.answer()
    await callback.message.answer(
        "Выберите причину отклонения:",
        reply_markup=reject_reason_keyboard(payment_id, student_tg),
    )


@router.callback_query(F.data.startswith("t_reject_reason:"))
async def cb_teacher_reject_reason(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Учитель выбрал причину отклонения."""
    parts = callback.data.split(":")
    payment_id = parts[1]
    student_tg = parts[2]
    reason_idx = int(parts[3])
    reason = REJECT_REASONS[reason_idx]

    if reason == "Другое":
        await state.set_state(RejectReason.waiting_for_custom_reason)
        await callback.answer()
        await callback.message.answer(
            "📝 Введите причину отклонения текстом:",
        )
        return

    await _execute_reject(callback, payment_id, student_tg, reason, state, bot)


@router.message(RejectReason.waiting_for_custom_reason)
async def teacher_custom_reject_reason(message: Message, state: FSMContext, bot: Bot) -> None:
    """Учитель ввёл свою причину отклонения."""
    data = await state.get_data()
    payment_id = data.get("reject_payment_id", "")
    student_tg = data.get("reject_student_tg", "")
    reason = message.text.strip()

    if not reason:
        reason = "Без причины"

    await _execute_reject_from_message(message, payment_id, student_tg, reason, state, bot)


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
    student_tg: str,
    reason: str,
    state: FSMContext,
    bot: Bot,
) -> None:
    """Общая логика отклонения."""
    payment_info = _pending_payments.get(payment_id)
    teacher_username = callback.from_user.username or str(callback.from_user.id)

    # Вызываем платформу
    await api.reject_payment(
        teacher_tg_username=f"@{teacher_username}",
        student_tg_username=f"@{student_tg}",
        comment=reason,
    )

    await callback.answer("Отклонено")

    # Обновляем сообщение учителю
    try:
        await callback.message.delete()
    except Exception:
        pass

    # Уведомляем родителя
    if payment_info:
        parent_chat_id = payment_info.get("parent_chat_id")
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

    _pending_payments.pop(payment_id, None)
    await state.clear()


async def _execute_reject_from_message(
    message: Message,
    payment_id: str,
    student_tg: str,
    reason: str,
    state: FSMContext,
    bot: Bot,
) -> None:
    """Отклонение из текстового сообщения."""
    payment_info = _pending_payments.get(payment_id)
    teacher_username = message.from_user.username or str(message.from_user.id)

    await api.reject_payment(
        teacher_tg_username=f"@{teacher_username}",
        student_tg_username=f"@{student_tg}",
        comment=reason,
    )

    await message.answer(f"❌ Платёж `{payment_id}` отклонён. Причина: {reason}", parse_mode="Markdown")

    if payment_info:
        parent_chat_id = payment_info.get("parent_chat_id")
        if parent_chat_id:
            try:
                await bot.send_message(
                    chat_id=parent_chat_id,
                    text=f"❌ *Оплата отклонена*\n\nID: `{payment_id}`\nПричина: {reason}",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Не удалось уведомить родителя: {e}")

    _pending_payments.pop(payment_id, None)
    await state.clear()


# ═══════════════════════════════════════════════════════════════
# Ученик
# ═══════════════════════════════════════════════════════════════


async def _handle_student_start(message: Message, result: dict, name: str) -> None:
    """Заглушка для ученика."""
    await message.answer(
        f"👋 Привет, {name}!\n\n"
        f"Функционал для учеников скоро появится! 🚀\n"
        f"А пока пользуйтесь веб-версией платформы."
    )


# ═══════════════════════════════════════════════════════════════
# Хелперы
# ═══════════════════════════════════════════════════════════════


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
