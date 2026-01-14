# inline клавиатуры

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Начать", callback_data="start_chat")],
        [InlineKeyboardButton(text="ℹ️ Подписка", callback_data="subscription")],
    ])

def subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплата (30 дней)", callback_data="pay_30d")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
    ])

# --- ADMIN ---
def admins_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Все пользователи", callback_data="adm:list_users")],
        [InlineKeyboardButton(text="🔎 Проверить подписку (chat_id)", callback_data="adm:check_user")],
        [InlineKeyboardButton(text="➕ Продлить/выдать +30 дней", callback_data="adm:grant_30")],
        [InlineKeyboardButton(text="♻️ Сбросить подписку", callback_data="adm:reset_sub")],
        [InlineKeyboardButton(text="🗑 Удалить пользователя", callback_data="adm:delete_user")],
    ])

def admins_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:back")],
    ])

def users_picker_keyboard(users, action: str, page: int = 0, per_page: int = 10) -> InlineKeyboardMarkup:
    """
    users: list[UserSubscription]
    action: 'check' | 'grant' | 'reset' | 'delete'
    callback: "adm:pick:<action>:<chat_id>"
    """
    start = page * per_page
    end = start + per_page
    chunk = users[start:end]

    kb = InlineKeyboardBuilder()

    # список юзеров
    for u in chunk:
        name = f"@{u.username}" if getattr(u, "username", None) else (getattr(u, "full_name", None) or "")
        label = f"{u.chat_id} {name}".strip()
        kb.row(InlineKeyboardButton(text=label, callback_data=f"adm:pick:{action}:{u.chat_id}"))

    # навигация
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"adm:users:{action}:{page-1}"))
    if end < len(users):
        nav_buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"adm:users:{action}:{page+1}"))
    if nav_buttons:
        kb.row(*nav_buttons)

    # ручной ввод + назад
    kb.row(InlineKeyboardButton(text="⌨️ Ввести chat_id вручную", callback_data=f"adm:manual:{action}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:back"))

    return kb.as_markup()