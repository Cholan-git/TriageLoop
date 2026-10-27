"""Flask application factory."""
from __future__ import annotations

import logging
import os
import secrets
from datetime import timedelta

from flask import Flask, g, jsonify, request

from triage.errors import DomainError
from triage.store import Store
from triage.timefmt import valid_timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
log = logging.getLogger("triageloop")


def _load_dotenv(path: str) -> None:
    """Minimal .env loader (KEY=VALUE lines). Real environment variables win."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _secret_key(instance: str, production: bool) -> str:
    key = os.environ.get("TRIAGELOOP_SECRET_KEY")
    if key:
        return key
    if production:
        raise RuntimeError("TRIAGELOOP_SECRET_KEY must be set when TRIAGELOOP_ENV=production")
    # Development: generate once and keep it outside version control.
    path = os.path.join(instance, "secret_key")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(key)
    return key


def create_app(test_config: dict | None = None) -> Flask:
    _load_dotenv(os.path.join(BASE_DIR, ".env"))
    app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"),
                static_folder=os.path.join(BASE_DIR, "static"))
    instance = os.path.join(BASE_DIR, "instance")
    os.makedirs(instance, exist_ok=True)
    production = os.environ.get("TRIAGELOOP_ENV", "development") == "production"
    tz = os.environ.get("TRIAGELOOP_TIMEZONE", "Asia/Kolkata")

    db_path = os.environ.get("TRIAGELOOP_DATABASE") or os.path.join(instance, "triageloop.db")
    if not os.path.isabs(db_path):
        db_path = os.path.join(BASE_DIR, db_path)   # relative paths are relative to the project
    app.config.update(
        DATABASE=db_path,
        TIMEZONE=tz if valid_timezone(tz) else "UTC",
        PRODUCTION=production,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production,
        SESSION_COOKIE_NAME="triageloop_session",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=64 * 1024,
        JSON_SORT_KEYS=False,
        LOGIN_MAX_FAILURES=5,
        LOGIN_WINDOW_SECONDS=300,
    )
    if test_config:
        app.config.update(test_config)
    if "SECRET_KEY" not in (test_config or {}):
        app.config["SECRET_KEY"] = _secret_key(instance, production)

    # Behind a hosting proxy (Render, nginx...) trust one hop of X-Forwarded-* headers
    # so redirects, client addresses (login rate limiting) and HTTPS detection are right.
    if os.environ.get("TRIAGELOOP_BEHIND_PROXY") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.extensions["store"] = Store(app.config["DATABASE"])
    app.extensions["store"].migrate()

    from . import api, auth, pages, security
    security.init_app(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(pages.bp)
    app.register_blueprint(api.bp)

    from .cli import register
    register(app)

    @app.teardown_appcontext
    def _close(_exc):
        conn = g.pop("conn", None)
        if conn is not None:
            conn.close()

    @app.errorhandler(DomainError)
    def _domain_error(e: DomainError):
        return jsonify(error={"code": e.code, "message": e.message, "details": e.details}), e.status

    @app.errorhandler(404)
    def _not_found(_e):
        if request.path.startswith("/api/"):
            return jsonify(error={"code": "not_found", "message": "Not found", "details": {}}), 404
        return pages.not_found()

    @app.errorhandler(405)
    def _method(_e):
        return jsonify(error={"code": "method_not_allowed", "message": "Method not allowed",
                              "details": {}}), 405

    @app.errorhandler(413)
    def _too_large(_e):
        return jsonify(error={"code": "too_large", "message": "Request body too large",
                              "details": {}}), 413

    @app.errorhandler(500)
    def _server_error(e):
        log.exception("Unhandled error: %s", e)
        return jsonify(error={"code": "server_error",
                              "message": "Something went wrong. The change was not saved.",
                              "details": {}}), 500

    return app


def get_conn():
    if "conn" not in g:
        from flask import current_app
        g.conn = current_app.extensions["store"].connect()
    return g.conn
