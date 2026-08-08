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
from collections.abc import Callable
from datetime import datetime

from .. import config

HELP = (
    "Merhaba! Komutlar:\n"
    "• bugün — günün düşük riskli kuponları (ana + alternatif)\n"
    "• sürpriz — 6+ Gol laboratuvarı ve sistem senaryoları\n\n"
    "• kadro — doğrulanmış ilk 11 rotasyon riskleri\n\n"
    "Not: Otomatik oynama yapılmaz; yalnızca bilgilendirme."
)


def _normalize(text: str) -> str:
    return (text or "").strip().casefold()


def _chat_id_of(update: dict) -> int | None:
    msg = update.get("message") or update.get("edited_message") or {}
    return (msg.get("chat") or {}).get("id")


class Bot:
    def __init__(
        self,
        client,
        allowed_username: str | None,
        *,
        on_daily: Callable[[], str],
        on_surprise: Callable[[], str],
        on_lineup: Callable[[], str] | None = None,
        db=None,
        push_hour: int | None = None,
        push_callback: Callable[[], object] | None = None,
        result_callback: Callable[[], object] | None = None,
        context_callback: Callable[[], object] | None = None,
        history_callback: Callable[[], object] | None = None,
    ) -> None:
        self.client = client
        self.allowed = (allowed_username or "").lstrip("@")
        self.on_daily = on_daily
        self.on_surprise = on_surprise
        self.on_lineup = on_lineup
        self.db = db
        self.push_hour = push_hour
        self.push_callback = push_callback
        self.result_callback = result_callback
        self.context_callback = context_callback
        self.history_callback = history_callback
        self._offset: int | None = None

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
        return HELP

    def _maybe_scheduled_push(self) -> None:
        """Proactively push the daily coupon at the configured local hour.

        The push callback is responsible for de-duplicating per day, so calling
        this every poll cycle is safe.
        """
        if self.push_hour is None or self.push_callback is None:
            return
        if datetime.now(tz=config.TIMEZONE).hour < self.push_hour:
            return
        try:
            result = self.push_callback()
            if result:
                stamp = time.strftime("%H:%M:%S")
                print(f"[{stamp}] proaktif günlük kupon gönderildi -> chat {result}")
        except Exception as exc:  # pragma: no cover - resilience
            print(f"Zamanlanmış gönderim hatası: {exc}")

    def poll_once(self, timeout: int = 25) -> int:
        updates = self.client.get_updates(offset=self._offset, timeout=timeout)
        sent = 0
        for u in updates:
            self._offset = u["update_id"] + 1
            reply = self.handle_update(u)
            if reply:
                chat_id = _chat_id_of(u)
                if chat_id is not None:
                    self.client.send_message(chat_id, reply)
                    sent += 1
                    stamp = time.strftime("%H:%M:%S")
                    incoming = ((u.get("message") or {}).get("text") or "").strip()
                    print(f"[{stamp}] chat {chat_id} <- '{incoming}' | yanıt gönderildi")
        return sent

    def run(self, poll_timeout: int = 25) -> None:
        print("Bot çalışıyor (long polling). Durdurmak için Ctrl-C.")
        while True:
            try:
                poll_failed = False
                try:
                    self.poll_once(poll_timeout)
                except Exception as exc:
                    poll_failed = True
                    print(f"Bot hata (devam ediliyor): {exc}")

                self._maybe_scheduled_push()
                if self.result_callback is not None:
                    try:
                        report = self.result_callback()
                        if report and report.get("settled"):
                            print(
                                f"Sonuç taraması: {report['matched']} maç eşleşti, "
                                f"{report['settled']} kupon kapandı ve bildirildi"
                            )
                    except Exception as exc:
                        print(f"Sonuç taraması hatası: {exc}")
                if self.context_callback is not None:
                    try:
                        report = self.context_callback()
                        if report and not report.get("skipped"):
                            print(
                                f"Bağlamsal capture: {report['matched']} maç eşleşti, "
                                f"{report['prematch_lineups']} maç önü kadro"
                            )
                    except Exception as exc:
                        print(f"Bağlamsal capture hatası: {exc}")
                if self.history_callback is not None:
                    try:
                        report = self.history_callback()
                        if report and not report.get("skipped") and report.get("days"):
                            print(
                                f"Tarihsel arşiv: {len(report['days'])} gün, "
                                f"{report['rows']} maç kaydedildi"
                            )
                    except Exception as exc:
                        print(f"Tarihsel arşiv hatası: {exc}")
                if poll_failed:
                    time.sleep(3)
            except KeyboardInterrupt:  # pragma: no cover
                print("Bot durduruluyor.")
                return
            except Exception as exc:  # pragma: no cover - resilience loop
                print(f"Bot hata (devam ediliyor): {exc}")
                time.sleep(3)
