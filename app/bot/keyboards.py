# inline клавиатуры

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

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