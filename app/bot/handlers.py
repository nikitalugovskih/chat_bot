# /start, кнопки, сообщения

from aiogram import Router, F
from aiogram.enums import ChatAction
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.types import FSInputFile
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.types import LabeledPrice, PreCheckoutQuery
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from decimal import Decimal
import uuid
from app.services.yookassa_client import YooKassaClient, YooKassaConfig

from app.bot.keyboards import (
    start_keyboard,
    chat_keyboard,
    subscription_keyboard,
    pay_methods_keyboard,
    yookassa_pay_keyboard,
    consent_keyboard,
    gender_keyboard,
    premium_keyboard,
    admin_panel_keyboard,
)
from app.bot.admin_handlers import is_admin
from app.bot.states import ChatFlow

from datetime import datetime, date

from app.utils.time import today_msk, now_msk

import logging
import hashlib
import contextlib
import asyncio
import re

from app.services.summary import build_memory

logger = logging.getLogger("bot")

router = Router()
LAST_STARS_INVOICE: dict[int, int] = {}

async def _typing_loop(bot, chat_id: int, interval: float = 3.5):
    try:
        while True:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        return

async def _update_memory_bg(repo, memory_llm, chat_id: int, user_text: str, answer: str, user_memory: str | None):
    try:
        turn_text = f"USER: {user_text}\nBOT: {answer}"
        updated_memory = await asyncio.to_thread(
            build_memory,
            memory_llm,
            turn_text,
            existing_memory=user_memory,
        )
        if updated_memory and updated_memory != (user_memory or "").strip():
            await repo.set_user_memory(chat_id, updated_memory)
    except Exception:
        logger.exception("Failed to update user memory", extra={"chat_id": chat_id})

def _split_response(text: str, max_len: int = 300) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    # split by paragraphs; keep bullets grouped if possible
    paragraphs = [p.strip() for p in t.split("\n\n") if p.strip()]
    parts: list[str] = []
    buf = ""
    for p in paragraphs:
        candidate = (buf + "\n\n" + p).strip() if buf else p
        if len(candidate) <= max_len:
            buf = candidate
        else:
            if buf:
                parts.append(buf)
            buf = p
    if buf:
        parts.append(buf)
    return parts

_ACK_WORDS = {
    "ок", "окей", "ok", "okay", "ага", "угу", "да", "нет", "понял", "поняла",
    "ясно", "спасибо", "спс", "мерси", "сенкс", "окейно", "ладно", "хорошо",
    "привет", "здарова", "пока", "бай",
}

def _should_update_memory(user_text: str) -> bool:
    t = (user_text or "").strip()
    if not t:
        return False
    if t.startswith("/"):
        return False
    words = re.findall(r"[a-zа-я0-9]+", t.lower())
    if not words:
        return False
    if len(words) <= 2 and all(w in _ACK_WORDS for w in words):
        return False
    if len(words) <= 2 and len(t) < 10:
        return False
    return True

def _get_start_payload(message: Message) -> str | None:
    # Prefer framework helper if available; fall back to parsing text.
    payload = ""
    get_args = getattr(message, "get_args", None)
    if callable(get_args):
        try:
            payload = (get_args() or "").strip()
        except Exception:
            payload = ""
    if not payload:
        text = message.text or ""
        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            if len(parts) > 1:
                payload = parts[1].strip()
    return payload or None

from aiogram import F
from aiogram.types import Message

