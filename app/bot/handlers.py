# /start, кнопки, сообщения

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext

from app.bot.keyboards import start_keyboard, subscription_keyboard
from app.bot.states import ChatFlow

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
    u = repo.activate_paid_30d(chat_id)
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
        answer = llm.generate(user_text)
    except Exception as e:
        await message.answer(f"⚠️ Ошибка модели: {e}")
        return

    # "Одно действие": обновили user_subscriptions + вставили requests_log
    repo.record_interaction_atomic(chat_id=chat_id, user_input=user_text, model_output=answer)

    await message.answer(answer)