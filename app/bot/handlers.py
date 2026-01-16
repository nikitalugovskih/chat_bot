# /start, кнопки, сообщения

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.types import FSInputFile
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.types import LabeledPrice, PreCheckoutQuery
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from decimal import Decimal
import uuid
from app.services.yookassa_client import YooKassaClient, YooKassaConfig

from app.bot.keyboards import start_keyboard, subscription_keyboard, pay_methods_keyboard, yookassa_pay_keyboard
from app.bot.states import ChatFlow

from datetime import datetime

from app.utils.time import today_msk

import logging
import hashlib

import asyncio

logger = logging.getLogger("bot")

router = Router()
LAST_STARS_INVOICE: dict[int, int] = {}

from aiogram import F
from aiogram.types import Message

# --- КНОПКИ ГЛАВНОГО МЕНЮ (reply keyboard) ---

@router.message(F.text == "💬 Начать")
async def btn_start_chat(message: Message, state: FSMContext):
    await state.set_state(ChatFlow.chatting)
    await message.answer("Ок, пишите сообщение — я отвечу 🙂")


@router.message(F.text == "ℹ️ Подписка")
async def btn_subscription(message: Message, repo):
    chat_id = message.chat.id
    u = await repo.get_user(chat_id)

    paid_text = "да ✅" if u.subscribe == 1 else "нет ❌"
    left = "анлим" if u.num_request is None else str(u.num_request)

    text = (
        f"📌 Статус подписки: {paid_text}\n"
        f"📆 Дата (счётчики на день): {u.date}\n"
        f"🔢 Запросов сегодня: {u.total_requests}\n"
        f"🧾 Осталось запросов: {left}\n"
    )
    await message.answer(text, reply_markup=subscription_keyboard())


@router.message(F.text == "🛟 Поддержка")
async def btn_support(message: Message):
    # переиспользуем то, что уже есть в /service
    await cmd_service(message)

# payload для счета
def make_payload(chat_id: int) -> str:
    # уникальный payload чтобы отличать счета (не обязательно, но полезно)
    return f"sub_30d:{chat_id}:{int(datetime.now().timestamp())}"

async def send_stars_invoice(message: Message, chat_id: int, stars_price: int = 1):
    inv_msg = await message.answer_invoice(
        title="Подписка на 30 дней",
        description="Анлим запросов в боте",
        payload=make_payload(chat_id),
        currency="XTR",
        prices=[LabeledPrice(label="Подписка 30 дней", amount=stars_price)],
        provider_token="",
    )
    LAST_STARS_INVOICE[chat_id] = inv_msg.message_id

@router.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)

@router.message(F.successful_payment)
async def successful_payment(message: Message, repo):
    chat_id = message.chat.id
    sp = message.successful_payment

    payload = getattr(sp, "invoice_payload", "")
    if not payload.startswith(f"sub_30d:{chat_id}:"):
        await message.answer("⚠️ Неизвестный платеж. Напишите в поддержку: test@gmail.com")
        return

    # 1) лог в payments
    try:
        await repo.log_payment_stars(chat_id, sp)
    except Exception as e:
        logger.exception("Failed to log payment: %r", e)

    # 2) активируем подписку
    u = await repo.activate_paid_30d(chat_id)

    # 3) удаляем сообщение-инвойс (если запоминали его message_id) и сервисное сообщение об оплате
    invoice_mid = LAST_STARS_INVOICE.pop(chat_id, None)
    if invoice_mid:
        try:
            await message.bot.delete_message(chat_id, invoice_mid)
        except (TelegramBadRequest, TelegramForbiddenError):
            pass

    try:
        await message.delete()  # удалит service-сообщение successful_payment
    except (TelegramBadRequest, TelegramForbiddenError):
        pass

    # 4) чек
    amount = getattr(sp, "total_amount", None)  # для XTR это количество звёзд
    lines = [
        "✅ Оплата прошла",
        f"Подписка активна до {u.end_payment_date}",
    ]
    if amount is not None:
        lines.append(f"Сумма: ⭐{amount}")

    await message.answer("\n".join(lines))


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
        "Я чат-бот компаньон! Нажми «Начать», чтобы запустить чат со мной.\n"
        "Или «Подписка», чтобы посмотреть лимиты/оплату.\n"
        "Если нужна поддержка, жми «Поддержка».\n"
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
    await send_stars_invoice(message, message.chat.id, stars_price=1)


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
async def cb_pay(call: CallbackQuery, repo, settings):
    chat_id = call.message.chat.id
    u = await repo.get_user(chat_id)

    today = today_msk(repo.tz)
    already_active = (
        u.subscribe == 1
        and u.end_payment_date is not None
        and today <= u.end_payment_date
    )

    if already_active:
        await call.answer("✅ Подписка уже активна 🙂", show_alert=True)
        return

    await call.answer()
    # вместо мгновенного Stars — показываем выбор метода
    await call.message.edit_reply_markup(reply_markup=pay_methods_keyboard())

