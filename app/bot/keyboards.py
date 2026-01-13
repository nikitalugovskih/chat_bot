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