FAQ_TEXT = (
    "❇️ Как я вообще работаю?\n"
    "Привет 🙂\n"
    "Я — дружелюбный чат-бот и собеседник, который всегда рядом, когда хочется выговориться, "
    "разложить мысли по полочкам или просто поговорить. Я умею поддерживать диалог, задавать "
    "уточняющие вопросы и помогать смотреть на ситуацию под разными углами.\n"
    "Во время общения я стараюсь вникнуть в то, что с тобой происходит: уточняю детали, "
    "размышляю вместе с тобой, предлагаю идеи и варианты, которые могут помочь именно в твоей ситуации. "
    "Мои ответы не заготовлены заранее — каждый раз они формируются под твой запрос и твои слова.\n"
    "Я могу помочь немного выдохнуть, успокоиться, навести порядок в голове и найти более спокойное состояние 😌\n"
    "Можешь писать о чём угодно: о переживаниях, сомнениях, стрессе, сложных решениях, тревоге, "
    "упадке настроения или просто когда хочется, чтобы тебя кто-то выслушал.\n"
    "Моя задача — быть рядом, поддержать разговор и помочь тебе лучше понять себя и то, что происходит.\n"
    "\n"
    "❇️ Мои сообщения — это конфиденциально?\n"
    "Да. Конфиденциальность — базовое правило.\n"
    "Всё, что ты мне пишешь, остаётся только в рамках твоего Telegram-аккаунта. Никто посторонний не имеет доступа к переписке.\n"
    "\n"
    "❇️ Могу ли я заменить психолога?\n"
    "Я — не психолог и не врач.\n"
    "Я подойду как поддержка и «первая точка опоры»: когда нужно поговорить, выговориться, "
    "получить внимание и идеи для размышлений.\n"
    "Если ты сталкиваешься с действительно тяжёлыми состояниями или серьёзными проблемами, лучше обратиться к живому специалисту — "
    "психологу или психотерапевту.\n"
    "\n"
    "❇️ Что даёт Premium-подписка?\n"
    "Premium открывает общение без ограничений по количеству сообщений и возможность отправлять голосовые сообщения.\n"
    "С подпиской ты можешь писать мне в любое время — днём, ночью, когда удобно. А ещё так ты поддерживаешь развитие бота, "
    "помогая ему становиться полезнее и лучше 💛\n"
    "\n"
    "❇️ Куда писать, если есть вопросы по работе бота?\n"
    "Если заметил ошибку, есть идеи, пожелания или вопросы — всегда можно написать в поддержку:\n"
    "👉 @Psy_pocket_support\n"
    "\n"
    "❇️ Подписка продлевается автоматически?\n"
    "Да, подписка продлевается автоматически. Оплата списывается за день до окончания текущего периода.\n"
    "Автопродление можно отключить в любой момент:\n"
    "Личный кабинет → Отменить подписку\n"
    "\n"
    "❇️ Как отменить подписку?\n"
    "Зайди в личный кабинет и нажми кнопку «Отменить подписку»."
)

CONSENT_TEXT = (
    "Хочу тебя заранее предупредить: я буду полезна тебе, если ты ищешь общения, "
    "возможность выразить свои мысли и чувства, получить поддержку и внимание, "
    "пути для решения собственных проблем 💡.\n"
    "\n"
    "Однако, если у тебя серьезные проблемы, то лучше обратиться к настоящему специалисту 👩‍⚕️.\n"
    "\n"
    "Ответь \"Да\"✅, если принимаешь условия."
)

PERSONALIZATION_TEXT = (
    "Когда ты делишься своим именем, возрастом и полом, мне легче подстроиться под тебя 👧.\n"
    "\n"
    "Имя помогает общаться более лично и дружелюбно.\n"
    "Возраст и пол дают возможность выбирать уместный тон и формы речи.\n"
    "Так наше общение становится комфортнее и естественнее."
)

# --- КНОПКИ ГЛАВНОГО МЕНЮ (reply keyboard) ---

