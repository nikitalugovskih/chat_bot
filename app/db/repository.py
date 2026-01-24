from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Tuple, Any, List

from app.db.models import RequestLog, UserSubscription, UserProfile
from app.utils.time import today_msk, now_msk
from app.services.limits import is_paid_active, is_banned

import json


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

    async def get_recent_user_inputs(self, chat_id: int, limit: int = 5) -> List[str]:
        if limit <= 0:
            return []
        if self._is_fake():
            items = [r for r in self.db.requests_log if r.chat_id == chat_id and (r.input or "").strip()]
            items.sort(key=lambda r: r.date)
            recent = items[-limit:]
            return [r.input for r in recent]

        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT input
                FROM requests_log
                WHERE chat_id=$1 AND input IS NOT NULL AND input <> ''
                ORDER BY date DESC
                LIMIT $2
                """,
                chat_id,
                limit,
            )
            return [r["input"] for r in reversed(rows)]

    async def get_recent_dialog_pairs(self, chat_id: int, limit: int = 5) -> List[tuple[str, str]]:
        if limit <= 0:
            return []
        if self._is_fake():
            items = [
                r for r in self.db.requests_log
                if r.chat_id == chat_id and (r.input or "").strip() and (r.output or "").strip()
            ]
            items.sort(key=lambda r: r.date)
            recent = items[-limit:]
            return [(r.input, r.output) for r in recent]

        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT input, output
                FROM requests_log
                WHERE chat_id=$1
                  AND input IS NOT NULL AND input <> ''
                  AND output IS NOT NULL AND output <> ''
                ORDER BY date DESC
                LIMIT $2
                """,
                chat_id,
                limit,
            )
            return [(r["input"], r["output"]) for r in reversed(rows)]

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

    async def get_user_profile(self, chat_id: int) -> UserProfile | None:
        if self._is_fake():
            return self.db.users.get(chat_id)

        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT chat_id, started_at, name, gender, age, consented, memory, end_dialog
                FROM users
                WHERE chat_id=$1
                """,
                chat_id,
            )
            if not row:
                return None
            return UserProfile(
                chat_id=row["chat_id"],
                started_at=row["started_at"],
                name=row["name"],
                gender=row["gender"],
                age=row["age"],
                consented=row["consented"] or 0,
                memory=row["memory"],
                end_dialog=row["end_dialog"] or 0,
            )
        
    async def log_payment_stars(self, chat_id: int, sp: Any) -> None:
        """
        Пишем платеж в таблицу payments. Защита от повторов по telegram_charge_id.
        sp = message.successful_payment (aiogram SuccessfulPayment)
        """
        if self._is_fake():
            return  # в fake режиме можно не логировать

        # aiogram SuccessfulPayment (pydantic) -> dict
        try:
            raw = sp.model_dump()
        except Exception:
            raw = dict(sp)

        provider = "telegram_stars"
        currency = getattr(sp, "currency", None) or raw.get("currency")
        amount = getattr(sp, "total_amount", None) or raw.get("total_amount")
        payload = getattr(sp, "invoice_payload", None) or raw.get("invoice_payload")
        telegram_charge_id = getattr(sp, "telegram_payment_charge_id", None) or raw.get("telegram_payment_charge_id")
        provider_charge_id = getattr(sp, "provider_payment_charge_id", None) or raw.get("provider_payment_charge_id")

        async with self.db.acquire() as conn:
            # ON CONFLICT — чтобы один и тот же платеж не записался дважды
            await conn.execute(
                """
                INSERT INTO payments
                    (chat_id, provider, currency, amount, payload,
                     telegram_charge_id, provider_charge_id, raw)
                VALUES
                    ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                ON CONFLICT (provider, telegram_charge_id) DO NOTHING
                """,
                chat_id,
                provider,
                currency,
                int(amount) if amount is not None else 0,
                payload or "",
                telegram_charge_id,
                provider_charge_id,
                json.dumps(raw, ensure_ascii=False),
            )

    async def stars_total(self) -> int:
        """Сумма Stars по нашей БД (payments)."""
        if self._is_fake():
            return 0

        async with self.db.acquire() as conn:
            val = await conn.fetchval(
                """
                SELECT COALESCE(SUM(amount), 0)
                FROM payments
                WHERE provider='telegram_stars' AND currency='XTR'
                """
            )
            return int(val or 0)

    async def stars_top_donors(self, limit: int = 20):
        """Топ доноров по сумме Stars."""
        if self._is_fake():
            return []

        async with self.db.acquire() as conn:
            return await conn.fetch(
                """
                SELECT
                    p.chat_id,
                    COALESCE(us.username, '') AS username,
                    COALESCE(us.full_name, '') AS full_name,
                    COALESCE(SUM(p.amount), 0) AS stars
                FROM payments p
                LEFT JOIN user_subscriptions us ON us.chat_id = p.chat_id
                WHERE p.provider='telegram_stars' AND p.currency='XTR'
                GROUP BY p.chat_id, us.username, us.full_name
                ORDER BY stars DESC
                LIMIT $1
                """,
                limit,
            )

    async def stars_last_payments(self, limit: int = 20):
        """Последние оплаты Stars."""
        if self._is_fake():
            return []

        async with self.db.acquire() as conn:
            return await conn.fetch(
                """
                SELECT
                    p.created_at,
                    p.chat_id,
                    COALESCE(us.username, '') AS username,
                    COALESCE(us.full_name, '') AS full_name,
                    p.amount
                FROM payments p
                LEFT JOIN user_subscriptions us ON us.chat_id = p.chat_id
                WHERE p.provider='telegram_stars' AND p.currency='XTR'
                ORDER BY p.created_at DESC
                LIMIT $1
                """,
                limit,
            )

    async def yk_insert_payment(
        self,
        *,
        chat_id: int,
        amount: int,
        payload: str,
        status: str,
        external_payment_id: str,
        idempotence_key: str,
        confirmation_url: str,
        raw: dict,
    ) -> None:
        """
        payments columns (как у тебя):
        created_at, chat_id, provider, currency, amount, payload, raw, status,
        external_payment_id, idempotence_key, confirmation_url, updated_at, paid_at, canceled_at ...
        """
        if self._is_fake():
            return

        async with self.db.acquire() as conn:
            # чтобы не плодить дубликаты, если юзер тыкнул дважды
            exists = await conn.fetchval(
                "SELECT 1 FROM payments WHERE provider='yookassa' AND external_payment_id=$1",
                external_payment_id,
            )
            if exists:
                return

            await conn.execute(
                """
                INSERT INTO payments
                (created_at, chat_id, provider, currency, amount, payload,
                 telegram_charge_id, provider_charge_id, raw,
                 status, external_payment_id, idempotence_key, confirmation_url,
                 paid_at, canceled_at, updated_at)
                VALUES
                (NOW(), $1, 'yookassa', 'RUB', $2, $3,
                 NULL, NULL, $4::jsonb,
                 $5, $6, $7, $8,
                 NULL, NULL, NOW())
                """,
                chat_id,
                int(amount),
                payload,
                json.dumps(raw, ensure_ascii=False),
                status,
                external_payment_id,
                idempotence_key,
                confirmation_url,
            )

    async def yk_update_payment(
        self,
        *,
        external_payment_id: str,
        status: str,
        raw: dict,
        paid_at: datetime | None,
        canceled_at: datetime | None,
    ) -> None:
        if self._is_fake():
            return

        async with self.db.acquire() as conn:
            await conn.execute(
                """
                UPDATE payments
                SET status=$2,
                    raw=$3::jsonb,
                    paid_at=$4,
                    canceled_at=$5,
                    updated_at=NOW()
                WHERE provider='yookassa' AND external_payment_id=$1
                """,
                external_payment_id,
                status,
                json.dumps(raw, ensure_ascii=False),
                paid_at,
                canceled_at,
            )

    async def yk_get_payment(self, external_payment_id: str) -> dict | None:
        if self._is_fake():
            return None

        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM payments
                WHERE provider='yookassa' AND external_payment_id=$1
                LIMIT 1
                """,
                external_payment_id,
            )
            return dict(row) if row else None
    
    async def yk_get_recent_pending(self, chat_id: int, ttl_minutes: int = 10) -> dict[str, Any] | None:
        """
        Возвращает последний платеж YooKassa со статусом pending,
        созданный за последние ttl_minutes минут.
        """
        if self._is_fake():
            return None

        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    external_payment_id,
                    confirmation_url,
                    created_at,
                    status
                FROM payments
                WHERE provider='yookassa'
                  AND chat_id=$1
                  AND status='pending'
                  AND created_at >= NOW() - ($2::int * INTERVAL '1 minute')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                chat_id,
                ttl_minutes,
            )
            return dict(row) if row else None

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

    async def upsert_user_profile(
        self,
        *,
        chat_id: int,
        name: str | None,
        gender: str | None,
        age: int | None,
        started_at: datetime,
        consented: int | None = None,
    ) -> None:
        if self._is_fake():
            existing = self.db.users.get(chat_id)
            started_at_final = existing.started_at if existing else started_at
            self.db.users[chat_id] = UserProfile(
                chat_id=chat_id,
                started_at=started_at_final,
                name=name,
                gender=gender,
                age=age,
                consented=consented if consented is not None else (existing.consented if existing else 0),
                memory=existing.memory if existing else None,
                end_dialog=existing.end_dialog if existing else 0,
            )
            return

        async with self.db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (chat_id, started_at, name, gender, age, consented)
                VALUES ($1, $2, $3, $4, $5, COALESCE($6, 0))
                ON CONFLICT (chat_id) DO UPDATE
                SET name=EXCLUDED.name,
                    gender=EXCLUDED.gender,
                    age=EXCLUDED.age,
                    consented=GREATEST(users.consented, EXCLUDED.consented),
                    started_at=users.started_at,
                    memory=users.memory,
                    end_dialog=users.end_dialog
                """,
                chat_id,
                started_at,
                name,
                gender,
                age,
                consented,
            )

    async def set_user_consented(self, chat_id: int, started_at: datetime) -> None:
        if self._is_fake():
            existing = self.db.users.get(chat_id)
            if existing:
                existing.consented = 1
            else:
                self.db.users[chat_id] = UserProfile(
                    chat_id=chat_id,
                    started_at=started_at,
                    consented=1,
                )
            return

        async with self.db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (chat_id, started_at, consented)
                VALUES ($1, $2, 1)
                ON CONFLICT (chat_id) DO UPDATE
                SET consented=1,
                    started_at=users.started_at
                """,
                chat_id,
                started_at,
            )

    async def set_user_memory(self, chat_id: int, memory: str) -> None:
        memory = (memory or "").strip()
        if self._is_fake():
            existing = self.db.users.get(chat_id)
            if existing:
                existing.memory = memory
            else:
                self.db.users[chat_id] = UserProfile(
                    chat_id=chat_id,
                    started_at=now_msk(self.tz),
                    consented=0,
                    memory=memory,
                )
            return

        async with self.db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (chat_id, started_at, memory)
                VALUES ($1, $2, $3)
                ON CONFLICT (chat_id) DO UPDATE
                SET memory=EXCLUDED.memory,
                    started_at=users.started_at
                """,
                chat_id,
                now_msk(self.tz),
                memory,
            )

    async def set_end_dialog(self, chat_id: int, value: int) -> None:
        val = 1 if value else 0
        if self._is_fake():
            existing = self.db.users.get(chat_id)
            if existing:
                existing.end_dialog = val
            else:
                self.db.users[chat_id] = UserProfile(
                    chat_id=chat_id,
                    started_at=now_msk(self.tz),
                    consented=0,
                    end_dialog=val,
                )
            return

        async with self.db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (chat_id, started_at, end_dialog)
                VALUES ($1, $2, $3)
                ON CONFLICT (chat_id) DO UPDATE
                SET end_dialog=EXCLUDED.end_dialog,
                    started_at=users.started_at
                """,
                chat_id,
                now_msk(self.tz),
                val,
            )

    async def clear_dialog_context(self, chat_id: int) -> None:
        if self._is_fake():
            self.db.requests_log = [r for r in self.db.requests_log if r.chat_id != chat_id]
            return

        async with self.db.acquire() as conn:
            await conn.execute("DELETE FROM requests_log WHERE chat_id=$1", chat_id)

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
