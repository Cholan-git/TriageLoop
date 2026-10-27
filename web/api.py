"""JSON API. Every mutation goes through triage.commands in one transaction and
returns the fresh snapshot, so the UI never computes state on its own.

Error schema:   {"error": {"code": str, "message": str, "details": {}}}
Success schema: {"ok": true, "message": str, "result": {...}, "state": <snapshot>}
"""
from __future__ import annotations

import csv
import io
import re
import threading

from flask import Blueprint, Response, current_app, g, jsonify, request
from werkzeug.security import generate_password_hash

from triage import commands as cmd
from triage import simulator
from triage.config import RESOURCES
from triage.errors import DomainError, NotFound
from triage.projection import rebuild
from triage.readmodel import describe, patient_detail, snapshot
from triage.store import Store

from . import get_conn
from .auth import validate_new_user
from .security import PERMISSIONS, api_auth, audit, csrf_token

bp = Blueprint("api", __name__, url_prefix="/api")

_cache: dict = {}
_cache_lock = threading.Lock()
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,100}$")
CATEGORIES = {
    "patient": ["patient_arrived", "vitals_recorded", "patient_status_changed"],
    "review": ["clinician_reviewed"],
    "resource": ["job_started", "job_completed", "job_interrupted", "job_cancelled",
                 "outage_started", "outage_extended", "outage_ended"],
    "clock": ["clock_advanced"],
    "config": ["settings_changed", "run_started"],
    "security": ["login", "logout", "login_failed", "access_denied", "user_created",
                 "run_created", "run_activated", "audit_exported"],
}


# ---- helpers ----------------------------------------------------------------

def _store() -> Store:
    return current_app.extensions["store"]


def _tz() -> str:
    return current_app.config["TIMEZONE"]


def _actor() -> str:
    return g.user["username"]


def current_state():
    conn = get_conn()
    run_id = cmd.ensure_run(_store(), conn, _actor(), _tz())
    version = Store.run_version(conn, run_id)
    key = (current_app.config["DATABASE"], run_id)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] == version:
            return hit[1]
    st = rebuild(Store.run_events(conn, run_id))
    with _cache_lock:
        _cache[key] = (version, st)
    return st


def body() -> dict:
    if not request.is_json:
        raise DomainError("Content-Type must be application/json", 415, "unsupported_media_type")
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise DomainError("Request body must be a JSON object")
    return data


def mutate(fn, *args, **kwargs):
    key = request.headers.get("Idempotency-Key")
    if key is not None and not KEY_RE.match(key):
        raise DomainError("Idempotency-Key must be 8–100 letters, digits, '-' or '_'")
    conn = get_conn()
    try:
        with cmd.command(_store(), conn, _actor(), key=key, endpoint=request.path,
                         timezone=_tz()) as ctx:
            result = fn(ctx, *args, **kwargs)
    except cmd.DuplicateRequest:
        return jsonify(ok=True, duplicate=True, message="Already processed", result={},
                       state=snapshot(current_state()))
    message = result.pop("message", "Done")
    return jsonify(ok=True, message=message, result=result, state=snapshot(current_state()))


def _page_args():
    try:
        page = max(1, int(request.args.get("page", 1)))
        size = min(100, max(1, int(request.args.get("page_size", 25))))
    except ValueError:
        raise DomainError("page and page_size must be whole numbers") from None
    return page, size


# ---- session & state --------------------------------------------------------

@bp.get("/session")
@api_auth()
def session_info():
    return jsonify(user=g.user, permissions=sorted(PERMISSIONS[g.user["role"]]),
                   csrf=csrf_token())


@bp.get("/state")
@api_auth()
def state():
    return jsonify(snapshot(current_state()))


@bp.get("/version")
@api_auth()
def version():
    st = current_state()
    return jsonify(version=st.last_event_id, run=st.run_id)


# ---- patients -----------------------------------------------------------------

@bp.get("/patients/<pid>")
@api_auth()
def get_patient(pid):
    d = patient_detail(current_state(), pid.upper())
    if d is None:
        raise NotFound(f"Patient {pid} not found")
    return jsonify(d)


@bp.post("/patients")
@api_auth("patient.write")
def create_patient():
    b = body()
    return mutate(cmd.arrival, b.get("esi"), b.get("complaint"), b.get("needs"), b.get("vitals"))


@bp.post("/patients/<pid>/vitals")
@api_auth("patient.write")
def patient_vitals(pid):
    b = body()
    return mutate(cmd.record_vitals, pid, b.get("vitals"), b.get("version"))


@bp.post("/patients/<pid>/review")
@api_auth("patient.write")
def patient_review(pid):
    b = body()
    return mutate(cmd.review, pid, b.get("version"), b.get("note", ""), b.get("esi"))


@bp.post("/patients/<pid>/transition")
@api_auth("patient.write")
def patient_transition(pid):
    b = body()
    return mutate(cmd.transition, pid, b.get("to"), b.get("version"), b.get("note", ""))


# ---- simulation & resources ----------------------------------------------------

