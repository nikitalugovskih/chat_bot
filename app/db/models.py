# "структуры таблиц" (dataclass)

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

@dataclass
class RequestLog:
    id: int
    date: datetime          # дата старта запроса (timestamp)
    chat_id: int
    input: str
    output: str
    summary: Optional[str] = None  # раз в сутки в 00:00 МСК

@dataclass
class UserSubscription:
    # "date" — дата старта подписки (или первого взаимодействия)
    date: date
    chat_id: int

    # num_request — сколько запросов осталось (для paid: None = анлим)
    num_request: Optional[int]

    # subscribe — 0/1
    subscribe: int = 0

    # total_requests — сколько запросов за сутки (сбрасываем каждый день)
    total_requests: int = 0

    # payment_date / end_payment_date — если paid
    payment_date: Optional[date] = None
    end_payment_date: Optional[date] = None

    # служебное (не просили, но очень удобно): если забанен до даты
    ban_until: Optional[date] = None