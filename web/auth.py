"""Sign-in, sign-out and first-run administrator setup."""
from __future__ import annotations

import re

from flask import (Blueprint, current_app, g, redirect, render_template, request, session,
                   url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from triage.store import Store

from . import get_conn
from .security import audit, check_csrf, csrf_token, limiter

bp = Blueprint("auth", __name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,40}$")
MIN_PASSWORD = 10
# Used to keep sign-in timing similar whether or not the username exists.
_DUMMY_HASH = generate_password_hash("timing-equaliser-not-a-password")


def validate_new_user(username: str, password: str, role: str) -> str | None:
    if not USERNAME_RE.match(username or ""):
        return "Username must be 3–40 letters, digits, dots, dashes or underscores."
    if len(password or "") < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    if role not in ("clinician", "admin"):
        return "Role must be clinician or admin."
    return None


@bp.route("/login", methods=["GET", "POST"])
def login():
    conn = get_conn()
    if Store.count_users(conn) == 0:
        return redirect(url_for("auth.setup"))
    if g.user is not None:
        return redirect(url_for("pages.index"))
    error, status = None, 200
    username = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        ip = request.remote_addr or "?"
        cfg = current_app.config
        if not check_csrf(request.form.get("csrf_token")):
            error, status = "Your session expired. Please try again.", 400
        elif limiter.blocked(ip, username, cfg["LOGIN_MAX_FAILURES"], cfg["LOGIN_WINDOW_SECONDS"]):
            error, status = "Too many failed attempts. Wait a few minutes and try again.", 429
        else:
            row = Store.user_by_name(conn, username) if username else None
            ok = check_password_hash(row["password_hash"] if row else _DUMMY_HASH, password)
            if row is not None and ok and row["active"]:
                limiter.reset(ip, username)
                session.clear()
                session.permanent = True
                session["uid"] = row["id"]
                csrf_token()
                audit("login", actor=row["username"], entity_id=str(row["id"]))
                return redirect(url_for("pages.index"))
            limiter.fail(ip, username)
            audit("login_failed", detail=f"username={username[:40]}", actor=None)
            error, status = "Incorrect username or password.", 401
    return render_template("login.html", error=error, username=username,
                           csrf=csrf_token(), mode="login"), status


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """Create the first administrator. Only available while no users exist."""
    conn = get_conn()
    if Store.count_users(conn) > 0:
        return redirect(url_for("auth.login"))
    error, status, username = None, 200, ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        if not check_csrf(request.form.get("csrf_token")):
            error, status = "Your session expired. Please try again.", 400
        elif password != confirm:
            error, status = "Passwords do not match.", 400
        else:
            error = validate_new_user(username, password, "admin")
            status = 400 if error else 200
        if not error:
            with Store.write(conn):
                if Store.count_users(conn) > 0:  # lost a race with another setup request
                    return redirect(url_for("auth.login"))
                uid = Store.create_user(conn, username, generate_password_hash(password), "admin")
            audit("user_created", detail=f"{username} (admin, first-run setup)", actor=username,
                  entity_id=str(uid))
            session.clear()
            session.permanent = True
            session["uid"] = uid
            csrf_token()
            audit("login", actor=username, entity_id=str(uid))
            return redirect(url_for("pages.index"))
    return render_template("login.html", error=error, username=username, csrf=csrf_token(),
                           mode="setup"), status


@bp.post("/logout")
def logout():
    if g.user is not None and check_csrf(request.form.get("csrf_token")):
        audit("logout", entity_id=str(g.user["id"]))
        session.clear()
    return redirect(url_for("auth.login"))
