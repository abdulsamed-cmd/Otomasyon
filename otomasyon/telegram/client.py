"""Minimal Telegram Bot API client (long polling + send)."""

from __future__ import annotations

from typing import Any

import requests

from .. import config

API_ROOT = "https://api.telegram.org"


class TelegramError(RuntimeError):
    pass


class TelegramSendError(TelegramError):
    """A send failure classified at the Telegram uncertainty boundary."""

    def __init__(
        self,
        message: str,
        *,
        ambiguous: bool,
        retryable: bool,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.ambiguous = ambiguous
        self.retryable = retryable
        self.retry_after = retry_after


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
        params = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        url = f"{API_ROOT}/bot{self.token}/sendMessage"
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
        except requests.Timeout as exc:
            raise TelegramSendError(
                "sendMessage timed out after transmission may have begun",
                ambiguous=True,
                retryable=False,
            ) from exc
        except requests.ConnectionError as exc:
            raise TelegramSendError(
                "sendMessage connection failed with unknown delivery outcome",
                ambiguous=True,
                retryable=False,
            ) from exc

        try:
            payload = response.json()
        except (ValueError, requests.JSONDecodeError) as exc:
            raise TelegramSendError(
                "sendMessage returned an unreadable acknowledgement",
                ambiguous=True,
                retryable=False,
            ) from exc
        if payload.get("ok"):
            return payload.get("result")

        retry_after = (payload.get("parameters") or {}).get("retry_after")
        status = getattr(response, "status_code", 0)
        if status == 429 or retry_after is not None:
            raise TelegramSendError(
                f"sendMessage rate limited: {payload.get('description')!r}",
                ambiguous=False,
                retryable=True,
                retry_after=int(retry_after) if retry_after is not None else None,
            )
        if status >= 500:
            raise TelegramSendError(
                f"sendMessage server failure: {payload.get('description')!r}",
                ambiguous=True,
                retryable=False,
            )
        raise TelegramSendError(
            f"sendMessage rejected: {payload.get('description')!r}",
            ambiguous=False,
            retryable=False,
        )
