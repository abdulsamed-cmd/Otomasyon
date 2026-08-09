"""Public aggregate / private coupon-detail web dashboard."""

from __future__ import annotations

import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from functools import wraps
from threading import Lock

from flask import (
    Flask,
    abort,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from . import config, service
from .storage import Database

KIND_LABELS = {
    "daily_main": "Günlük ana",
    "daily_alt": "Günlük alternatif",
    "surprise": "Sürpriz",
}


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=config.DASHBOARD_SECRET_KEY or secrets.token_hex(32),
        DASHBOARD_PASSWORD=config.DASHBOARD_PASSWORD,
        DASHBOARD_SECRET_CONFIGURED=bool(config.DASHBOARD_SECRET_KEY),
        DB_PATH=config.DB_PATH,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("DASHBOARD_SECURE_COOKIE") == "1",
    )
    if test_config:
        app.config.update(test_config)
        if "SECRET_KEY" in test_config:
            app.config["DASHBOARD_SECRET_CONFIGURED"] = True

    def csrf_token() -> str:
        token = session.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf_token"] = token
        return token

    app.jinja_env.globals.update(
        csrf_token=csrf_token,
        kind_label=lambda kind: KIND_LABELS.get(kind, kind),
    )
    login_attempts = defaultdict(deque)
    attempts_lock = Lock()

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; "
            "img-src 'self'; frame-ancestors 'none'"
        )
        if request.endpoint in {
            "login",
            "logout",
            "coupons",
            "coupon_detail",
            "surprises",
            "surprise_detail",
        }:
            response.headers["Cache-Control"] = "no-store"
        return response

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("authenticated"):
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)

        return wrapped

    @app.get("/healthz")
    def health():
        try:
            with Database(app.config["DB_PATH"]) as db:
                db.count("coupons")
            return {"status": "ok"}
        except Exception:
            return {"status": "error"}, 503

    @app.get("/")
    def index():
        metrics = service.metrics_by_kind(app.config["DB_PATH"])
        shadow = service.shadow_model_metrics(app.config["DB_PATH"])
        goal_metrics = service.surprise_category_metrics(
            app.config["DB_PATH"]
        )
        with Database(app.config["DB_PATH"]) as db:
            recent = db.dashboard_coupon_summaries(limit=12, settled_only=True)
        # Public output deliberately contains no event/team/selection fields.
        return render_template(
            "index.html",
            metrics=metrics,
            shadow=shadow,
            goal_metrics=goal_metrics,
            recent=recent,
        )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        configured = bool(
            app.config.get("DASHBOARD_PASSWORD")
            and app.config.get("DASHBOARD_SECRET_CONFIGURED")
        )
        error = None
        if request.method == "POST":
            submitted_token = request.form.get("csrf_token", "")
            if not hmac.compare_digest(
                submitted_token, session.get("csrf_token", "")
            ):
                abort(400)
            password = request.form.get("password", "")
            expected = app.config.get("DASHBOARD_PASSWORD") or ""
            remote = request.remote_addr or "unknown"
            now = time.time()
            with attempts_lock:
                attempts = login_attempts[remote]
                while attempts and now - attempts[0] > 300:
                    attempts.popleft()
                if len(attempts) >= 5:
                    return render_template(
                        "login.html",
                        error="Çok fazla deneme. Bir süre sonra tekrar deneyin.",
                        configured=configured,
                    ), 429
            if configured and hmac.compare_digest(password, expected):
                with attempts_lock:
                    login_attempts.pop(remote, None)
                session.clear()
                session["authenticated"] = True
                destination = request.args.get("next", "")
                if not destination.startswith("/") or destination.startswith("//"):
                    destination = url_for("coupons")
                return redirect(destination)
            with attempts_lock:
                login_attempts[remote].append(now)
            error = (
                "Dashboard güvenlik secret'ları yapılandırılmamış."
                if not configured
                else "Şifre hatalı."
            )
        return render_template("login.html", error=error, configured=configured)

    @app.post("/logout")
    def logout():
        submitted_token = request.form.get("csrf_token", "")
        if not hmac.compare_digest(
            submitted_token, session.get("csrf_token", "")
        ):
            abort(400)
        session.clear()
        return redirect(url_for("index"))

    @app.get("/coupons")
    @login_required
    def coupons():
        with Database(app.config["DB_PATH"]) as db:
            rows = db.dashboard_coupon_summaries(limit=200)
        return render_template("coupons.html", coupons=rows)

    @app.get("/coupons/<int:coupon_id>")
    @login_required
    def coupon_detail(coupon_id: int):
        with Database(app.config["DB_PATH"]) as db:
            coupon = db.dashboard_coupon(coupon_id)
        if coupon is None:
            abort(404)
        return render_template("coupon_detail.html", coupon=coupon)

    @app.get("/surprises")
    @login_required
    def surprises():
        with Database(app.config["DB_PATH"]) as db:
            rows = db.dashboard_surprise_reports(limit=100)
        return render_template("surprises.html", reports=rows)

    @app.get("/surprises/<int:report_id>")
    @login_required
    def surprise_detail(report_id: int):
        with Database(app.config["DB_PATH"]) as db:
            report = db.dashboard_surprise_report(report_id)
        if report is None:
            abort(404)
        return render_template("surprise_detail.html", report=report)

    return app


app = create_app()
