from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.bot.keyboards import admins_keyboard, admins_back_keyboard, users_picker_keyboard
from app.bot.states import AdminFlow

from zoneinfo import ZoneInfo

router = Router()

def is_admin(chat_id: int, settings) -> bool:
    return chat_id in settings.admin_ids

def fmt_user(u) -> str:
    status = "paid ✅" if u.subscribe == 1 else "free ❌"
    left = "анлим" if u.num_request is None else str(u.num_request)
    uname = f"@{u.username}" if getattr(u, "username", None) else "-"
    full = getattr(u, "full_name", None) or "-"

    return (
        f"chat_id={u.chat_id}\n"
        f"username={uname}\n"
        f"full_name={full}\n"
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

    users = await repo.list_users()
    if not users:
        await call.message.edit_text("Пользователей пока нет.", reply_markup=admins_back_keyboard())
        return

    # Telegram ограничивает длину сообщения, поэтому выводим кратко
    lines = []
    for u in users[:200]:
        status = "paid" if u.subscribe == 1 else "free"
        endp = u.end_payment_date if u.end_payment_date else "-"
        left = "∞" if u.num_request is None else u.num_request
        name = f"@{u.username}" if u.username else (u.full_name or "-")
        lines.append(f"{u.chat_id} | {name} | {status} | left={left} | today={u.total_requests} | end={endp}")


    text = "👥 Пользователи:\n" + "\n".join(lines)
    if len(text) > 3800:
        text = text[:3800] + "\n... (обрезано)"
    await call.message.edit_text(text, reply_markup=admins_back_keyboard())

@router.callback_query(F.data == "adm:check_user")
async def adm_check_user(call: CallbackQuery, repo, settings):
    if not is_admin(call.message.chat.id, settings):
        await call.answer("Нет доступа", show_alert=True)
        return
    users = await repo.list_users()
    await call.message.edit_text(
        "Выбери пользователя для просмотра:",
        reply_markup=users_picker_keyboard(users, action="check", page=0),
    )

@router.message(AdminFlow.waiting_chat_id_for_check)
async def adm_check_user_input(message: Message, repo, settings, state: FSMContext):
    if not is_admin(message.chat.id, settings):
        return

    if not (message.text and message.text.strip().isdigit()):
        await message.answer("Нужен chat_id (число). Попробуй ещё раз:")
        return

    chat_id = int(message.text.strip())
    u = await repo.get_user(chat_id)
    await state.clear()
    await message.answer("🔎 Данные пользователя:\n\n" + fmt_user(u), reply_markup=admins_keyboard())

@router.callback_query(F.data == "adm:grant_30")
async def adm_grant_30(call: CallbackQuery, repo, settings):
    if not is_admin(call.message.chat.id, settings):
        await call.answer("Нет доступа", show_alert=True)
        return
    users = await repo.list_users()
    await call.message.edit_text(
        "Выбери пользователя — выдам/продлю 30 дней:",
        reply_markup=users_picker_keyboard(users, action="grant", page=0),
    )

@router.message(AdminFlow.waiting_chat_id_for_grant)
async def adm_grant_30_input(message: Message, repo, settings, state: FSMContext):
    if not is_admin(message.chat.id, settings):
        return

    if not (message.text and message.text.strip().isdigit()):
        await message.answer("Нужен chat_id (число). Попробуй ещё раз:")
        return

    chat_id = int(message.text.strip())
    u = await repo.admin_extend_paid_30d(chat_id)
    await state.clear()
    await message.answer(
        f"✅ Готово. Подписка активна до {u.end_payment_date}\n\n" + fmt_user(u),
        reply_markup=admins_keyboard()
    )

@router.callback_query(F.data == "adm:reset_sub")
async def adm_reset_sub(call: CallbackQuery, repo, settings):
    if not is_admin(call.message.chat.id, settings):
        await call.answer("Нет доступа", show_alert=True)
        return
    users = await repo.list_users()
    await call.message.edit_text(
        "Выбери пользователя — сброшу подписку (free):",
        reply_markup=users_picker_keyboard(users, action="reset", page=0),
    )

@router.message(AdminFlow.waiting_chat_id_for_reset)
async def adm_reset_sub_input(message: Message, repo, settings, state: FSMContext):
    if not is_admin(message.chat.id, settings):
        return

    if not (message.text and message.text.strip().isdigit()):
        await message.answer("Нужен chat_id (число). Попробуй ещё раз:")
        return

    chat_id = int(message.text.strip())
    u = await repo.admin_reset_subscription(chat_id)
    await state.clear()
    await message.answer("♻️ Подписка сброшена.\n\n" + fmt_user(u), reply_markup=admins_keyboard())

@router.callback_query(F.data == "adm:delete_user")
async def adm_delete_user(call: CallbackQuery, repo, settings):
    if not is_admin(call.message.chat.id, settings):
        await call.answer("Нет доступа", show_alert=True)
        return
    users = await repo.list_users()
    await call.message.edit_text(
        "Выбери пользователя — удалю из БД (и логи тоже):",
        reply_markup=users_picker_keyboard(users, action="delete", page=0),
    )

@router.message(AdminFlow.waiting_chat_id_for_delete)
async def adm_delete_user_input(message: Message, repo, settings, state: FSMContext):
    if not is_admin(message.chat.id, settings):
        return

    if not (message.text and message.text.strip().isdigit()):
        await message.answer("Нужен chat_id (число). Попробуй ещё раз:")
        return

    chat_id = int(message.text.strip())
    await repo.admin_delete_user(chat_id)
    await state.clear()
    await message.answer(f"🗑 Пользователь {chat_id} удалён.", reply_markup=admins_keyboard())

@router.callback_query(F.data.startswith("adm:users:"))
async def adm_users_page(call: CallbackQuery, repo, settings):
    if not is_admin(call.message.chat.id, settings):
        await call.answer("Нет доступа", show_alert=True)
        return

    # adm:users:<action>:<page>
    _, _, action, page_s = call.data.split(":")
    page = int(page_s)

    users = await repo.list_users()
    if not users:
        await call.message.edit_text("Пользователей пока нет.", reply_markup=admins_back_keyboard())
        return

    await call.message.edit_text(
        f"Выбери пользователя для действия: {action}",
        reply_markup=users_picker_keyboard(users, action=action, page=page),
    )

@router.callback_query(F.data.startswith("adm:pick:"))
async def adm_pick_user(call: CallbackQuery, repo, settings):
    if not is_admin(call.message.chat.id, settings):
        await call.answer("Нет доступа", show_alert=True)
        return

    # adm:pick:<action>:<chat_id>
    _, _, action, chat_id_s = call.data.split(":")
    chat_id = int(chat_id_s)

    if action == "check":
        u = await repo.get_user(chat_id)
        await call.message.edit_text("🔎 Данные пользователя:\n\n" + fmt_user(u), reply_markup=admins_back_keyboard())
        return

    if action == "grant":
        u = await repo.admin_extend_paid_30d(chat_id)
        await call.message.edit_text("✅ Выдал/продлил paid.\n\n" + fmt_user(u), reply_markup=admins_back_keyboard())
        return

    if action == "reset":
        u = await repo.admin_reset_subscription(chat_id)
        await call.message.edit_text("♻️ Сбросил подписку.\n\n" + fmt_user(u), reply_markup=admins_back_keyboard())
        return

    if action == "delete":
        await repo.admin_delete_user(chat_id)
        await call.message.edit_text(f"🗑 Пользователь {chat_id} удалён.", reply_markup=admins_back_keyboard())
        return

    await call.answer("Неизвестное действие", show_alert=True)

@router.callback_query(F.data.startswith("adm:manual:"))
async def adm_manual(call: CallbackQuery, settings, state: FSMContext):
    if not is_admin(call.message.chat.id, settings):
        await call.answer("Нет доступа", show_alert=True)
        return

    # adm:manual:<action>
    _, _, action = call.data.split(":")
    if action == "check":
        await state.set_state(AdminFlow.waiting_chat_id_for_check)
    elif action == "grant":
        await state.set_state(AdminFlow.waiting_chat_id_for_grant)
    elif action == "reset":
        await state.set_state(AdminFlow.waiting_chat_id_for_reset)
    elif action == "delete":
        await state.set_state(AdminFlow.waiting_chat_id_for_delete)
    else:
        await call.answer("Неизвестное действие", show_alert=True)
        return

    await call.message.edit_text("Введи chat_id вручную:", reply_markup=admins_back_keyboard())

@router.callback_query(F.data == "adm:stars")
async def adm_stars(call: CallbackQuery, repo, settings):
    if not is_admin(call.message.chat.id, settings):
        await call.answer("Нет доступа", show_alert=True)
        return

    total = await repo.stars_total()
    top = await repo.stars_top_donors(limit=15)
    last = await repo.stars_last_payments(limit=10)

    lines = [f"⭐️ Stars (по нашей БД payments): {total}\n"]

    if top:
        lines.append("🏆 Топ доноров:")
        for i, r in enumerate(top, 1):
            name = (f"@{r['username']}" if r["username"] else r["full_name"]).strip()
            if not name:
                name = "—"
            lines.append(f"{i}) {r['chat_id']} | {name} | ⭐️ {int(r['stars'])}")
    else:
        lines.append("🏆 Топ доноров: пока пусто")

    lines.append("")

    if last:
        lines.append("🕒 Последние донаты:")
        for r in last:
            name = (f"@{r['username']}" if r["username"] else r["full_name"]).strip()
            if not name:
                name = "—"

            dt = r["created_at"]  # datetime с tz из Postgres
            dt_msk = dt.astimezone(ZoneInfo("Europe/Moscow"))
            dt_str = dt_msk.strftime("%d.%m.%Y %H:%M:%S")

            lines.append(f"{dt_str} | {r['chat_id']} | {name} | ⭐️ {int(r['amount'])}")
    else:
        lines.append("🕒 Последние донаты: пока пусто")

    text = "\n".join(lines)
    if len(text) > 3800:
        text = text[:3800] + "\n…"

    await call.message.edit_text(text, reply_markup=admins_back_keyboard())