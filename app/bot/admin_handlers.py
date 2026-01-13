from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.bot.keyboards import admins_keyboard, admins_back_keyboard
from app.bot.states import AdminFlow

router = Router()

def is_admin(chat_id: int, settings) -> bool:
    return chat_id in settings.admin_ids

def fmt_user(u) -> str:
    status = "paid ✅" if u.subscribe == 1 else "free ❌"
    left = "анлим" if u.num_request is None else str(u.num_request)
    return (
        f"chat_id={u.chat_id}\n"
        f"status={status}\n"
        f"start_day={u.date}\n"
        f"total_today={u.total_requests}\n"
        f"left={left}\n"
        f"end_payment={u.end_payment_date}\n"
    )

@router.message(Command("admins"))
async def admins_cmd(message: Message, settings, state: FSMContext):
    if not is_admin(message.chat.id, settings):
        return
    await state.clear()
    await message.answer("🛠 Админ-панель:", reply_markup=admins_keyboard())

@router.callback_query(F.data == "adm:back")
async def adm_back(call: CallbackQuery, settings, state: FSMContext):
    if not is_admin(call.message.chat.id, settings):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await call.message.edit_text("🛠 Админ-панель:", reply_markup=admins_keyboard())

@router.callback_query(F.data == "adm:list_users")
async def adm_list_users(call: CallbackQuery, repo, settings):
    if not is_admin(call.message.chat.id, settings):
        await call.answer("Нет доступа", show_alert=True)
        return

    users = repo.list_users()
    if not users:
        await call.message.edit_text("Пользователей пока нет.", reply_markup=admins_back_keyboard())
        return

    # Telegram ограничивает длину сообщения, поэтому выводим кратко
    lines = []
    for u in users[:200]:
        status = "paid" if u.subscribe == 1 else "free"
        endp = u.end_payment_date if u.end_payment_date else "-"
        left = "∞" if u.num_request is None else u.num_request
        lines.append(f"{u.chat_id} | {status} | left={left} | today={u.total_requests} | end={endp}")

    text = "👥 Пользователи:\n" + "\n".join(lines)
    await call.message.edit_text(text, reply_markup=admins_back_keyboard())

@router.callback_query(F.data == "adm:check_user")
async def adm_check_user(call: CallbackQuery, settings, state: FSMContext):
    if not is_admin(call.message.chat.id, settings):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminFlow.waiting_chat_id_for_check)
    await call.message.edit_text("Введи chat_id пользователя для просмотра подписки:", reply_markup=admins_back_keyboard())

@router.message(AdminFlow.waiting_chat_id_for_check)
async def adm_check_user_input(message: Message, repo, settings, state: FSMContext):
    if not is_admin(message.chat.id, settings):
        return

    if not (message.text and message.text.strip().isdigit()):
        await message.answer("Нужен chat_id (число). Попробуй ещё раз:")
        return

    chat_id = int(message.text.strip())
    u = repo.get_user(chat_id)
    await state.clear()
    await message.answer("🔎 Данные пользователя:\n\n" + fmt_user(u), reply_markup=admins_keyboard())

@router.callback_query(F.data == "adm:grant_30")
async def adm_grant_30(call: CallbackQuery, settings, state: FSMContext):
    if not is_admin(call.message.chat.id, settings):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminFlow.waiting_chat_id_for_grant)
    await call.message.edit_text("Введи chat_id пользователя — выдам/продлю подписку на 30 дней:", reply_markup=admins_back_keyboard())

@router.message(AdminFlow.waiting_chat_id_for_grant)
async def adm_grant_30_input(message: Message, repo, settings, state: FSMContext):
    if not is_admin(message.chat.id, settings):
        return

    if not (message.text and message.text.strip().isdigit()):
        await message.answer("Нужен chat_id (число). Попробуй ещё раз:")
        return

    chat_id = int(message.text.strip())
    u = repo.admin_extend_paid_30d(chat_id)
    await state.clear()
    await message.answer(f"✅ Готово. Подписка активна до {u.end_payment_date}\n\n" + fmt_user(u), reply_markup=admins_keyboard())

@router.callback_query(F.data == "adm:reset_sub")
async def adm_reset_sub(call: CallbackQuery, settings, state: FSMContext):
    if not is_admin(call.message.chat.id, settings):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminFlow.waiting_chat_id_for_reset)
    await call.message.edit_text("Введи chat_id пользователя — сброшу подписку (free):", reply_markup=admins_back_keyboard())

@router.message(AdminFlow.waiting_chat_id_for_reset)
async def adm_reset_sub_input(message: Message, repo, settings, state: FSMContext):
    if not is_admin(message.chat.id, settings):
        return

    if not (message.text and message.text.strip().isdigit()):
        await message.answer("Нужен chat_id (число). Попробуй ещё раз:")
        return

    chat_id = int(message.text.strip())
    u = repo.admin_reset_subscription(chat_id)
    await state.clear()
    await message.answer("♻️ Подписка сброшена.\n\n" + fmt_user(u), reply_markup=admins_keyboard())

@router.callback_query(F.data == "adm:delete_user")
async def adm_delete_user(call: CallbackQuery, settings, state: FSMContext):
    if not is_admin(call.message.chat.id, settings):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminFlow.waiting_chat_id_for_delete)
    await call.message.edit_text("Введи chat_id пользователя — удалю из БД (и логи тоже):", reply_markup=admins_back_keyboard())

@router.message(AdminFlow.waiting_chat_id_for_delete)
async def adm_delete_user_input(message: Message, repo, settings, state: FSMContext):
    if not is_admin(message.chat.id, settings):
        return

    if not (message.text and message.text.strip().isdigit()):
        await message.answer("Нужен chat_id (число). Попробуй ещё раз:")
        return

    chat_id = int(message.text.strip())
    repo.admin_delete_user(chat_id)
    await state.clear()
    await message.answer(f"🗑 Пользователь {chat_id} удалён.", reply_markup=admins_keyboard())