@bp.post("/sim/advance")
@api_auth("sim.operate")
def sim_advance():
    return mutate(cmd.advance, body().get("minutes", 10))


@bp.post("/sim/arrivals")
@api_auth("sim.operate")
def sim_arrivals():
    return mutate(cmd.synthetic_arrivals, body().get("count"))


@bp.post("/sim/scenario")
@api_auth("sim.operate")
def sim_scenario():
    return mutate(cmd.update_settings, {"scenario": body().get("scenario")})


@bp.post("/resources/<code>/outage")
@api_auth("sim.operate")
def resource_outage(code):
    b = body()
    return mutate(cmd.outage, code, b.get("minutes", 60), b.get("reason", ""))


@bp.post("/resources/<code>/recover")
@api_auth("resource.manage")
def resource_recover(code):
    body()
    return mutate(cmd.end_outage, code)


# ---- settings -------------------------------------------------------------------

@bp.get("/settings")
@api_auth()
def get_settings():
    st = current_state()
    return jsonify(config=st.config, timezone=_tz(), resources=list(RESOURCES))


@bp.put("/settings")
@api_auth("settings.write")
def put_settings():
    return mutate(cmd.update_settings, body().get("changes"))


# ---- event log --------------------------------------------------------------------

@bp.get("/events")
@api_auth()
def events():
    page, size = _page_args()
    category = request.args.get("category") or None
    if category and category not in CATEGORIES:
        raise DomainError(f"category must be one of {', '.join(CATEGORIES)}")
    include_global = request.args.get("scope") == "all"
    if include_global or category == "security":
        if "audit.global" not in PERMISSIONS[g.user["role"]]:
            raise DomainError("Your role does not allow viewing security events", 403, "forbidden")
        include_global = True
    search = (request.args.get("q") or "").strip()[:80] or None
    entity = (request.args.get("patient") or "").strip()[:20] or None
    st = current_state()
    rows, total = Store.query_events(get_conn(), st.run_id, types=CATEGORIES.get(category),
                                     entity_id=entity, search=search,
                                     include_global=include_global,
                                     limit=size, offset=(page - 1) * size)
    return jsonify(items=[describe(st if r["run_id"] == st.run_id else None, r) for r in rows],
                   total=total, page=page, page_size=size,
                   pages=max(1, -(-total // size)), categories=list(CATEGORIES))


def _csv_cell(value) -> str:
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@bp.get("/events/export.csv")
@api_auth("audit.export")
def export_events():
    st = current_state()
    rows, _ = Store.query_events(get_conn(), st.run_id, limit=100000, offset=0)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "sim_time", "recorded_at_utc", "type", "actor", "entity", "synthetic",
                "title", "detail"])
    for r in reversed(rows):
        d = describe(st, r)
        w.writerow([_csv_cell(x) for x in (d["id"], d["time"], d["recorded_at"], d["type"],
                                            d["actor"], d["entity_id"], d["synthetic"],
                                            d["title"], d["detail"])])
    audit("audit_exported", detail=f"run {st.run_id}, {len(rows)} events")
    return Response(buf.getvalue(), mimetype="text/csv", headers={
        "Content-Disposition": f'attachment; filename="triageloop-run{st.run_id}-events.csv"'})


# ---- runs ---------------------------------------------------------------------

@bp.get("/runs")
@api_auth()
def runs():
    conn = get_conn()
    return jsonify(items=Store.list_runs(conn), active=Store.active_run_id(conn))


@bp.post("/runs")
@api_auth("runs.manage")
def create_run():
    b = body()
    cmd.new_run(_store(), get_conn(), b.get("name", ""), _actor(), _tz())
    return jsonify(ok=True, message="New empty run started", state=snapshot(current_state()))


@bp.post("/runs/demo")
@api_auth("runs.manage")
def load_demo():
    body()
    simulator.load_demo(_store(), get_conn(), _actor(), _tz())
    return jsonify(ok=True, message="Demo scenario loaded (synthetic data)",
                   state=snapshot(current_state()))


@bp.post("/runs/<int:run_id>/activate")
@api_auth("runs.manage")
def activate(run_id):
    body()
    cmd.activate_run(_store(), get_conn(), run_id, _actor())
    return jsonify(ok=True, message=f"Switched to run {run_id}", state=snapshot(current_state()))


# ---- users --------------------------------------------------------------------

@bp.get("/users")
@api_auth("users.manage")
def users():
    return jsonify(items=Store.list_users(get_conn()))


@bp.post("/users")
@api_auth("users.manage")
def create_user():
    b = body()
    username = str(b.get("username", "")).strip()
    password, role = str(b.get("password", "")), str(b.get("role", ""))
    err = validate_new_user(username, password, role)
    if err:
        raise DomainError(err)
    conn = get_conn()
    with Store.write(conn):
        if Store.user_by_name(conn, username) is not None:
            raise DomainError("That username is already taken", 409, "conflict")
        uid = Store.create_user(conn, username, generate_password_hash(password), role)
    audit("user_created", detail=f"{username} ({role})", entity_id=str(uid))
    return jsonify(ok=True, message=f"User {username} created", items=Store.list_users(conn)), 201
