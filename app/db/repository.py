# логика записи/чтения (атомарное действие)

from __future__ import annotations
from datetime import timedelta
from typing import Optional, Tuple

from app.db.models import RequestLog, UserSubscription
from app.utils.time import today_msk, now_msk
from app.services.limits import is_paid_active, is_banned

class Repository:
    def __init__(self, db, tz: str, free_limit: int, daily_hard_limit: int):
        self.db = db
        self.tz = tz
        self.free_limit = free_limit
        self.daily_hard_limit = daily_hard_limit

    def _ensure_user(self, chat_id: int) -> UserSubscription:
        today = today_msk(self.tz)
        u = self.db.user_subscriptions.get(chat_id)
        if u is None:
            u = UserSubscription(
                date=today,
                chat_id=chat_id,
                num_request=self.free_limit,
                subscribe=0,
                total_requests=0,
            )
            self.db.user_subscriptions[chat_id] = u
            return u

        # Сброс суточных счетчиков при смене дня
        # (в Postgres это обычно отдельный джоб/cron, но тут удобно)
        if u.date != today:
            u.date = today
            u.total_requests = 0
            u.ban_until = None

            if u.subscribe == 0:
                u.num_request = self.free_limit
            else:
                u.num_request = None

        # Если подписка кончилась — возвращаем в free
        if u.subscribe == 1 and u.end_payment_date is not None and today > u.end_payment_date:
            u.subscribe = 0
            u.payment_date = None
            u.end_payment_date = None
            u.num_request = self.free_limit

        return u

    def get_user(self, chat_id: int) -> UserSubscription:
        return self._ensure_user(chat_id)

    def activate_paid_30d(self, chat_id: int) -> UserSubscription:
        today = today_msk(self.tz)
        u = self._ensure_user(chat_id)
        u.subscribe = 1
        u.payment_date = today
        u.end_payment_date = today + timedelta(days=30)
        u.num_request = None  # анлим
        return u

    def can_make_request(self, chat_id: int) -> Tuple[bool, str]:
        today = today_msk(self.tz)
        u = self._ensure_user(chat_id)

        if is_banned(u, today):
            return False, "⛔️ Вы временно забанены на сутки за превышение лимита. Напишите в поддержку: test@gmail.com"

        paid = is_paid_active(u, today)

        # hard-limit (антиабуз)
        if u.total_requests >= self.daily_hard_limit:
            u.ban_until = today
            return False, "⛔️ Слишком много запросов за сутки. Напишите в поддержку: test@gmail.com"


        if paid:
            return True, ""

        # free-limit
        if (u.num_request is not None) and (u.num_request <= 0):
            return False, "🚫 ЛИМИТ ИСЧЕРПАН. Хотите продолжить — оформите подписку."

        return True, ""

    def record_interaction_atomic(
        self,
        chat_id: int,
        user_input: str,
        model_output: str,
    ) -> RequestLog:
        """
        "Одно действие":
        - ensure user row (table #2)
        - update counters (table #2)
        - insert request log (table #1)

        В реальном Postgres это делается транзакцией.
        """
        today = today_msk(self.tz)
        u = self._ensure_user(chat_id)

        # обновляем счётчики
        u.total_requests += 1

        paid = is_paid_active(u, today)
        if not paid and u.num_request is not None:
            u.num_request -= 1

        # пишем лог запроса
        req_id = self.db.next_request_id()
        row = RequestLog(
            id=req_id,
            date=now_msk(self.tz),
            chat_id=chat_id,
            input=user_input,
            output=model_output,
            summary=None,
        )
        self.db.requests_log.append(row)
        return row

    def get_day_dialog_text(self, chat_id: int) -> str:
        """
        Для daily summary: собираем все input/output за сегодня.
        """
        today = today_msk(self.tz)
        parts = []
        for r in self.db.requests_log:
            if r.chat_id == chat_id and r.date.date() == today:
                parts.append(f"USER: {r.input}\nBOT: {r.output}")
        return "\n\n".join(parts)

    def save_daily_summary(self, chat_id: int, summary_text: str) -> None:
        """
        Кладём summary в последний запрос дня (можно и иначе, но так проще с твоей таблицей).
        """
        today = today_msk(self.tz)
        last = None
        for r in self.db.requests_log:
            if r.chat_id == chat_id and r.date.date() == today:
                last = r
        if last:
            last.summary = summary_text
    
    # --- admin ---

    def list_users(self):
        return list(self.db.user_subscriptions.values())

    def admin_extend_paid_30d(self, chat_id: int):
        # бесплатное продление/выдача платной на 30 дней
        u = self._ensure_user(chat_id)
        today = today_msk(self.tz)
        u.subscribe = 1
        u.payment_date = today
        u.end_payment_date = today + timedelta(days=30)
        u.num_request = None
        return u

    def admin_reset_subscription(self, chat_id: int):
        # сброс к free
        u = self._ensure_user(chat_id)
        u.subscribe = 0
        u.payment_date = None
        u.end_payment_date = None
        u.num_request = self.free_limit
        return u

    def admin_delete_user(self, chat_id: int):
        # удалить из "таблицы #2"
        self.db.user_subscriptions.pop(chat_id, None)
        # удалить логи из "таблицы #1"
        self.db.requests_log = [r for r in self.db.requests_log if r.chat_id != chat_id]