@router.callback_query(F.data == "pay_methods:back")
async def cb_pay_methods_back(call: CallbackQuery, repo):
    # возвращаемся к экрану "Подписка" (текущий текст пересоздавать не будем — только клаву)
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=subscription_keyboard())

@router.callback_query(F.data == "pay_method:stars")
async def cb_pay_method_stars(call: CallbackQuery, repo):
    chat_id = call.message.chat.id
    u = await repo.get_user(chat_id)

    today = today_msk(repo.tz)
    already_active = (
        u.subscribe == 1
        and u.end_payment_date is not None
        and today <= u.end_payment_date
    )

    if already_active:
        await call.answer("✅ Подписка уже активна 🙂", show_alert=True)
        return

    await call.answer()
    await send_stars_invoice(call.message, chat_id, stars_price=1)

@router.callback_query(F.data == "pay_method:card")
async def cb_pay_method_card(call: CallbackQuery, repo, settings):
    chat_id = call.message.chat.id
    u = await repo.get_user(chat_id)

    today = today_msk(repo.tz)
    already_active = (
        u.subscribe == 1
        and u.end_payment_date is not None
        and today <= u.end_payment_date
    )
    if already_active:
        await call.answer("✅ Подписка уже активна 🙂", show_alert=True)
        return

    if not settings.yookassa_enabled:
        await call.answer("💳 Оплата картой выключена", show_alert=True)
        return

    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        await call.answer("⚠️ YooKassa ключи не настроены", show_alert=True)
        return

    await call.answer()

    # ✅ 1) Сначала пытаемся переиспользовать свежий pending (до 10 минут)
    recent = await repo.yk_get_recent_pending(chat_id, ttl_minutes=10)
    if recent:
        payment_id = recent["external_payment_id"]
        confirmation_url = recent["confirmation_url"]
        await call.message.answer(
            "⏳ У вас уже есть созданный платеж (он действует около 10 минут).\n"
            "Используйте кнопку ниже:",
            reply_markup=yookassa_pay_keyboard(confirmation_url, payment_id),
        )
        return

    # ✅ 2) Если свежего pending нет — создаём новый
    amount_value = settings.card_price_rub.strip()  # "199.00"
    amount_kopecks = int((Decimal(amount_value) * 100).to_integral_value())

    idem_key = str(uuid.uuid4())
    payload = make_payload(chat_id)

    client = YooKassaClient(
        YooKassaConfig(
            shop_id=settings.yookassa_shop_id,
            secret_key=settings.yookassa_secret_key,
            return_url=(settings.yookassa_return_url or "https://t.me/"),
        )
    )

    yk_payment, yk_meta = await client.create_payment(
        amount_value=amount_value,
        currency="RUB",
        description="Подписка на 30 дней",
        idempotence_key=idem_key,
        metadata={"chat_id": str(chat_id), "payload": payload},
        force_bank_card=True,
    )

    external_payment_id = yk_payment.get("id", "")
    status = yk_payment.get("status", "pending")
    confirmation_url = (yk_payment.get("confirmation") or {}).get("confirmation_url", "")

    if not external_payment_id or not confirmation_url:
        await call.message.answer("⚠️ Не удалось создать платеж. Попробуйте позже.")
        return

    raw_to_store = {"payment": yk_payment, "_meta": yk_meta}

    await repo.yk_insert_payment(
        chat_id=chat_id,
        amount=amount_kopecks,
        payload=payload,
        status=status,
        external_payment_id=external_payment_id,
        idempotence_key=idem_key,
        confirmation_url=confirmation_url,
        raw=raw_to_store,
    )

    await call.message.answer(
        "💳 Платеж создан.\n"
        "1) Нажмите «Перейти к оплате»\n"
        "2) После оплаты нажмите «✅ Я оплатил»\n\n",
        # f"debug: payment_id={external_payment_id}\n"
        # f"debug: idempotence_key={idem_key}",
        reply_markup=yookassa_pay_keyboard(confirmation_url, external_payment_id),
    )

