"""Minimal Telegram Bot API client (long polling + send)."""

from __future__ import annotations

from typing import Any

import requests

from .. import config

API_ROOT = "https://api.telegram.org"


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        session: requests.Session | None = None,
        timeout: int = 35,
    ) -> None:
        self.token = token or config.telegram_bot_token()
        if not self.token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is not set")
        self.timeout = timeout
        self.session = session or requests.Session()

    def _call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{API_ROOT}/bot{self.token}/{method}"
        resp = self.session.get(url, params=params, timeout=self.timeout)
        payload = resp.json()
        if not payload.get("ok"):
            raise TelegramError(f"{method} failed: {payload.get('description')!r}")
        return payload.get("result")

    def get_me(self) -> dict:
        return self._call("getMe")

    def get_updates(self, offset: int | None = None, timeout: int = 25) -> list[dict]:
        params = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", params)

    def send_message(self, chat_id: int | str, text: str) -> dict:
        return self._call(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        )
