# /start, кнопки, сообщения

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.types import FSInputFile
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext

from app.bot.keyboards import start_keyboard, subscription_keyboard
from app.bot.states import ChatFlow

from datetime import date

from app.utils.time import today_msk

import logging
import hashlib

import asyncio

from aiogram.filters import Command

logger = logging.getLogger("bot")

router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message, repo, state: FSMContext):
    chat_id = message.chat.id

    # ✅ создать/обновить пользователя + обновить ник/имя
    await repo.get_user(chat_id)
    await repo.touch_user_profile(
        chat_id=chat_id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )

    await state.clear()

    text = (
        "Привет! 👋\n"
        "Я чат-бот Психолог. Нажми «Начать» или /start, чтобы стартовать чат.\n"
        "Или «Подписка», чтобы посмотреть лимиты/оплату."
    )
    await message.answer(text, reply_markup=start_keyboard())

@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message, repo):
    chat_id = message.chat.id
    u = await repo.get_user(chat_id)

    if u.subscribe == 1:
        text = (
            "✅ Подписка: PAID\n"
            f"📅 Дата покупки: {u.payment_date}\n"
            f"⏳ Действует до: {u.end_payment_date}\n"
        )
    else:
        # для free показываем дату начала “дня” в системе (у тебя в БД date = текущий день/счётчики)
        # но ты попросил “первое взаимодействие”. В текущей схеме это НЕ хранится отдельно.
        # Поэтому честно показываем то, что есть: u.date (дата счётчиков).
        text = (
            "❌ Подписка: FREE\n"
            f"📅 Дата в системе (счётчики на день): {u.date}\n"
        )

    await message.answer(text)

@router.message(Command("limits"))
async def cmd_limits(message: Message, repo):
    chat_id = message.chat.id
    u = await repo.get_user(chat_id)

    left = "анлим" if u.num_request is None else str(u.num_request)
    text = (
        "📊 Лимиты:\n"
        f"🧾 Осталось запросов: {left}\n"
        f"🔢 Запросов сегодня: {u.total_requests}\n"
    )
    await message.answer(text)


@router.message(Command("buy_subscribe"))
async def cmd_buy_subscribe(message: Message):
    await message.answer("🛠 В разработке.")


@router.message(Command("service"))
async def cmd_service(message: Message):
    await message.answer("В случае предложений/жалоб, пишите на эту почту: test@gmail.com")


@router.message(Command("ban_untill"))
async def cmd_ban_until(message: Message, repo):
    chat_id = message.chat.id
    u = await repo.get_user(chat_id)

    if u.ban_until is not None:
        await message.answer(f"⛔️ Вы в бане до: {u.ban_until}")
    else:
        await message.answer("✅ Вы не в бане!")

@router.callback_query(F.data == "start_chat")
async def cb_start_chat(call: CallbackQuery, state: FSMContext):
    await state.set_state(ChatFlow.chatting)
    await call.message.edit_text("Ок, пишите сообщение — я отвечу 🙂")

@router.callback_query(F.data == "subscription")
async def cb_subscription(call: CallbackQuery, repo):
    chat_id = call.message.chat.id
    u = await repo.get_user(chat_id)

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
    u = await repo.get_user(chat_id)

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

    u = await repo.activate_paid_30d(chat_id)

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

    await repo.touch_user_profile(
        chat_id=chat_id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )

    ok, reason = await repo.can_make_request(chat_id)
    if not ok:
        # если лимит — предлагаем оплату/подписку
        await message.answer(reason, reply_markup=subscription_keyboard())
        return

    # LLM
    loading_sticker = None
    loading_text = None

    try:
        # 1) показываем анимированный стикер + текст
        # loading_sticker = await message.answer_sticker(FSInputFile("app/assets/loader.tgs"))
        loading_text = await message.answer("🎲 Получил ваш запрос, думаю, как вам помочь…")

        # 2) твой лог + генерация
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

    finally:
        # 3) убираем лоадер (если получилось отправить)
        for m in (loading_sticker, loading_text):
            if m:
                try:
                    await m.delete()
                except Exception:
                    pass


    # "Одно действие": обновили user_subscriptions + вставили requests_log
    await repo.record_interaction_atomic(chat_id, user_text, answer)

    await message.answer(answer)