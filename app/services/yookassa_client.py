# app/services/yookassa_client.py

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Tuple

import aiohttp


@dataclass(frozen=True)
class YooKassaConfig:
    shop_id: str
    secret_key: str
    return_url: str


class YooKassaClient:
    BASE_URL = "https://api.yookassa.ru"

    def __init__(self, cfg: YooKassaConfig):
        self.cfg = cfg
        token = f"{cfg.shop_id}:{cfg.secret_key}".encode("utf-8")
        self._auth_header = "Basic " + base64.b64encode(token).decode("ascii")

    async def create_payment(
        self,
        *,
        amount_value: str,   # "199.00"
        currency: str,       # "RUB"
        description: str,
        idempotence_key: str,
        metadata: dict[str, Any],
        force_bank_card: bool = True,
    ) -> Tuple[dict[str, Any], dict[str, Any]]:
        """
        Returns: (payment_json, debug_meta)
        debug_meta contains: http_status, request_id, idempotence_key
        """
        url = f"{self.BASE_URL}/v3/payments"
        headers = {
            "Authorization": self._auth_header,
            "Content-Type": "application/json",
            "Idempotence-Key": idempotence_key,
        }

        payload: dict[str, Any] = {
            "amount": {"value": amount_value, "currency": currency},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": self.cfg.return_url},
            "description": description,
            "metadata": metadata,
        }

        # ВАЖНО: чтобы "Карта" реально была картой, а не сбер/др. методы
        if force_bank_card:
            payload["payment_method_data"] = {"type": "bank_card"}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=json.dumps(payload)) as r:
                request_id = r.headers.get("Request-Id") or r.headers.get("X-Request-Id")
                text = await r.text()
                try:
                    data = json.loads(text) if text else {}
                except Exception:
                    data = {"_non_json_body": text}

                meta = {
                    "http_status": r.status,
                    "request_id": request_id,
                    "idempotence_key": idempotence_key,
                }

                if r.status >= 400:
                    raise RuntimeError(f"YooKassa create_payment failed: {meta} body={data}")

                return data, meta

    async def get_payment(self, payment_id: str) -> Tuple[dict[str, Any], dict[str, Any]]:
        """
        Returns: (payment_json, debug_meta)
        """
        url = f"{self.BASE_URL}/v3/payments/{payment_id}"
        headers = {"Authorization": self._auth_header}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as r:
                request_id = r.headers.get("Request-Id") or r.headers.get("X-Request-Id")
                text = await r.text()
                try:
                    data = json.loads(text) if text else {}
                except Exception:
                    data = {"_non_json_body": text}

                meta = {
                    "http_status": r.status,
                    "request_id": request_id,
                }

                if r.status >= 400:
                    raise RuntimeError(f"YooKassa get_payment failed: {meta} body={data}")

                return data, meta
