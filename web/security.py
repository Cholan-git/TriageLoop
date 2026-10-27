"""Authentication helpers, role permissions, CSRF and HTTP security headers."""
from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from functools import wraps

from flask import current_app, g, jsonify, redirect, request, session, url_for

from triage.errors import DomainError
from triage.store import Store

PERMISSIONS = {
    "clinician": {"view", "patient.write", "sim.operate"},
    "admin": {"view", "patient.write", "sim.operate", "resource.manage", "settings.write",
              "runs.manage", "users.manage", "audit.export", "audit.global"},
}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'none'")


def init_app(app):
    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.path.startswith("/api/") or request.path in ("/", "/login", "/setup"):
            resp.headers["Cache-Control"] = "no-store"
        if current_app.config["PRODUCTION"]:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp

    @app.before_request
    def _load_user():
        g.user = None
        uid = session.get("uid")
        if uid is not None:
            from . import get_conn
            row = Store.user_by_id(get_conn(), uid)
            if row is not None and row["active"]:
                g.user = {"id": row["id"], "username": row["username"], "role": row["role"]}
            else:
                session.clear()


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf(token: str | None) -> bool:
    expected = session.get("csrf")
    return bool(expected and token and hmac.compare_digest(expected, token))


def can(permission: str) -> bool:
    return g.user is not None and permission in PERMISSIONS[g.user["role"]]


def api_auth(permission: str = "view"):
    """Require a signed-in user with ``permission``; mutating calls also need CSRF."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if g.user is None:
                raise DomainError("Sign in required", 401, "unauthenticated")
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                if not check_csrf(request.headers.get("X-CSRF-Token")):
                    raise DomainError("Security token missing or expired. Reload the page.",
                                      403, "csrf_failed")
            if not can(permission):
                audit("access_denied", detail=f"{request.method} {request.path}")
                raise DomainError("Your role does not allow this action", 403, "forbidden")
            return fn(*args, **kwargs)
        return wrapper
    return deco


def page_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        return fn(*args, **kwargs)
    return wrapper


def audit(type_: str, detail: str = "", actor: str | None = None, entity_id: str | None = None):
    """Record a security/administrative event (not part of any run) in its own transaction."""
    from . import get_conn
    conn = get_conn()
    who = actor or (g.user["username"] if getattr(g, "user", None) else None)
    try:
        with Store.write(conn):
            Store.insert_event(conn, type=type_, actor=who, entity_type="user",
                               entity_id=entity_id, data={"detail": detail[:200],
                                                         "ip": request.remote_addr})
    except Exception:  # auditing must never break the request it describes
        current_app.logger.exception("Could not write audit event %s", type_)


class LoginLimiter:
    """In-memory failed-login limiter keyed by client address and username."""

    def __init__(self):
        self._fails = defaultdict(deque)
        self._lock = threading.Lock()

    def _key(self, ip, username):
        return f"{ip}|{(username or '').lower()}"

    def blocked(self, ip, username, limit, window) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._fails[self._key(ip, username)]
            while q and now - q[0] > window:
                q.popleft()
            return len(q) >= limit

    def fail(self, ip, username):
        with self._lock:
            self._fails[self._key(ip, username)].append(time.monotonic())

    def reset(self, ip, username):
        with self._lock:
            self._fails.pop(self._key(ip, username), None)


limiter = LoginLimiter()


def error_json(message, status, code):
    return jsonify(error={"code": code, "message": message, "details": {}}), status
