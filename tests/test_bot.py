import pytest

from otomasyon.storage import Database
from otomasyon.telegram import bot as bot_module
from otomasyon.telegram.bot import Bot, HELP
from otomasyon.telegram.client import TelegramSendError


class FakeClient:
    def __init__(self, updates=None):
        self._updates = list(updates or [])
        self.sent = []

    def get_updates(self, offset=None, timeout=25):
        updates = [u for u in self._updates if offset is None or u["update_id"] >= offset]
        return updates

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return {
            "message_id": 700 + len(self.sent),
            "date": 1_786_258_800,
            "chat": {"id": int(chat_id)},
        }


def _update(update_id, username, text, chat_id=555):
    return {
        "update_id": update_id,
        "message": {
            "from": {"username": username},
            "chat": {"id": chat_id},
            "text": text,
        },
    }


def _bot(client, db=None):
    return Bot(
        client,
        "AbdulsamedErden",
        on_daily=lambda: "DAILY",
        on_surprise=lambda: "SURPRISE",
        on_lineup=lambda: "LINEUP",
        on_status=lambda: "STATUS",
        db=db,
    )


def test_allowed_user_bugun_and_surpriz():
    bot = _bot(FakeClient())
    assert bot.handle_update(_update(1, "AbdulsamedErden", "bugün")) == "DAILY"
    assert bot.handle_update(_update(2, "abdulsamederden", "Sürpriz")) == "SURPRISE"
    assert bot.handle_update(_update(3, "AbdulsamedErden", "/start")) == HELP
    assert bot.handle_update(_update(4, "AbdulsamedErden", "kadro")) == "LINEUP"
    assert bot.handle_update(_update(5, "AbdulsamedErden", "durum")) == "STATUS"


def test_unknown_command_returns_help():
    bot = _bot(FakeClient())
    assert bot.handle_update(_update(1, "AbdulsamedErden", "selam")) == HELP


def test_surprise_typo_tolerance():
    bot = _bot(FakeClient())
    for typo in ("süpriz", "supriz", "Suprise"):
        assert bot.handle_update(_update(1, "AbdulsamedErden", typo)) == "SURPRISE"


def test_other_users_are_ignored():
    bot = _bot(FakeClient())
    assert bot.handle_update(_update(1, "someone_else", "bugün")) is None
    assert bot.handle_update(_update(2, None, "bugün")) is None


def test_records_chat_id_for_allowed_user():
    db = Database(":memory:")
    bot = _bot(FakeClient(), db=db)
    bot.handle_update(_update(1, "AbdulsamedErden", "bugün", chat_id=98765))
    assert db.get_setting("telegram_chat_id") == "98765"
    db.close()


def test_poll_once_sends_reply_and_advances_offset():
    client = FakeClient([_update(10, "AbdulsamedErden", "bugün", chat_id=42)])
    bot = _bot(client)
    sent = bot.poll_once(timeout=0)
    assert sent == 1
    assert client.sent == [("42", "DAILY")]
    assert bot._offset == 11


def test_poll_error_does_not_stop_long_polling(monkeypatch):
    class FailingPollClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.polls = 0

        def get_updates(self, offset=None, timeout=25):
            self.polls += 1
            if self.polls == 1:
                raise TimeoutError("getUpdates timed out")
            raise KeyboardInterrupt

    bot = Bot(
        FailingPollClient(),
        "AbdulsamedErden",
        on_daily=lambda: "DAILY",
        on_surprise=lambda: "SURPRISE",
    )
    monkeypatch.setattr(bot_module.time, "sleep", lambda _seconds: None)

    bot.run(poll_timeout=0)

    assert bot.client.polls == 2


def test_keyboard_interrupt_stops_bot():
    class InterruptedPollClient(FakeClient):
        def get_updates(self, offset=None, timeout=25):
            raise KeyboardInterrupt

    bot = Bot(
        InterruptedPollClient(),
        "AbdulsamedErden",
        on_daily=lambda: "DAILY",
        on_surprise=lambda: "SURPRISE",
    )

    bot.run(poll_timeout=0)


class Clock:
    def __init__(self, value=1_000):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class PlannedTelegram(FakeClient):
    def __init__(self, updates=None, *, sends=None, polls=None):
        super().__init__(updates)
        self.send_plan = list(sends or [])
        self.poll_plan = list(polls or [])
        self.offsets = []

    def get_updates(self, offset=None, timeout=25):
        self.offsets.append(offset)
        if self.poll_plan:
            outcome = self.poll_plan.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return super().get_updates(offset, timeout)

    def send_message(self, chat_id, text):
        self.sent.append((str(chat_id), text))
        if self.send_plan:
            outcome = self.send_plan.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return {
            "message_id": 900 + len(self.sent),
            "date": 1_786_258_800,
            "chat": {"id": int(chat_id)},
        }


