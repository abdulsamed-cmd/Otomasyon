"""Long-poll transport durability.

The bot stopped answering because idle long-poll connections were dropped
silently and the client waited out the full read timeout while messages queued
on Telegram's side. These tests pin the properties that bound that stall.
"""

import socket

import pytest
import requests

from otomasyon import config
from otomasyon.telegram import supervise
from otomasyon.telegram.client import (
    POLL_READ_MARGIN_SECONDS,
    TelegramClient,
    TelegramPollError,
    build_session,
)


class RecordingSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.closed = 0

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def close(self):
        self.closed += 1


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _client(session):
    return TelegramClient(token="test-token", session=session)


def test_socket_keepalive_is_enabled_on_owned_sessions():
    """Keep-alive probes surface a dropped connection before the read timeout."""
    session = build_session()
    adapter = session.get_adapter("https://api.telegram.org")
    options = adapter.poolmanager.connection_pool_kw["socket_options"]
    session.close()

    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in options
    probes = {
        option: value
        for level, option, value in options
        if level == socket.IPPROTO_TCP
    }
    idle = getattr(socket, "TCP_KEEPIDLE", None)
    if idle is not None:
        # Probing must start well before a typical NAT idle timeout.
        assert probes[idle] <= 60


def test_poll_read_timeout_exceeds_the_long_poll_deadline():
    """A read timeout below the poll deadline would abort healthy polls."""
    session = RecordingSession([FakeResponse({"ok": True, "result": []})])
    client = _client(session)

    client.get_updates(offset=5)

    call = session.calls[0]
    connect_timeout, read_timeout = call["timeout"]
    assert call["params"]["timeout"] == config.TELEGRAM_POLL_TIMEOUT_SECONDS
    assert read_timeout == (
        config.TELEGRAM_POLL_TIMEOUT_SECONDS + POLL_READ_MARGIN_SECONDS
    )
    assert connect_timeout == config.TELEGRAM_CONNECT_TIMEOUT_SECONDS


def test_one_poll_attempt_is_bounded_in_total_time():
    """Internal retries restart the clock, turning a stall into minutes.

    A single attempt must be capped by connect + read, because the bot redials
    itself after a failure and a longer attempt only hides incoming messages.
    """
    assert config.TELEGRAM_POLL_CONNECT_RETRIES == 0
    worst_case = (
        config.TELEGRAM_CONNECT_TIMEOUT_SECONDS
        + config.TELEGRAM_POLL_TIMEOUT_SECONDS
        + POLL_READ_MARGIN_SECONDS
    )
    assert worst_case <= 60

    poll_adapter = TelegramClient(token="t").poll_session.get_adapter(
        "https://api.telegram.org"
    )
    assert poll_adapter.max_retries.total == 0


def test_long_poll_is_short_enough_to_bound_a_silent_drop():
    """A dropped connection can hide messages only until the poll deadline."""
    assert config.TELEGRAM_POLL_TIMEOUT_SECONDS <= 20


def test_poll_timeout_raises_poll_error_and_retires_the_socket():
    session = RecordingSession([requests.Timeout("read timed out")])
    client = TelegramClient(token="test-token")
    client.poll_session = session
    client._owns_session = True

    with pytest.raises(TelegramPollError):
        client.get_updates(offset=1)

    assert session.closed == 1
    assert client.poll_session is not session


def test_sends_never_retry_automatically():
    """An automatic retry after delivery would send the same message twice."""
    client = TelegramClient(token="test-token")
    send_adapter = client.session.get_adapter("https://api.telegram.org")
    poll_adapter = client.poll_session.get_adapter("https://api.telegram.org")

    assert send_adapter.max_retries.total == 0
    assert poll_adapter.max_retries.connect == (
        config.TELEGRAM_POLL_CONNECT_RETRIES
    )
    assert client.session is not client.poll_session


def test_injected_sessions_are_never_rebuilt():
    """Tests and callers that supply a session keep ownership of it."""
    session = RecordingSession([requests.ConnectionError("boom")])
    client = _client(session)

    with pytest.raises(TelegramPollError):
        client.get_updates()

    assert session.closed == 0
    assert client.session is session


def test_poll_rejection_is_reported_without_losing_the_offset():
    session = RecordingSession(
        [FakeResponse({"ok": False, "description": "Unauthorized"})]
    )
    client = _client(session)

    with pytest.raises(TelegramPollError):
        client.get_updates(offset=9)


def test_supervisor_restarts_the_bot_after_a_crash():
    attempts = []
    delays = []

    def start():
        attempts.append(len(attempts))
        raise RuntimeError("bot died")

    exit_code = supervise(
        start,
        sleep=delays.append,
        clock=lambda: 0.0,
        max_restarts=3,
        on_restart=lambda *_: None,
    )

    assert exit_code == 1
    assert len(attempts) == 4
    assert delays == sorted(delays)
    assert max(delays) <= config.TELEGRAM_SUPERVISOR_MAX_BACKOFF_SECONDS


def test_supervisor_restarts_when_the_bot_returns_unexpectedly():
    """A bot that returns without being asked to stop is still a failure."""
    starts = []

    def start():
        starts.append(1)

    supervise(
        start,
        sleep=lambda _s: None,
        clock=lambda: 0.0,
        max_restarts=2,
        on_restart=lambda *_: None,
    )

    assert len(starts) == 3


def test_supervisor_stops_on_keyboard_interrupt():
    """Ctrl-C must stop the bot for good, not trigger an endless restart loop."""
    starts = []

    def start():
        starts.append(1)
        raise KeyboardInterrupt

    assert supervise(start, sleep=lambda _s: None) == 0
    assert len(starts) == 1


def test_interrupted_bot_run_reaches_the_supervisor():
    """The bot's own stop path must surface as an interrupt, not a clean return."""
    from otomasyon.storage import Database
    from otomasyon.telegram.bot import Bot

    class Interrupted:
        def get_updates(self, offset=None, timeout=None):
            raise KeyboardInterrupt

        def send_message(self, chat_id, text):  # pragma: no cover - unused
            raise AssertionError("no reply expected")

    bot = Bot(
        Interrupted(),
        "AbdulsamedErden",
        on_daily=lambda: "DAILY",
        on_surprise=lambda: "SURPRISE",
        db=Database(":memory:"),
    )

    assert supervise(lambda: bot.run(poll_timeout=0), sleep=lambda _s: None) == 0


def test_supervisor_backoff_resets_after_a_healthy_run():
    delays = []
    times = iter([0.0, 1_000.0, 1_000.0, 2_000.0, 2_000.0, 3_000.0])

    def start():
        raise RuntimeError("late failure")

    supervise(
        start,
        sleep=delays.append,
        clock=lambda: next(times),
        max_restarts=2,
        on_restart=lambda *_: None,
    )

    assert delays[0] == delays[1] == config.TELEGRAM_SUPERVISOR_BACKOFF_SECONDS