@router.message(F.text == "💬 Начать")
async def btn_start_chat(message: Message, state: FSMContext, repo):
    await state.clear()
    chat_id = message.chat.id
    profile = await repo.get_user_profile(chat_id)
    if profile and profile.end_dialog == 1:
        await repo.clear_dialog_context(chat_id)
        await repo.set_end_dialog(chat_id, 0)
    if profile and profile.consented == 1:
        if profile.name and profile.gender and profile.age:
            await state.set_state(ChatFlow.chatting)
            await message.answer("Ок, пишите сообщение — я отвечу 🙂", reply_markup=chat_keyboard())
            return
        started_at = profile.started_at
        await state.set_state(ChatFlow.waiting_name)
        await state.update_data(started_at=started_at)
        await message.answer(
            PERSONALIZATION_TEXT + "\n\nКак тебя зовут?",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if not profile:
        await repo.upsert_user_profile(
            chat_id=chat_id,
            name=None,
            gender=None,
            age=None,
            started_at=now_msk(repo.tz),
            consented=0,
        )
    await message.answer(CONSENT_TEXT, reply_markup=consent_keyboard())


def _format_ru_date(d: date) -> str:
    months = [
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    ]
    return f"{d.day} {months[d.month - 1]} {d.year} г."

@router.message((F.text == "Личный Кабинет") | (F.text == "ℹ️ Подписка"))
async def btn_subscription(message: Message, repo):
    chat_id = message.chat.id
    u = await repo.get_user(chat_id)
    profile = await repo.get_user_profile(chat_id)

    paid_text = "да ✅" if u.subscribe == 1 else "нет ❌"
    left = "анлим" if u.num_request is None else str(u.num_request)
    reg_date = _format_ru_date(profile.started_at.date()) if profile else "—"

    text = (
        f"📌 Статус подписки: {paid_text}\n"
        f"📆 Дата (счётчики на день): {u.date}\n"
        f"🔢 Запросов сегодня: {u.total_requests}\n"
        f"🧾 Осталось запросов: {left}\n"
        f"🆔 Твой ID: {chat_id}\n"
        f"🗓️ Регистрация: {reg_date}\n"
    )
    await message.answer(text, reply_markup=subscription_keyboard())

@router.message(F.text == "Премиум подписка")
async def btn_premium(message: Message, repo):
    chat_id = message.chat.id
    u = await repo.get_user(chat_id)

    paid_text = "да ✅" if u.subscribe == 1 else "нет ❌"
    left = "анлим" if u.num_request is None else str(u.num_request)

    text = (
        "💎 Premium подписка дает тебе:\n\n"
        "✨ Безлимитные сообщения\n"
        "🤖 Улучшенная модель\n"
        "🖼 Понимание фото\n"
        "💡 Глубокий анализ проблемы\n"
        "🔒 Повышенная анонимность\n"
        "🚀 Высокая скорость работы\n"
        "\n"
        "Выбери способ оплаты:"
    )
    await message.answer(text, reply_markup=premium_keyboard())



@router.message((F.text == "Вопрос-Ответ") | (F.text == "❓ Вопрос-Ответ"))
async def btn_faq(message: Message):
    await message.answer(FAQ_TEXT)

@router.message(F.text == "📄 Условия")
async def btn_terms(message: Message):
    text = (
        "Мои алгоритмы запрещают разговоры на определенные темы. В частности, я не могу обсуждать "
        "наркотики, оружие, призывы к любому насилию и селфхарму.\n"
        "\n"
        "Моя цель - свести к минимуму любые риски. Используя данный сервис, ты автоматически соглашаешься "
        "с условиями использования по ссылке https://vk.com/wall-235516249_1"
    )
    await message.answer(text, disable_web_page_preview=True)

@router.callback_query(F.data == "consent_yes")
async def cb_consent_yes(call: CallbackQuery, state: FSMContext, repo):
    chat_id = call.message.chat.id
    profile = await repo.get_user_profile(chat_id)
    started_at = profile.started_at if profile else now_msk(repo.tz)
    await repo.set_user_consented(chat_id, started_at)
    await repo.set_end_dialog(chat_id, 0)

    if profile and profile.name and profile.gender and profile.age:
        await state.set_state(ChatFlow.chatting)
        await call.message.edit_text("Ок, пишите сообщение — я отвечу 🙂")
        return

    await state.set_state(ChatFlow.waiting_name)
    await state.update_data(started_at=started_at)
    await call.message.edit_text(PERSONALIZATION_TEXT + "\n\nКак тебя зовут?")

@router.message(F.text == "Да ✅")
async def msg_consent_yes(message: Message, state: FSMContext, repo):
    chat_id = message.chat.id
    profile = await repo.get_user_profile(chat_id)
    started_at = profile.started_at if profile else now_msk(repo.tz)
    await repo.set_user_consented(chat_id, started_at)
    await repo.set_end_dialog(chat_id, 0)

    if profile and profile.name and profile.gender and profile.age:
        await state.set_state(ChatFlow.chatting)
        await message.answer("Ок, пишите сообщение — я отвечу 🙂", reply_markup=chat_keyboard())
        return

    await state.set_state(ChatFlow.waiting_name)
    await state.update_data(started_at=started_at)
    await message.answer(
        PERSONALIZATION_TEXT + "\n\nКак тебя зовут?",
        reply_markup=ReplyKeyboardRemove(),
    )

@router.message(ChatFlow.waiting_name)
async def onboarding_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("Напиши, пожалуйста, как тебя зовут.")
        return
    await state.update_data(name=name)
    await state.set_state(ChatFlow.waiting_gender)
    await message.answer("Твой пол?", reply_markup=gender_keyboard())

@router.message(ChatFlow.waiting_gender)
async def onboarding_gender(message: Message, state: FSMContext):
    raw = (message.text or "").strip().lower()
    if raw in {"м", "муж", "мужской"}:
        gender = "М"
    elif raw in {"ж", "жен", "женский"}:
        gender = "Ж"
    elif raw in {"другое", "иной", "другой"}:
        gender = "Другое"
    else:
        await message.answer("Выбери вариант из кнопок: М / Ж / Другое.", reply_markup=gender_keyboard())
        return

    await state.update_data(gender=gender)
    await state.set_state(ChatFlow.waiting_age)
    await message.answer("Сколько тебе лет? Введи число.", reply_markup=ReplyKeyboardRemove())

@router.message(ChatFlow.waiting_age)
async def onboarding_age(message: Message, state: FSMContext, repo):
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужна цифра. Например: 25.")
        return
    age = int(raw)
    if age < 1 or age > 120:
        await message.answer("Возраст должен быть от 1 до 120. Попробуй ещё раз.")
        return

    data = await state.get_data()
    name = data.get("name")
    gender = data.get("gender")
    started_at = data.get("started_at") or now_msk(repo.tz)

    await repo.upsert_user_profile(
        chat_id=message.chat.id,
        name=name,
        gender=gender,
        age=age,
        started_at=started_at,
        consented=1,
    )
    await repo.set_end_dialog(message.chat.id, 0)

    await state.set_state(ChatFlow.chatting)
    await message.answer("Ок, пишите сообщение — я отвечу 🙂", reply_markup=chat_keyboard())

@router.message((F.text == "👋 Завершить диалог") | (F.text == "Завершить диалог"))
async def btn_end_chat(message: Message, state: FSMContext, repo, settings):
    await state.clear()
    await message.answer(
        "Диалог завершен. Можешь начать новый в любое время.",
        reply_markup=start_keyboard(is_admin=is_admin(message.chat.id, settings)),
    )
    await repo.set_end_dialog(message.chat.id, 1)
    await repo.set_user_memory(message.chat.id, "")

@router.callback_query(F.data == "profile_edit")
async def cb_profile_edit(call: CallbackQuery, state: FSMContext, repo):
    await call.answer()
    profile = await repo.get_user_profile(call.message.chat.id)
    await state.set_state(ChatFlow.waiting_name)
    await state.update_data(started_at=profile.started_at if profile else now_msk(repo.tz))
    await call.message.answer(
        PERSONALIZATION_TEXT + "\n\nКак тебя зовут?",
        reply_markup=ReplyKeyboardRemove(),
    )

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
async def cmd_start(message: Message, repo, state: FSMContext, settings):
    chat_id = message.chat.id

    # ✅ создать/обновить пользователя + обновить ник/имя
    await repo.get_user(chat_id)
    await repo.touch_user_profile(
        chat_id=chat_id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )

    await state.clear()

    payload = _get_start_payload(message)
    if payload == "premium":
        await btn_premium(message, repo)
        return

    text = (
        "Привет! 👋\n"
        "Я чат-бот компаньон! Нажми «Начать», чтобы запустить чат со мной.\n"
        "Или «Личный кабинет», чтобы посмотреть лимиты/оплату.\n"
        "Если нужна поддержка, жми «Поддержка».\n"
    )
    await message.answer(text, reply_markup=start_keyboard(is_admin=is_admin(chat_id, settings)))

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

@router.message(F.text == "🛠 Админ-панель")
async def btn_admin_panel(message: Message, settings, state: FSMContext):
    if not is_admin(message.chat.id, settings):
        return
    await state.clear()
    await message.answer("🛠 Админ-панель:", reply_markup=admin_panel_keyboard())


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
    # возвращаемся к экрану "Премиум подписка" (текущий текст пересоздавать не будем — только клаву)
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=premium_keyboard())

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
async def cb_back(call: CallbackQuery, state: FSMContext, settings):
    await state.clear()
    await call.message.edit_text(
        "Главное меню:", reply_markup=start_keyboard(is_admin=is_admin(call.message.chat.id, settings))
    )