def _durable_bot(path, client, clock, **kwargs):
    db = Database(path)
    return Bot(
        client,
        "AbdulsamedErden",
        on_daily=lambda: "DAILY",
        on_surprise=lambda: "SURPRISE",
        on_lineup=lambda: "LINEUP",
        on_status=lambda: "STATUS",
        db=db,
        clock=clock,
        **kwargs,
    )


def test_command_lifecycle_is_durable_and_receipted(tmp_path):
    path = str(tmp_path / "commands.db")
    clock = Clock()
    client = PlannedTelegram(
        [_update(40, "AbdulsamedErden", "durum", chat_id=42)]
    )
    bot = _durable_bot(path, client, clock, owner_id="one")

    assert bot.poll_once(timeout=0) == 1

    with Database(path) as db:
        assert db.telegram_poll_offset() == 41
        inbox = db.telegram_command_inbox(40)
        outbox = db.telegram_command_outbox(40)
        receipt = db.telegram_command_receipt(40)
        assert inbox["status"] == "completed"
        assert inbox["authorized"] == 1
        assert inbox["command"] == "status"
        assert outbox["status"] == "sent"
        assert outbox["attempts"] == 1
        assert receipt["message_id"] == 901
        assert receipt["chat_id"] == "42"
        assert receipt["telegram_date"] == 1_786_258_800
        assert receipt["received_ts"] == 1_000
        assert receipt["acknowledged_ts"] == 1_000
        assert [
            (row["stage"], row["outcome"])
            for row in db.telegram_command_audit(40)
        ] == [
            ("authorization", "allowed"),
            ("dispatch", "started"),
            ("dispatch", "completed"),
            ("render", "completed"),
            ("send", "attempted"),
            ("send", "acknowledged"),
        ]


def test_restart_drains_pending_inbox_and_duplicate_update_is_idempotent(tmp_path):
    path = str(tmp_path / "restart.db")
    update = _update(50, "AbdulsamedErden", "bugün", chat_id=77)
    with Database(path) as db:
        assert db.persist_telegram_updates([update], received_ts=1_000) == 51

    clock = Clock()
    first = PlannedTelegram([update])
    bot = _durable_bot(path, first, clock, owner_id="restart")
    assert bot.poll_once(timeout=0) == 1
    assert first.offsets == [51]
    assert first.sent == [("77", "DAILY")]

    # Telegram returning the same update again cannot render or send it twice.
    bot.db.persist_telegram_updates([update], received_ts=1_001)
    assert bot.poll_once(timeout=0) == 0
    assert first.sent == [("77", "DAILY")]
    with Database(path) as db:
        assert db.count("telegram_command_inbox") == 1
        assert db.count("telegram_command_outbox") == 1
        assert db.count("telegram_command_reply_receipts") == 1


def test_poll_offset_does_not_advance_when_batch_cannot_be_persisted(tmp_path):
    path = str(tmp_path / "atomic-poll.db")
    client = PlannedTelegram(
        polls=[[{"update_id": 60}, {"update_id": "invalid"}]]
    )
    bot = _durable_bot(path, client, Clock(), owner_id="atomic")

    with pytest.raises(ValueError, match="update_id"):
        bot.poll_once(timeout=0)

    with Database(path) as db:
        assert db.telegram_poll_offset() is None
        assert db.count("telegram_command_inbox") == 0


def test_definitive_send_failures_retry_with_bounded_backoff(tmp_path):
    path = str(tmp_path / "retry.db")
    clock = Clock()
    failures = [
        TelegramSendError("rate limited", ambiguous=False, retryable=True)
        for _ in range(4)
    ]
    client = PlannedTelegram(
        [_update(70, "AbdulsamedErden", "bugün")], sends=failures
    )
    bot = _durable_bot(
        path,
        client,
        clock,
        owner_id="retry",
        max_send_attempts=4,
        retry_base_seconds=2,
        retry_max_seconds=5,
    )

    assert bot.poll_once(timeout=0) == 0
    with Database(path) as db:
        assert db.telegram_command_outbox(70)["next_attempt_ts"] == 1_002

    for advance, expected_due in ((2, 1_006), (4, 1_011)):
        clock.advance(advance)
        assert bot.poll_once(timeout=0) == 0
        with Database(path) as db:
            assert db.telegram_command_outbox(70)["next_attempt_ts"] == expected_due

    clock.advance(5)
    assert bot.poll_once(timeout=0) == 0
    with Database(path) as db:
        outbox = db.telegram_command_outbox(70)
        assert outbox["status"] == "failed"
        assert outbox["attempts"] == 4
    assert len(client.sent) == 4


