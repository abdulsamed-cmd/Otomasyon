"""Single-user Telegram bot dispatch and long-polling loop.

Only the configured username may interact with the bot. Two commands are
supported:

- ``bugün``  -> today's low-risk main + alternative coupons
- ``sürpriz`` -> the surprise-lab report

Handlers are injected as callables so the bot can be unit-tested without any
network access.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

from .. import config
from ..storage import Database

HELP = (
    "Merhaba! Komutlar:\n"
    "• bugün — günün düşük riskli kuponları (ana + alternatif)\n"
    "• sürpriz — 6+ Gol laboratuvarı ve sistem senaryoları\n\n"
    "• kadro — doğrulanmış ilk 11 rotasyon riskleri\n\n"
    "• durum — ROI, CLV, xG gölge ve 6+ model sağlık raporu\n\n"
    "Not: Otomatik oynama yapılmaz; yalnızca bilgilendirme."
)

RENDER_FAILURE_REPLY = (
    "Komutunuz alındı, ancak yanıt şu anda hazırlanamadı "
    "(veri kaynağına ulaşılamıyor olabilir).\n"
    "Birkaç dakika sonra tekrar yazabilirsiniz; sorun sürerse otomatik uyarı "
    "gönderilecek."
)


def _normalize(text: str) -> str:
    return (text or "").strip().casefold()


def _chat_id_of(update: dict) -> int | None:
    msg = update.get("message") or update.get("edited_message") or {}
    return (msg.get("chat") or {}).get("id")


class BotLeaseError(RuntimeError):
    pass


class Bot:
    def __init__(
        self,
        client,
        allowed_username: str | None,
        *,
        on_daily: Callable[[], str],
        on_surprise: Callable[[], str],
        on_lineup: Callable[[], str] | None = None,
        on_status: Callable[[], str] | None = None,
        db=None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        owner_id: str | None = None,
        lease_seconds: int = config.TELEGRAM_BOT_LEASE_SECONDS,
        max_send_attempts: int = 4,
        retry_base_seconds: int = 2,
        retry_max_seconds: int = 30,
        poll_reset_after_failures: int = config.TELEGRAM_POLL_RESET_AFTER_FAILURES,
    ) -> None:
        self.client = client
        self.allowed = (allowed_username or "").lstrip("@")
        self.on_daily = on_daily
        self.on_surprise = on_surprise
        self.on_lineup = on_lineup
        self.on_status = on_status
        self.db = db or Database(":memory:")
        self.clock = clock
        self.sleep = sleep
        self.owner_id = owner_id or uuid.uuid4().hex
        self.lease_seconds = lease_seconds
        self.max_send_attempts = max_send_attempts
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.poll_reset_after_failures = poll_reset_after_failures
        self._recovered = False
        self._poll_failures = 0
        self._offset = self.db.telegram_poll_offset()

    def _is_allowed(self, username: str | None) -> bool:
        return bool(self.allowed) and bool(username) and (
            username.casefold() == self.allowed.casefold()
        )

    def handle_update(self, update: dict) -> str | None:
        """Return the reply text for an update, or None to stay silent."""
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return None
        username = (msg.get("from") or {}).get("username")
        if not self._is_allowed(username):
            return None  # ignore everyone except the allowed user

        chat_id = (msg.get("chat") or {}).get("id")
        if self.db is not None and chat_id is not None:
            # Remember where to send proactive result notifications later.
            self.db.set_setting("telegram_chat_id", str(chat_id))

        return self._dispatch(_normalize(msg.get("text", "")))

    def _dispatch(self, text: str) -> str:
        if text.startswith("/start") or "yardım" in text or "yardim" in text or "help" in text:
            return HELP
        if "bugün" in text or "bugun" in text:
            return self.on_daily()
        # Tolerant of common misspellings (süpriz / supriz / suprise).
        if any(w in text for w in ("sürpriz", "surpriz", "süpriz", "supriz", "suprise")):
            return self.on_surprise()
        if "kadro" in text and self.on_lineup is not None:
            return self.on_lineup()
        if "durum" in text and self.on_status is not None:
            return self.on_status()
        return HELP

    def _command_name(self, text: str) -> str:
        if text.startswith("/start") or any(
            word in text for word in ("yardım", "yardim", "help")
        ):
            return "help"
        if "bugün" in text or "bugun" in text:
            return "daily"
        if any(
            word in text
            for word in ("sürpriz", "surpriz", "süpriz", "supriz", "suprise")
        ):
            return "surprise"
        if "kadro" in text and self.on_lineup is not None:
            return "lineup"
        if "durum" in text and self.on_status is not None:
            return "status"
        return "help"

    def _renew_lease(self, now: int) -> None:
        if not self.db.acquire_telegram_bot_lease(
            self.owner_id, now, self.lease_seconds
        ):
            runtime = self.db.telegram_bot_runtime() or {}
            raise BotLeaseError(
                f"Telegram bot lease is held by {runtime.get('owner_id', 'unknown')}"
            )
        if not self._recovered:
            self.db.recover_telegram_commands(now)
            self._recovered = True

    def _process_inbox(self) -> None:
        for row in self.db.pending_telegram_inbox():
            update_id = row["update_id"]
            if not self.db.begin_telegram_command(update_id):
                continue
            update = row["payload"]
            msg = update.get("message") or update.get("edited_message")
            now = int(self.clock())
            if not msg:
                self.db.reject_telegram_command(
                    update_id, now, "unsupported update without message"
                )
                continue
            username = (msg.get("from") or {}).get("username")
            if not self._is_allowed(username):
                self.db.reject_telegram_command(
                    update_id, now, "username not on allow-list"
                )
                continue
            chat_id = (msg.get("chat") or {}).get("id")
            if chat_id is None:
                self.db.reject_telegram_command(update_id, now, "missing chat id")
                continue

            text = _normalize(msg.get("text", ""))
            command = self._command_name(text)
            self.db.authorize_telegram_command(
                update_id, chat_id, command, now
            )
            self.db.audit_telegram_command(
                update_id, "dispatch", "started", now, command
            )
            try:
                reply = self._dispatch(text)
            except Exception as exc:
                failed_at = int(self.clock())
                self.db.fail_telegram_render(
                    update_id, f"{type(exc).__name__}: {exc}", failed_at
                )
                # An unanswered command looks exactly like a dead bot, so a
                # failure is reported rather than swallowed.
                self.db.enqueue_telegram_failure_reply(
                    update_id, chat_id, RENDER_FAILURE_REPLY, failed_at
                )
                continue
            rendered_at = int(self.clock())
            self.db.audit_telegram_command(
                update_id, "dispatch", "completed", rendered_at, command
            )
            self.db.enqueue_telegram_reply(
                update_id, chat_id, reply, rendered_at
            )

    def _retry_delay(self, attempts: int, retry_after: int | None) -> int:
        exponential = min(
            self.retry_base_seconds * (2 ** max(0, attempts - 1)),
            self.retry_max_seconds,
        )
        if retry_after is None:
            return exponential
        return min(max(exponential, int(retry_after)), self.retry_max_seconds)

    def _process_outbox(self) -> int:
        sent = 0
        now = int(self.clock())
        for candidate in self.db.due_telegram_outbox(now):
            row = self.db.begin_telegram_send(candidate["id"], now)
            if row is None:
                continue
            try:
                response = self.client.send_message(
                    row["chat_id"], row["reply_text"]
                )
            except Exception as exc:
                failed_at = int(self.clock())
                detail = f"{type(exc).__name__}: {exc}"
                if getattr(exc, "ambiguous", True):
                    self.db.mark_telegram_send_ambiguous(
                        row["id"], detail, failed_at
                    )
                elif getattr(exc, "retryable", False):
                    if row["attempts"] >= self.max_send_attempts:
                        self.db.fail_telegram_send(row["id"], detail, failed_at)
                    else:
                        delay = self._retry_delay(
                            row["attempts"], getattr(exc, "retry_after", None)
                        )
                        self.db.retry_telegram_send(
                            row["id"], detail, failed_at + delay, failed_at
                        )
                else:
                    self.db.fail_telegram_send(row["id"], detail, failed_at)
                continue

            message_id = (
                response.get("message_id") if isinstance(response, dict) else None
            )
            response_chat_id = (
                (response.get("chat") or {}).get("id")
                if isinstance(response, dict)
                else None
            )
            if message_id is None or response_chat_id is None:
                self.db.mark_telegram_send_ambiguous(
                    row["id"], "sendMessage acknowledgement missing ids", int(self.clock())
                )
                continue
            self.db.complete_telegram_send(
                row["id"],
                message_id=message_id,
                chat_id=response_chat_id,
                telegram_date=response.get("date"),
                now=int(self.clock()),
            )
            sent += 1
            stamp = time.strftime("%H:%M:%S")
            print(
                f"[{stamp}] update {row['update_id']} -> chat "
                f"{response_chat_id} | yanıt gönderildi"
            )
        return sent

    def _reset_connection(self) -> None:
        reset = getattr(self.client, "reset_connections", None)
        if callable(reset):
            reset()

    def _fetch_updates(self, timeout: int | None) -> list[dict]:
        """Fetch updates, recording poll health so stalls are observable."""
        try:
            updates = self.client.get_updates(
                offset=self.db.telegram_poll_offset(), timeout=timeout
            )
        except Exception as exc:
            self._poll_failures += 1
            self.db.record_telegram_poll(
                self.owner_id,
                int(self.clock()),
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            # Repeated failures mean the pooled socket is unusable, not that
            # Telegram is down; dial a fresh connection before messages pile up.
            if self._poll_failures >= self.poll_reset_after_failures:
                self._reset_connection()
                self._poll_failures = 0
            raise
        self._poll_failures = 0
        self.db.record_telegram_poll(self.owner_id, int(self.clock()), ok=True)
        return updates

    def poll_once(self, timeout: int | None = None) -> int:
        now = int(self.clock())
        self._renew_lease(now)

        # Drain durable work before a potentially long getUpdates request.
        self._process_inbox()
        sent = self._process_outbox()

        self._renew_lease(int(self.clock()))
        updates = self._fetch_updates(timeout)
        self._offset = self.db.persist_telegram_updates(
            updates, int(self.clock())
        )
        self._process_inbox()
        sent += self._process_outbox()
        self._renew_lease(int(self.clock()))
        return sent

    def run(self, poll_timeout: int | None = None) -> None:
        """Poll until interrupted.

        A deliberate stop propagates as ``KeyboardInterrupt`` so a supervisor
        can tell it apart from a crash and refrain from restarting the bot.
        """
        print("Bot çalışıyor (long polling). Durdurmak için Ctrl-C.")
        backoff = config.TELEGRAM_POLL_RETRY_BASE_SECONDS
        reported: str | None = None
        try:
            while True:
                try:
                    self.poll_once(poll_timeout)
                except KeyboardInterrupt:
                    print("Bot durduruluyor.")
                    raise
                except Exception as exc:
                    # A persistent condition is reported once so that repeated
                    # retries cannot bury a different, newer failure.
                    message = str(exc)
                    if message != reported:
                        print(f"Bot yoklama hatası (yeniden deneniyor): {exc}")
                        reported = message
                    # The user is already waiting, so recover in under a second
                    # rather than adding a fixed penalty to every hiccup.
                    self.sleep(backoff)
                    backoff = min(
                        backoff * 2, config.TELEGRAM_POLL_RETRY_MAX_SECONDS
                    )
                else:
                    if reported is not None:
                        print("Bot yoklaması normale döndü.")
                        reported = None
                    backoff = config.TELEGRAM_POLL_RETRY_BASE_SECONDS
        finally:
            self.db.release_telegram_bot_lease(
                self.owner_id, int(self.clock())
            )