@router.message(ChatFlow.chatting)
async def on_chat_message(message: Message, repo, llm, memory_llm):
    chat_id = message.chat.id
    user_text = message.text or ""
    profile = await repo.get_user_profile(chat_id)
    user_name = profile.name if profile else None
    user_gender = profile.gender if profile else None
    user_age = profile.age if profile else None
    user_memory = profile.memory if profile else None

    await repo.touch_user_profile(
        chat_id=chat_id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )

    ok, reason = await repo.can_make_request(chat_id)
    if not ok:
        # если лимит — предлагаем оплату/подписку
        if "закончился лимит" in (reason or "").lower():
            await message.answer(reason, reply_markup=premium_keyboard())
        else:
            await message.answer(reason, reply_markup=subscription_keyboard())
        return

    # Router disabled: отвечаем на все запросы без отсева.

    # LLM
    loading_sticker = None
    loading_text = None

    typing_task = None
    try:
        # 1) показываем "печатает..." + текст
        typing_task = asyncio.create_task(_typing_loop(message.bot, chat_id))
        # loading_sticker = await message.answer_sticker(FSInputFile("app/assets/loader.tgs"))
        # typing indicator is enough; no extra loading message

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

        prompt_input = user_text

        logger.info(
            "chat_id=%s | memory_len=%s | context_len=%s | prompt_preview='%s'",
            chat_id,
            len(user_memory or ""),
            len(prompt_input),
            prompt_input[:500].replace("\n", " "),
        )

        answer = await asyncio.to_thread(
            llm.generate,
            prompt_input,
            user_name=user_name,
            user_gender=user_gender,
            user_age=user_age,
            user_memory=user_memory,
        )
        if not (answer or "").strip():
            # one retry with minimal context to avoid empty replies
            retry_input = f"Коротко и по делу ответь пользователю:\n{user_text}"
            answer = await asyncio.to_thread(
                llm.generate,
                retry_input,
                user_name=user_name,
                user_gender=user_gender,
                user_age=user_age,
                user_memory=None,
            )
        if not (answer or "").strip():
            answer = (
                "Понял. Давай коротко и по делу:\n"
                "- Что случилось?\n"
                "- Что ты хочешь получить от ответа прямо сейчас?\n"
                "Если сложно, напиши одной фразой — разберём вместе."
            )

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
    if _should_update_memory(user_text):
        try:
            asyncio.create_task(
                _update_memory_bg(repo, memory_llm, chat_id, user_text, answer, user_memory)
            )
        except Exception:
            logger.exception("Failed to schedule memory update", extra={"chat_id": chat_id})

    parts = _split_response(answer, max_len=800)
    if not parts:
        parts = ["Понял. Давай коротко и по делу: что случилось?"]
    for i, part in enumerate(parts):
        if typing_task and typing_task.done() is False:
            await message.bot.send_chat_action(chat_id, ChatAction.TYPING)
        await message.answer(part)
        if i < len(parts) - 1:
            await asyncio.sleep(4.0)
    if typing_task:
        typing_task.cancel()
        with contextlib.suppress(Exception):
            await typing_task
