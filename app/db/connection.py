# подключение + интерфейс

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List
from app.db.models import RequestLog, UserSubscription

@dataclass
class FakeDatabase:
    """
    In-memory "DB". Позже заменишь на Postgres pool.
    """
    requests_log: List[RequestLog]
    user_subscriptions: Dict[int, UserSubscription]  # key = chat_id
    _request_id_seq: int = 0

    def next_request_id(self) -> int:
        self._request_id_seq += 1
        return self._request_id_seq

def get_db(use_fake: bool = True):
    # Здесь будет реальный коннект к Postgres.
    # Пока: возвращаем FakeDatabase.
    if use_fake:
        return FakeDatabase(requests_log=[], user_subscriptions={}, _request_id_seq=0)

    raise NotImplementedError("Postgres connection is not implemented yet.")