# /start, кнопки, сообщения

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext

from app.bot.keyboards import start_keyboard, subscription_keyboard
from app.bot.states import ChatFlow

from datetime import date

from app.utils.time import today_msk

import logging
import hashlib

logger = logging.getLogger("bot")

router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message, repo, state: FSMContext):
    chat_id = message.chat.id
    repo.get_user(chat_id)  # создаём пользователя сразу
    await state.clear()

    text = (
        "Привет! 👋\n"
        "Я чат-бот. Нажми «Начать», чтобы стартовать чат.\n"
        "Или «Подписка», чтобы посмотреть лимиты/оплату."
    )
    await message.answer(text, reply_markup=start_keyboard())

@router.callback_query(F.data == "start_chat")
async def cb_start_chat(call: CallbackQuery, state: FSMContext):
    await state.set_state(ChatFlow.chatting)
    await call.message.edit_text("Ок, пишите сообщение — я отвечу 🙂")

@router.callback_query(F.data == "subscription")
async def cb_subscription(call: CallbackQuery, repo):
    chat_id = call.message.chat.id
    u = repo.get_user(chat_id)

    paid_text = "да ✅" if u.subscribe == 1 else "нет ❌"
    left = "анлим" if u.num_request is None else str(u.num_request)

    text = (
        f"📌 Статус подписки: {paid_text}\n"
        f"📆 Дата (счётчики на день): {u.date}\n"
        f"🔢 Запросов сегодня: {u.total_requests}\n"
        f"🧾 Осталось запросов: {left}\n"
    )
    await call.message.edit_text(text, reply_markup=subscription_keyboard())

@router.callback_query(F.data == "pay_30d")
async def cb_pay(call: CallbackQuery, repo):
    chat_id = call.message.chat.id
    u = repo.get_user(chat_id)

    today = today_msk(repo.tz)
    already_active = (
        u.subscribe == 1
        and u.end_payment_date is not None
        and today <= u.end_payment_date
    )

    if already_active:
        # popup (можно show_alert=False, тогда это "тост" внизу)
        await call.answer("✅ Подписка уже активна 🙂", show_alert=True)
        return

    u = repo.activate_paid_30d(chat_id)

    # обязательно закрываем "часики" у callback
    await call.answer("✅ Оплата успешна!")

    await call.message.edit_text(
        f"✅ Подписка активирована до {u.end_payment_date}.\nТеперь запросы: анлим.",
        reply_markup=subscription_keyboard(),
    )

@router.callback_query(F.data == "back")
async def cb_back(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text(
        "Главное меню:", reply_markup=start_keyboard()
    )

@router.message(ChatFlow.chatting)
async def on_chat_message(message: Message, repo, llm):
    chat_id = message.chat.id
    user_text = message.text or ""

    ok, reason = repo.can_make_request(chat_id)
    if not ok:
        # если лимит — предлагаем оплату/подписку
        await message.answer(reason, reply_markup=subscription_keyboard())
        return

    # LLM
    try:
        # короткий превью промпта + хэш, чтобы понимать что за версия/контент
        prompt_text = getattr(__import__("app.services.openai_client", fromlist=["SYSTEM_PROMPT"]), "SYSTEM_PROMPT", "")
        prompt_version = getattr(__import__("app.services.openai_client", fromlist=["PROMPT_VERSION"]), "PROMPT_VERSION", "unknown")

        prompt_preview = prompt_text[:180].replace("\n", " ")
        prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:12]

        logger.info(
            "chat_id=%s | prompt=%s | prompt_hash=%s | prompt_preview='%s' | user_input='%s'",
            chat_id,
            prompt_version,
            prompt_hash,
            prompt_preview,
            (user_text[:300].replace("\n", " ")),
        )
        answer = llm.generate(user_text)
    except Exception as e:
        await message.answer(f"⚠️ Ошибка модели: {e}")
        return

    # "Одно действие": обновили user_subscriptions + вставили requests_log
    repo.record_interaction_atomic(chat_id=chat_id, user_input=user_text, model_output=answer)

    await message.answer(answer)