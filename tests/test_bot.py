from otomasyon.storage import Database
from otomasyon.telegram.bot import Bot, HELP


class FakeClient:
    def __init__(self, updates=None):
        self._updates = list(updates or [])
        self.sent = []

    def get_updates(self, offset=None, timeout=25):
        updates = [u for u in self._updates if offset is None or u["update_id"] >= offset]
        return updates

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return {"ok": True}


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
        db=db,
    )


def test_allowed_user_bugun_and_surpriz():
    bot = _bot(FakeClient())
    assert bot.handle_update(_update(1, "AbdulsamedErden", "bugün")) == "DAILY"
    assert bot.handle_update(_update(2, "abdulsamederden", "Sürpriz")) == "SURPRISE"
    assert bot.handle_update(_update(3, "AbdulsamedErden", "/start")) == HELP


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
    assert client.sent == [(42, "DAILY")]
    assert bot._offset == 11
