"""Minimal Telegram Bot API client (long polling + send).

Long polling keeps a TCP connection open while Telegram has nothing to send.
Idle connections are silently dropped by NAT gateways, and the client then
waits for the full read timeout while new messages queue up on Telegram's
side. Keep-alive probes surface a dead connection quickly, and a failed poll
retires the pooled socket so the next attempt dials a fresh one.
"""

from __future__ import annotations

import socket
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from .. import config

API_ROOT = "https://api.telegram.org"

# Probe an idle connection well before a typical NAT idle timeout so a dropped
# long poll fails fast instead of hanging until the read timeout expires.
KEEPALIVE_IDLE_SECONDS = 15
KEEPALIVE_INTERVAL_SECONDS = 5
KEEPALIVE_FAILED_PROBES = 3

# Headroom between Telegram's long-poll deadline and our socket read timeout.
POLL_READ_MARGIN_SECONDS = 10


def _keepalive_socket_options() -> list[tuple[int, int, int]]:
    options = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    for name, value in (
        ("TCP_KEEPIDLE", KEEPALIVE_IDLE_SECONDS),
        ("TCP_KEEPINTVL", KEEPALIVE_INTERVAL_SECONDS),
        ("TCP_KEEPCNT", KEEPALIVE_FAILED_PROBES),
    ):
        option = getattr(socket, name, None)
        if option is not None:
            options.append((socket.IPPROTO_TCP, option, value))
    return options


class KeepAliveAdapter(HTTPAdapter):
    """An adapter whose connections send TCP keep-alive probes while idle."""

    def init_poolmanager(self, *args, **kwargs):
        kwargs.setdefault("socket_options", _keepalive_socket_options())
        super().init_poolmanager(*args, **kwargs)


def build_session() -> requests.Session:
    """A session whose sockets send keep-alive probes while idle."""
    session = requests.Session()
    adapter = KeepAliveAdapter()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class TelegramError(RuntimeError):
    pass


class TelegramPollError(TelegramError):
    """A getUpdates failure that leaves the polling offset untouched."""


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
        poll_timeout: int = config.TELEGRAM_POLL_TIMEOUT_SECONDS,
    ) -> None:
        self.token = token or config.telegram_bot_token()
        if not self.token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is not set")
        self.timeout = timeout
        self.poll_timeout = poll_timeout
        # Injected sessions belong to the caller and are never rebuilt.
        self._owns_session = session is None
        self.session = session or build_session()

    def reset_connections(self) -> None:
        """Discard pooled sockets so the next call dials a fresh connection."""
        if not self._owns_session:
            return
        try:
            self.session.close()
        except Exception:  # pragma: no cover - closing must never mask errors
            pass
        self.session = build_session()

    def _call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{API_ROOT}/bot{self.token}/{method}"
        resp = self.session.get(url, params=params, timeout=self.timeout)
        payload = resp.json()
        if not payload.get("ok"):
            raise TelegramError(f"{method} failed: {payload.get('description')!r}")
        return payload.get("result")

    def get_me(self) -> dict:
        return self._call("getMe")

    def get_updates(
        self, offset: int | None = None, timeout: int | None = None
    ) -> list[dict]:
        poll_timeout = self.poll_timeout if timeout is None else timeout
        params: dict[str, Any] = {"timeout": poll_timeout}
        if offset is not None:
            params["offset"] = offset
        url = f"{API_ROOT}/bot{self.token}/getUpdates"
        read_timeout = poll_timeout + POLL_READ_MARGIN_SECONDS
        try:
            response = self.session.get(url, params=params, timeout=read_timeout)
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            # The socket is unusable or suspect; a fresh one avoids stalling
            # again on the same dead connection while messages queue up.
            self.reset_connections()
            raise TelegramPollError(f"getUpdates failed: {exc}") from exc
        if not payload.get("ok"):
            raise TelegramPollError(
                f"getUpdates rejected: {payload.get('description')!r}"
            )
        return payload.get("result")

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
