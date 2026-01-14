from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Union, Any

from app.db.models import RequestLog, UserSubscription

@dataclass
class FakeDatabase:
    requests_log: List[RequestLog]
    user_subscriptions: Dict[int, UserSubscription]  # key = chat_id
    _request_id_seq: int = 0

    def next_request_id(self) -> int:
        self._request_id_seq += 1
        return self._request_id_seq

async def get_db(use_fake: bool, dsn: str):
    """
    Если use_fake=True -> FakeDatabase.
    Иначе -> asyncpg pool.
    """
    if use_fake:
        return FakeDatabase(requests_log=[], user_subscriptions={}, _request_id_seq=0)

    import asyncpg  # чтобы проект запускался без asyncpg, если FakeDB
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=10)
    return pool