@router.callback_query(F.data.startswith("yk_check:"))
async def cb_yk_check(call: CallbackQuery, repo, settings):
    if not settings.yookassa_enabled or not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        await call.answer("💳 Оплата картой недоступна", show_alert=True)
        return

    await call.answer()

    payment_id = (call.data or "").split("yk_check:", 1)[-1].strip()
    if not payment_id:
        await call.message.answer("⚠️ Некорректный платеж.")
        return

    client = YooKassaClient(
        YooKassaConfig(
            shop_id=settings.yookassa_shop_id,
            secret_key=settings.yookassa_secret_key,
            return_url=(settings.yookassa_return_url or "https://t.me/"),
        )
    )

    yk_payment, yk_meta = await client.get_payment(payment_id)

    status = yk_payment.get("status", "unknown")
    paid = bool(yk_payment.get("paid", False))
    pm = yk_payment.get("payment_method") or {}
    cd = yk_payment.get("cancellation_details") or {}

    paid_at = None
    canceled_at = None
    if status == "succeeded" and paid:
        paid_at = datetime.utcnow()
    if status == "canceled":
        canceled_at = datetime.utcnow()

    raw_to_store = {"payment": yk_payment, "_meta": yk_meta}

    await repo.yk_update_payment(
        external_payment_id=payment_id,
        status=status,
        raw=raw_to_store,
        paid_at=paid_at,
        canceled_at=canceled_at,
    )

    debug_text = (
        f"status={status}\n"
        f"paid={paid}\n"
        f"pm.type={pm.get('type')}\n"
        f"pm.status={pm.get('status')}\n"
        f"cancel.reason={cd.get('reason')}\n"
        f"cancel.party={cd.get('party')}\n"
        f"idempotence? (смотри в БД)\n"
        f"request_id(from headers)={yk_meta.get('request_id')}\n"
    )


    if status == "succeeded" and paid:
        # сумма из ответа YooKassa
        amount_obj = yk_payment.get("amount") or {}
        amount_value = amount_obj.get("value")
        amount_currency = amount_obj.get("currency", "RUB")

        # активируем подписку
        u = await repo.activate_paid_30d(call.message.chat.id)

        # удаляем сообщение со ссылкой (если хочешь)
        try:
            await call.message.delete()
        except (TelegramBadRequest, TelegramForbiddenError):
            pass

        # отправляем красивое подтверждение
        lines = [
            "✅ Оплата прошла",
            f"Подписка активна до {u.end_payment_date}",
        ]
        if amount_value:
            lines.append(f"Сумма: {amount_value} {amount_currency}")

        await call.message.answer("\n".join(lines))
        return
    
    # или убрать кнопки, или удалить сообщение со ссылкой
    # if status == "succeeded" and paid:
    #     await call.message.edit_reply_markup(reply_markup=None)  # убрать кнопки
    #     u = await repo.activate_paid_30d(call.message.chat.id)
    #     await call.message.answer(f"✅ Оплата прошла! Подписка активна до {u.end_payment_date}.")
    #     return


    if status == "canceled":
        await call.message.answer(f"❌ Платеж отменен.\n\n{debug_text}")
        return

    await call.message.answer(
        "⏳ Платеж еще не завершен (pending). Если он долго остается pending — это повод написать в поддержку YooKassa.\n\n"
        # f"{debug_text}"
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

        answer = await asyncio.to_thread(llm.generate, user_text)

    except Exception as e:
        await message.answer(f"⚠️ Ошибка при обработке: {e}")
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