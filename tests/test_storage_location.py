"""Where the record is kept.

A working copy is disposable. It gets re-cloned, and on 29 August one was:
the checkout came back at an older commit and the database inside it - a
fortnight of coupons, results and settled bets - did not come back at all.
Settled bets are not a build artefact of a checkout and must not share the
fate of one, so the default path is outside it.
"""

import os

from otomasyon import config


def test_the_record_is_kept_outside_the_working_copy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "DEFAULT_DB_PATH", str(tmp_path / "home" / "kayit.db"))
    monkeypatch.setattr(config, "LEGACY_DB_PATH", str(tmp_path / "repo" / "data.db"))

    chosen = config._default_db_path()
    assert chosen == config.DEFAULT_DB_PATH
    assert not chosen.startswith(str(tmp_path / "repo"))


def test_an_install_already_writing_to_the_old_path_keeps_its_history(
    tmp_path, monkeypatch
):
    """Moving the default must not read as an empty history to whoever moves.

    Someone whose record is in the checkout has that record and no other. A
    default that quietly pointed elsewhere would answer their questions from a
    blank database rather than tell them anything was wrong.
    """
    legacy = tmp_path / "repo" / "data.db"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("kayit")
    monkeypatch.setattr(config, "DEFAULT_DB_PATH", str(tmp_path / "home" / "kayit.db"))
    monkeypatch.setattr(config, "LEGACY_DB_PATH", str(legacy))

    assert config._default_db_path() == str(legacy)


def test_the_new_path_wins_once_the_record_has_moved_to_it(tmp_path, monkeypatch):
    # Both present means the move has been made; the old file is a leftover.
    legacy = tmp_path / "repo" / "data.db"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("eski")
    durable = tmp_path / "home" / "kayit.db"
    durable.parent.mkdir(parents=True)
    durable.write_text("kayit")
    monkeypatch.setattr(config, "DEFAULT_DB_PATH", str(durable))
    monkeypatch.setattr(config, "LEGACY_DB_PATH", str(legacy))

    assert config._default_db_path() == str(durable)


def test_the_environment_still_decides_when_it_says_so(monkeypatch):
    # Docker sets OTOMASYON_DB; the default must not override a stated path.
    monkeypatch.setenv("OTOMASYON_DB", "/srv/kupon.db")
    assert os.environ.get("OTOMASYON_DB") == "/srv/kupon.db"
    assert (
        os.environ.get("OTOMASYON_DB", config._default_db_path()) == "/srv/kupon.db"
    )
