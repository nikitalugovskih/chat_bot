from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Tuple, Any, List

from app.db.models import RequestLog, UserSubscription
from app.utils.time import today_msk, now_msk
from app.services.limits import is_paid_active, is_banned


def _day_bounds(tz_name: str, day: date) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return start, end


class Repository:
    def __init__(self, db, tz: str, free_limit: int, daily_hard_limit: int):
        self.db = db  # FakeDatabase или asyncpg.Pool
        self.tz = tz
        self.free_limit = free_limit
        self.daily_hard_limit = daily_hard_limit

    def _is_fake(self) -> bool:
        return hasattr(self.db, "user_subscriptions") and hasattr(self.db, "requests_log")

    # -------------------- FAKE (старый режим) --------------------

    async def _ensure_user_fake(self, chat_id: int) -> UserSubscription:
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

        if u.date != today:
            u.date = today
            u.total_requests = 0
            u.ban_until = None
            u.num_request = self.free_limit if u.subscribe == 0 else None

        if u.subscribe == 1 and u.end_payment_date is not None and today > u.end_payment_date:
            u.subscribe = 0
            u.payment_date = None
            u.end_payment_date = None
            u.num_request = self.free_limit

        return u

    # -------------------- POSTGRES --------------------

    def _row_to_user(self, row: Any) -> UserSubscription:
        return UserSubscription(
            date=row["date"],
            chat_id=row["chat_id"],
            num_request=row["num_request"],
            subscribe=row["subscribe"],
            total_requests=row["total_requests"],
            payment_date=row["payment_date"],
            end_payment_date=row["end_payment_date"],
            ban_until=row["ban_until"],
            username=row["username"],
            full_name=row["full_name"],
        )

    async def _ensure_user_pg(self, conn, chat_id: int) -> UserSubscription:
        today = today_msk(self.tz)

        row = await conn.fetchrow(
            """
            SELECT date, chat_id, num_request, subscribe, total_requests, payment_date, end_payment_date, ban_until, username, full_name
            FROM user_subscriptions
            WHERE chat_id=$1
            FOR UPDATE
            """,
            chat_id,
        )

        if row is None:
            row = await conn.fetchrow(
                """
                INSERT INTO user_subscriptions (chat_id, date, num_request, subscribe, total_requests)
                VALUES ($1, $2, $3, 0, 0)
                RETURNING date, chat_id, num_request, subscribe, total_requests, payment_date, end_payment_date, ban_until, username, full_name
                """,
                chat_id,
                today,
                self.free_limit,
            )
            return self._row_to_user(row)

        u = self._row_to_user(row)

        # смена дня — сброс суточных счетчиков
        if u.date != today:
            new_num = self.free_limit if u.subscribe == 0 else None
            row = await conn.fetchrow(
                """
                UPDATE user_subscriptions
                SET date=$2,
                    total_requests=0,
                    ban_until=NULL,
                    num_request=$3
                WHERE chat_id=$1
                RETURNING date, chat_id, num_request, subscribe, total_requests, payment_date, end_payment_date, ban_until, username, full_name
                """,
                chat_id,
                today,
                new_num,
            )
            u = self._row_to_user(row)

        # если paid кончился — в free
        if u.subscribe == 1 and u.end_payment_date is not None and today > u.end_payment_date:
            row = await conn.fetchrow(
                """
                UPDATE user_subscriptions
                SET subscribe=0,
                    payment_date=NULL,
                    end_payment_date=NULL,
                    num_request=$2
                WHERE chat_id=$1
                RETURNING date, chat_id, num_request, subscribe, total_requests, payment_date, end_payment_date, ban_until, username, full_name
                """,
                chat_id,
                self.free_limit,
            )
            u = self._row_to_user(row)

        return u

    # -------------------- PUBLIC API (то, что дергают хендлеры) --------------------

    async def get_user(self, chat_id: int) -> UserSubscription:
        if self._is_fake():
            return await self._ensure_user_fake(chat_id)

        async with self.db.acquire() as conn:
            async with conn.transaction():
                return await self._ensure_user_pg(conn, chat_id)

    async def activate_paid_30d(self, chat_id: int) -> UserSubscription:
        today = today_msk(self.tz)

        if self._is_fake():
            u = await self._ensure_user_fake(chat_id)
            u.subscribe = 1
            u.payment_date = today
            u.end_payment_date = today + timedelta(days=30)
            u.num_request = None
            return u

        async with self.db.acquire() as conn:
            async with conn.transaction():
                await self._ensure_user_pg(conn, chat_id)
                row = await conn.fetchrow(
                    """
                    UPDATE user_subscriptions
                    SET subscribe=1,
                        payment_date=$2,
                        end_payment_date=$3,
                        num_request=NULL
                    WHERE chat_id=$1
                    RETURNING date, chat_id, num_request, subscribe, total_requests, payment_date, end_payment_date, ban_until, username, full_name
                    """,
                    chat_id,
                    today,
                    today + timedelta(days=30),
                )
                return self._row_to_user(row)

    async def can_make_request(self, chat_id: int) -> Tuple[bool, str]:
        today = today_msk(self.tz)

        if self._is_fake():
            u = await self._ensure_user_fake(chat_id)
            if is_banned(u, today):
                return False, "⛔️ Вы временно забанены на сутки за превышение лимита. Напишите в поддержку: test@gmail.com"
            paid = is_paid_active(u, today)
            if u.total_requests >= self.daily_hard_limit:
                u.ban_until = today
                return False, "⛔️ Слишком много запросов за сутки. Напишите в поддержку: test@gmail.com"
            if paid:
                return True, ""
            if (u.num_request is not None) and (u.num_request <= 0):
                return False, "🚫 ЛИМИТ ИСЧЕРПАН. Хотите продолжить — оформите подписку."
            return True, ""

        async with self.db.acquire() as conn:
            async with conn.transaction():
                u = await self._ensure_user_pg(conn, chat_id)

                if is_banned(u, today):
                    return False, "⛔️ Вы временно забанены на сутки за превышение лимита. Напишите в поддержку: test@gmail.com"

                paid = is_paid_active(u, today)

                # hard-limit
                if u.total_requests >= self.daily_hard_limit:
                    await conn.execute(
                        "UPDATE user_subscriptions SET ban_until=$2 WHERE chat_id=$1",
                        chat_id,
                        today,
                    )
                    return False, "⛔️ Слишком много запросов за сутки. Напишите в поддержку: test@gmail.com"

                if paid:
                    return True, ""

                if (u.num_request is not None) and (u.num_request <= 0):
                    return False, "🚫 ЛИМИТ ИСЧЕРПАН. Хотите продолжить — оформите подписку."

                return True, ""

    async def record_interaction_atomic(self, chat_id: int, user_input: str, model_output: str) -> RequestLog:
        today = today_msk(self.tz)

        if self._is_fake():
            u = await self._ensure_user_fake(chat_id)
            u.total_requests += 1
            paid = is_paid_active(u, today)
            if not paid and u.num_request is not None:
                u.num_request -= 1

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

        async with self.db.acquire() as conn:
            async with conn.transaction():
                u = await self._ensure_user_pg(conn, chat_id)
                paid = is_paid_active(u, today)

                # обновляем счетчики
                if paid:
                    await conn.execute(
                        "UPDATE user_subscriptions SET total_requests = total_requests + 1 WHERE chat_id=$1",
                        chat_id,
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE user_subscriptions
                        SET total_requests = total_requests + 1,
                            num_request = CASE
                                WHEN num_request IS NULL THEN NULL
                                ELSE num_request - 1
                            END
                        WHERE chat_id=$1
                        """,
                        chat_id,
                    )

                # пишем лог
                row = await conn.fetchrow(
                    """
                    INSERT INTO requests_log (date, chat_id, input, output, summary)
                    VALUES ($1, $2, $3, $4, NULL)
                    RETURNING id, date, chat_id, input, output, summary
                    """,
                    now_msk(self.tz),
                    chat_id,
                    user_input,
                    model_output,
                )

                return RequestLog(
                    id=row["id"],
                    date=row["date"],
                    chat_id=row["chat_id"],
                    input=row["input"],
                    output=row["output"],
                    summary=row["summary"],
                )

    async def get_day_dialog_text(self, chat_id: int) -> str:
        day = today_msk(self.tz)
        start, end = _day_bounds(self.tz, day)

        if self._is_fake():
            parts = []
            for r in self.db.requests_log:
                if r.chat_id == chat_id and start <= r.date < end:
                    parts.append(f"USER: {r.input}\nBOT: {r.output}")
            return "\n\n".join(parts)

        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT input, output
                FROM requests_log
                WHERE chat_id=$1 AND date >= $2 AND date < $3
                ORDER BY date ASC
                """,
                chat_id,
                start,
                end,
            )
            return "\n\n".join([f"USER: {r['input']}\nBOT: {r['output']}" for r in rows])

    async def save_daily_summary(self, chat_id: int, summary_text: str) -> None:
        day = today_msk(self.tz)
        start, end = _day_bounds(self.tz, day)

        if self._is_fake():
            last = None
            for r in self.db.requests_log:
                if r.chat_id == chat_id and start <= r.date < end:
                    last = r
            if last:
                last.summary = summary_text
            return

        async with self.db.acquire() as conn:
            await conn.execute(
                """
                WITH last_req AS (
                    SELECT id
                    FROM requests_log
                    WHERE chat_id=$1 AND date >= $2 AND date < $3
                    ORDER BY date DESC
                    LIMIT 1
                )
                UPDATE requests_log
                SET summary=$4
                WHERE id IN (SELECT id FROM last_req)
                """,
                chat_id,
                start,
                end,
                summary_text,
            )

    async def list_users(self) -> List[UserSubscription]:
        if self._is_fake():
            return list(self.db.user_subscriptions.values())

        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT date, chat_id, num_request, subscribe, total_requests, payment_date, end_payment_date, ban_until, username, full_name
                FROM user_subscriptions
                ORDER BY chat_id
                """
            )
            return [self._row_to_user(r) for r in rows]

    async def list_chat_ids(self) -> List[int]:
        if self._is_fake():
            return list(self.db.user_subscriptions.keys())

        async with self.db.acquire() as conn:
            rows = await conn.fetch("SELECT chat_id FROM user_subscriptions")
            return [int(r["chat_id"]) for r in rows]

    # --- admin methods ---

    async def admin_extend_paid_30d(self, chat_id: int) -> UserSubscription:
        return await self.activate_paid_30d(chat_id)

    async def admin_reset_subscription(self, chat_id: int) -> UserSubscription:
        today = today_msk(self.tz)

        if self._is_fake():
            u = await self._ensure_user_fake(chat_id)
            u.subscribe = 0
            u.payment_date = None
            u.end_payment_date = None
            u.num_request = self.free_limit
            return u

        async with self.db.acquire() as conn:
            async with conn.transaction():
                await self._ensure_user_pg(conn, chat_id)
                row = await conn.fetchrow(
                    """
                    UPDATE user_subscriptions
                    SET subscribe=0,
                        payment_date=NULL,
                        end_payment_date=NULL,
                        num_request=$2
                    WHERE chat_id=$1
                    RETURNING date, chat_id, num_request, subscribe, total_requests, payment_date, end_payment_date, ban_until, username, full_name
                    """,
                    chat_id,
                    self.free_limit,
                )
                return self._row_to_user(row)
            
    async def touch_user_profile(self, chat_id: int, username: str | None, full_name: str | None) -> None:
        if self._is_fake():
            u = await self._ensure_user_fake(chat_id)
            u.username = username
            u.full_name = full_name
            return

        async with self.db.acquire() as conn:
            async with conn.transaction():
                await self._ensure_user_pg(conn, chat_id)
                await conn.execute(
                    """
                    UPDATE user_subscriptions
                    SET username=$2,
                        full_name=$3
                    WHERE chat_id=$1
                    """,
                    chat_id, username, full_name
                )

    async def admin_delete_user(self, chat_id: int) -> None:
        """
        Удаляет пользователя и его логи.
        """
        if self._is_fake():
            self.db.user_subscriptions.pop(chat_id, None)
            self.db.requests_log = [r for r in self.db.requests_log if r.chat_id != chat_id]
            return

        async with self.db.acquire() as conn:
            async with conn.transaction():
                # сначала удаляем логи (из-за FK), потом саму подписку
                await conn.execute("DELETE FROM requests_log WHERE chat_id=$1", chat_id)
                await conn.execute("DELETE FROM user_subscriptions WHERE chat_id=$1", chat_id)