def test_ambiguous_send_timeout_is_retained_and_never_auto_retried(tmp_path):
    path = str(tmp_path / "ambiguous.db")
    clock = Clock()
    client = PlannedTelegram(
        [_update(80, "AbdulsamedErden", "bugün")],
        sends=[TimeoutError("read timed out after request upload")],
    )
    bot = _durable_bot(path, client, clock, owner_id="before")
    assert bot.poll_once(timeout=0) == 0

    with Database(path) as db:
        assert db.telegram_command_outbox(80)["status"] == "ambiguous"
        assert db.telegram_command_receipt(80) is None

    clock.advance(301)
    restarted_client = PlannedTelegram([])
    restarted = _durable_bot(
        path, restarted_client, clock, owner_id="after"
    )
    assert restarted.poll_once(timeout=0) == 0
    assert restarted_client.sent == []
    with Database(path) as db:
        assert db.telegram_command_outbox(80)["status"] == "ambiguous"


def test_read_timeout_keeps_offset_and_does_not_duplicate_receipted_reply(tmp_path):
    path = str(tmp_path / "read-timeout.db")
    clock = Clock()
    update = _update(90, "AbdulsamedErden", "durum")
    client = PlannedTelegram(polls=[[update], TimeoutError("getUpdates timeout"), []])
    bot = _durable_bot(path, client, clock, owner_id="reader")

    assert bot.poll_once(timeout=0) == 1
    with pytest.raises(TimeoutError):
        bot.poll_once(timeout=0)
    assert bot.poll_once(timeout=0) == 0
    assert client.offsets == [None, 91, 91]
    assert len(client.sent) == 1


def test_interrupted_sending_state_becomes_ambiguous_on_restart(tmp_path):
    path = str(tmp_path / "crash.db")
    update = _update(100, "AbdulsamedErden", "bugün")
    clock = Clock()
    client = PlannedTelegram([update])
    bot = _durable_bot(path, client, clock, owner_id="crashed")
    bot._renew_lease(1_000)
    bot.db.persist_telegram_updates([update], 1_000)
    bot._process_inbox()
    outbox = bot.db.telegram_command_outbox(100)
    assert bot.db.begin_telegram_send(outbox["id"], 1_000)["status"] == "sending"

    clock.advance(301)
    restarted_client = PlannedTelegram([])
    restarted = _durable_bot(
        path, restarted_client, clock, owner_id="replacement"
    )
    assert restarted.poll_once(timeout=0) == 0
    assert restarted_client.sent == []
    with Database(path) as db:
        assert db.telegram_command_outbox(100)["status"] == "ambiguous"


def test_single_instance_lease_heartbeats_and_expires(tmp_path):
    path = str(tmp_path / "lease.db")
    clock = Clock()
    first = _durable_bot(path, PlannedTelegram([]), clock, owner_id="first")
    second = _durable_bot(path, PlannedTelegram([]), clock, owner_id="second")

    assert first.poll_once(timeout=0) == 0
    with Database(path) as db:
        runtime = db.telegram_bot_runtime()
        assert runtime["owner_id"] == "first"
        assert runtime["heartbeat_ts"] == 1_000
        assert runtime["lease_expires_ts"] == 1_300

    with pytest.raises(bot_module.BotLeaseError):
        second.poll_once(timeout=0)

    clock.advance(300)
    assert second.poll_once(timeout=0) == 0
    with Database(path) as db:
        runtime = db.telegram_bot_runtime()
        assert runtime["owner_id"] == "second"
        assert runtime["heartbeat_ts"] == 1_300


def test_allow_list_rejection_is_audited_without_outbox(tmp_path):
    path = str(tmp_path / "allow-list.db")
    client = PlannedTelegram([_update(110, "someone_else", "bugün")])
    bot = _durable_bot(path, client, Clock(), owner_id="allow")

    assert bot.poll_once(timeout=0) == 0
    with Database(path) as db:
        inbox = db.telegram_command_inbox(110)
        assert inbox["status"] == "completed"
        assert inbox["authorized"] == 0
        assert db.telegram_command_outbox(110) is None
        assert db.telegram_command_audit(110)[0]["outcome"] == "rejected"
    assert client.sent == []
