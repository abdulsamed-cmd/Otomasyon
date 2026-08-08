import re

from otomasyon import config, engine
from otomasyon.storage import Database
from otomasyon.web import create_app

from .test_engine import NOW, _events
from .test_surprise import _goals_event
from otomasyon import surprise


def _seed(path):
    events = _events()
    events[0].home = "PRIVATE HOME TEAM"
    coupons = engine.build_daily_coupons(events, now=NOW)
    with Database(path) as db:
        db.upsert_competitions(
            {1: {"name": "Test Lig", "country_code": "TR"}}
        )
        db.save_events(events, now=int(NOW.timestamp()))
        coupon_id = db.save_coupon(
            coupons["main"], "2026-08-08", now=int(NOW.timestamp())
        )
        db.conn.execute("UPDATE coupons SET status='won' WHERE id=?", (coupon_id,))
        db.conn.execute(
            "UPDATE coupon_legs SET result='win' WHERE coupon_id=?", (coupon_id,)
        )
        db.conn.commit()
    return coupon_id


def _csrf(html: bytes) -> str:
    match = re.search(rb'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1).decode()


def _seed_surprise(path):
    events = [_goals_event(51, 5.0), _goals_event(52, 7.0)]
    events[0].home = "PRIVATE SURPRISE TEAM"
    report = surprise.build_surprise(events, now=NOW)
    with Database(path) as db:
        db.upsert_competitions(
            {1: {"name": "Test Lig", "country_code": "TR"}}
        )
        db.save_events(events, now=int(NOW.timestamp()))
        return db.save_surprise_report(
            report, "2026-W32", now=int(NOW.timestamp())
        )


def test_public_dashboard_exposes_metrics_but_not_coupon_details(tmp_path):
    db_path = str(tmp_path / "web.db")
    _seed(db_path)
    app = create_app(
        {
            "TESTING": True,
            "DB_PATH": db_path,
            "SECRET_KEY": "test-secret",
            "DASHBOARD_PASSWORD": "correct-password",
        }
    )
    response = app.test_client().get("/")
    assert response.status_code == 200
    assert b"G\xc3\xbcnl\xc3\xbck ana" in response.data
    assert b"xg-ou-v1" in response.data
    assert b"PRIVATE HOME TEAM" not in response.data
    assert response.headers["X-Frame-Options"] == "DENY"


def test_private_coupon_details_require_password_and_csrf(tmp_path):
    db_path = str(tmp_path / "web.db")
    coupon_id = _seed(db_path)
    app = create_app(
        {
            "TESTING": True,
            "DB_PATH": db_path,
            "SECRET_KEY": "test-secret",
            "DASHBOARD_PASSWORD": "correct-password",
        }
    )
    client = app.test_client()
    assert client.get(f"/coupons/{coupon_id}").status_code == 302
    login = client.get("/login")
    response = client.post(
        "/login",
        data={"csrf_token": _csrf(login.data), "password": "correct-password"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    detail = client.get(f"/coupons/{coupon_id}")
    assert b"PRIVATE HOME TEAM" in detail.data


def test_surprise_details_are_also_private(tmp_path):
    db_path = str(tmp_path / "web.db")
    report_id = _seed_surprise(db_path)
    app = create_app(
        {
            "TESTING": True,
            "DB_PATH": db_path,
            "SECRET_KEY": "test-secret",
            "DASHBOARD_PASSWORD": "correct-password",
        }
    )
    client = app.test_client()
    assert b"PRIVATE SURPRISE TEAM" not in client.get("/").data
    assert client.get(f"/surprises/{report_id}").status_code == 302
    login = client.get("/login")
    client.post(
        "/login",
        data={"csrf_token": _csrf(login.data), "password": "correct-password"},
    )
    detail = client.get(f"/surprises/{report_id}")
    assert b"PRIVATE SURPRISE TEAM" in detail.data
    assert b"2/2 sistem" in detail.data


def test_dashboard_health_checks_database(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DB_PATH": str(tmp_path / "health.db"),
            "SECRET_KEY": "test-secret",
        }
    )
    assert app.test_client().get("/healthz").json == {"status": "ok"}


def test_login_rate_limits_repeated_failures(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DB_PATH": str(tmp_path / "rate.db"),
            "SECRET_KEY": "test-secret",
            "DASHBOARD_PASSWORD": "correct-password",
        }
    )
    client = app.test_client()
    login = client.get("/login")
    token = _csrf(login.data)
    for _ in range(5):
        assert client.post(
            "/login", data={"csrf_token": token, "password": "wrong"}
        ).status_code == 200
    assert client.post(
        "/login", data={"csrf_token": token, "password": "wrong"}
    ).status_code == 429
