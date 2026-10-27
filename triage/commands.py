"""The only way state changes: validated commands that append events.

Every command runs inside one SQLite write transaction (``BEGIN IMMEDIATE``):
the run is rebuilt from its events, the command is validated against that
state, and its events are appended. Any error rolls the whole command back.
"""
from __future__ import annotations

import random
from contextlib import contextmanager
from dataclasses import dataclass

from . import config as cfg
from .errors import Conflict, DomainError, NotFound
from .projection import TRANSITIONS, State, apply, rebuild
from .rules import evaluate
from .scheduler import all_estimates, dispatch, interrupt, is_offline, settle
from .scoring import score_vitals
from .store import Store
from .timefmt import local_now

MAX_ADVANCE = 240
MAX_OUTAGE = 24 * 60
MAX_SYNTHETIC_ARRIVALS = 20
AVPU = ("A", "C", "V", "P", "U")
VITAL_BOUNDS = {"rr": (0, 80), "spo2": (50, 100), "sbp": (40, 300), "hr": (20, 250),
                "temp": (30.0, 45.0)}


class DuplicateRequest(Exception):
    """The request's idempotency key was already used; nothing was applied."""


@dataclass
class Ctx:
    conn: object
    st: State
    actor: str | None

    def emit(self, type, minute, entity_type=None, entity_id=None, data=None,
             synthetic=False) -> dict:
        ev = Store.insert_event(self.conn, type=type, run_id=self.st.run_id, sim_minute=minute,
                                actor=self.actor, entity_type=entity_type, entity_id=entity_id,
                                synthetic=synthetic or self.st.synthetic, data=data or {})
        apply(self.st, ev)
        return ev


@contextmanager
def command(store: Store, conn, actor: str | None, *, key: str | None = None,
            endpoint: str = "", timezone: str = "UTC"):
    """Open a transaction on the active run and yield a Ctx for one command."""
    with store.write(conn):
        if key is not None and not store.claim_key(conn, key, endpoint):
            raise DuplicateRequest()
        run_id = store.active_run_id(conn) or _create_run(conn, "Simulation run 1", False,
                                                          actor, timezone)
        st = rebuild(store.run_events(conn, run_id))
        yield Ctx(conn, st, actor)


# ---- runs -------------------------------------------------------------------

def _create_run(conn, name: str, synthetic: bool, actor, timezone: str, start_local=None) -> int:
    run_id = Store.create_run(conn, name, synthetic, actor)
    start = start_local or local_now(timezone).isoformat(timespec="minutes")
    Store.insert_event(conn, type="run_started", run_id=run_id, sim_minute=0, actor=actor,
                       entity_type="run", entity_id=str(run_id), synthetic=synthetic,
                       data={"name": name, "synthetic": synthetic, "start_local": start,
                             "timezone": timezone, "config": cfg.default_config()})
    Store.insert_event(conn, type="run_created", actor=actor, entity_type="run",
                       entity_id=str(run_id), data={"detail": name})
    Store.set_active_run(conn, run_id)
    return run_id


def new_run(store: Store, conn, name: str, actor, timezone: str, synthetic=False,
            start_local=None) -> int:
    name = (name or "").strip()
    if not 1 <= len(name) <= 80:
        raise DomainError("Run name must be 1–80 characters")
    with store.write(conn):
        return _create_run(conn, name, synthetic, actor, timezone, start_local)


def activate_run(store: Store, conn, run_id: int, actor) -> None:
    with store.write(conn):
        if store.get_run(conn, run_id) is None:
            raise NotFound("Run not found")
        store.set_active_run(conn, run_id)
        Store.insert_event(conn, type="run_activated", actor=actor, entity_type="run",
                           entity_id=str(run_id), data={"detail": f"Run {run_id}"})


# ---- helpers ------------------------------------------------------------------

def _int(name, value, lo, hi) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise DomainError(f"{name} must be a whole number")
    try:
        n = int(value)
    except ValueError:
        raise DomainError(f"{name} must be a whole number") from None
    if not lo <= n <= hi:
        raise DomainError(f"{name} must be between {lo} and {hi}")
    return n


def _text(name, value, lo, hi, required=True) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise DomainError(f"{name} must be text")
    value = " ".join(value.split())
    if not value and not required:
        return ""
    if not lo <= len(value) <= hi:
        raise DomainError(f"{name} must be {lo}–{hi} characters")
    return value


def validate_vitals(raw, require_any=True) -> dict:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise DomainError("vitals must be an object")
    unknown = set(raw) - set(VITAL_BOUNDS) - {"on_oxygen", "avpu"}
    if unknown:
        raise DomainError(f"Unknown vital sign: {', '.join(sorted(unknown))}")
    out = {}
    for k, (lo, hi) in VITAL_BOUNDS.items():
        v = raw.get(k)
        if v is None or v == "":
            continue
        if isinstance(v, bool):
            raise DomainError(f"{k} must be a number")
        try:
            num = float(v)
        except (TypeError, ValueError):
            raise DomainError(f"{k} must be a number") from None
        if not lo <= num <= hi:
            raise DomainError(f"{k} must be between {lo} and {hi}")
        out[k] = round(num, 1) if k == "temp" else int(round(num))
    if raw.get("on_oxygen") is not None:
        if not isinstance(raw["on_oxygen"], bool):
            raise DomainError("on_oxygen must be true or false")
        out["on_oxygen"] = raw["on_oxygen"]
    if raw.get("avpu") not in (None, ""):
        a = str(raw["avpu"]).strip().upper()
        if a not in AVPU:
            raise DomainError("avpu must be one of A, C, V, P, U")
        out["avpu"] = a
    if require_any and not out:
        raise DomainError("Record at least one vital sign")
    return out


def vitals_summary(v: dict) -> str:
    names = {"rr": "RR", "spo2": "SpO₂", "sbp": "SBP", "hr": "HR", "temp": "Temp",
             "avpu": "AVPU", "on_oxygen": "O₂"}
    parts = []
    for k in ("rr", "spo2", "hr", "sbp", "temp", "avpu", "on_oxygen"):
        if k in v:
            val = ("yes" if v[k] else "no") if k == "on_oxygen" else v[k]
            parts.append(f"{names[k]} {val}{' °C' if k == 'temp' else ''}")
    return ", ".join(parts) + f" · NEWS2 {score_vitals(v).total}"


def _patient(ctx: Ctx, pid) -> object:
    if not isinstance(pid, str) or not pid.strip():
        raise DomainError("Patient id is required")
    p = ctx.st.patients.get(pid.strip().upper())
    if p is None:
        raise NotFound(f"Patient {pid} not found")
    return p


def _check_version(p, version) -> None:
    if version is None:
        return
    if isinstance(version, bool) or not isinstance(version, int):
        raise DomainError("version must be a whole number")
    if version != p.version:
        raise Conflict(f"{p.pid} was updated by someone else. Review the latest details and try again.",
                       {"current_version": p.version})


def _resource(code) -> str:
    code = str(code or "").upper()
    if code not in cfg.RESOURCES:
        raise DomainError(f"resource must be one of {', '.join(cfg.RESOURCES)}")
    return code


# ---- commands -------------------------------------------------------------------

def advance(ctx: Ctx, minutes) -> dict:
    minutes = _int("minutes", minutes, 1, MAX_ADVANCE)
    st = ctx.st
    start, end = st.now, st.now + minutes
    settle(st, start, end, ctx.emit)
    drifted = []
    if st.config.get("vitals_drift"):
        drifted = _drift(ctx, end)
    ctx.emit("clock_advanced", end, "clock", None, {"from": start, "to": end})
    dispatch(st, end, ctx.emit)
    return {"message": f"Clock advanced {minutes} min", "drifted": drifted}


def _drift(ctx: Ctx, minute: int) -> list:
    """Seeded synthetic vitals drift for up to three waiting patients."""
    st = ctx.st
    candidates = sorted((p for p in st.patients.values() if p.status == "waiting" and p.latest),
                        key=lambda p: p.pid)
    if not candidates:
        return []
    rng = random.Random(f"{st.run_id}:{st.last_event_id}:{minute}")
    changed = []
    for p in rng.sample(candidates, k=min(3, len(candidates))):
        step = rng.choice([-1, 0, 1, 1, 2])
        if step == 0:
            continue
        v = dict(p.latest.vitals)
        v["rr"] = max(12, min(35, v.get("rr", 16) + 3 * step))
        v["hr"] = max(60, min(150, v.get("hr", 80) + 8 * step))
        v["spo2"] = max(85, min(99, v.get("spo2", 97) - 2 * step))
        before = p.news2
        ctx.emit("vitals_recorded", minute, "patient", p.pid,
                 {"vitals": v, "source": "simulated", "summary": vitals_summary(v)},
                 synthetic=True)
        changed.append({"pid": p.pid, "from": before, "to": p.news2})
    return changed


def outage(ctx: Ctx, resource, minutes, reason="") -> dict:
    r = _resource(resource)
    minutes = _int("minutes", minutes, 1, MAX_OUTAGE)
    reason = _text("reason", reason, 0, 120, required=False)
    st, now = ctx.st, ctx.st.now
    end = now + minutes
    label = cfg.RESOURCE_LABELS[r]
    current = st.outages.get(r) if is_offline(st, r) else None
    if current is not None:
        if end <= current.end:
            return {"message": f"{label} is already offline for {current.end - now} more min; "
                               "outage unchanged", "changed": False}
        added = end - current.end
        ctx.emit("outage_extended", now, "resource", r,
                 {"resource": r, "end": end, "added": added, "reason": reason})
        return {"message": f"{label} outage extended by {added} min", "changed": True}
    interrupt(st, r, now, ctx.emit)
    ctx.emit("outage_started", now, "resource", r,
             {"resource": r, "end": end, "minutes": minutes, "reason": reason})
    return {"message": f"{label} offline for {minutes} min", "changed": True}


def end_outage(ctx: Ctx, resource) -> dict:
    r = _resource(resource)
    if not is_offline(ctx.st, r):
        raise Conflict(f"{cfg.RESOURCE_LABELS[r]} is not offline")
    ctx.emit("outage_ended", ctx.st.now, "resource", r, {"resource": r, "early": True})
    dispatch(ctx.st, ctx.st.now, ctx.emit)
    return {"message": f"{cfg.RESOURCE_LABELS[r]} back online"}


def arrival(ctx: Ctx, esi, complaint, needs=None, vitals=None, synthetic=False,
            pid: str | None = None) -> dict:
    """Register an arrival. ``pid`` is for the scripted demo only, never the API."""
    esi = _int("esi", esi, 1, 5)
    complaint = _text("complaint", complaint, 2, 80)
    if needs is None:
        needs = []
    if not isinstance(needs, list):
        raise DomainError("needs must be a list")
    needs = sorted({_resource(n) for n in needs}, key=cfg.RESOURCES.index)
    vitals = validate_vitals(vitals, require_any=False)
    if pid is None:
        pid = ctx.st.next_pid()
    elif pid in ctx.st.patients:
        raise Conflict(f"{pid} already exists")
    data = {"esi": esi, "complaint": complaint, "needs": needs, "vitals": vitals or None}
    if vitals:
        data["summary"] = vitals_summary(vitals)
    ctx.emit("patient_arrived", ctx.st.now, "patient", pid, data, synthetic=synthetic)
    dispatch(ctx.st, ctx.st.now, ctx.emit)
    return {"message": f"{pid} added to the queue", "pid": pid}


SYNTH_COMPLAINTS = [
    ("Chest pain", 2, ["CT", "LAB"]), ("Abdominal pain", 3, ["CT"]), ("Minor injury", 4, []),
    ("Shortness of breath", 2, ["CT"]), ("Fever", 3, ["LAB"]), ("Headache", 3, ["CT"]),
    ("Laceration", 4, ["BEDS"]), ("Dizziness", 3, ["LAB"]), ("Back pain", 4, []),
    ("Allergic reaction", 2, ["BEDS"]), ("Sprained ankle", 5, []), ("Head injury", 3, ["CT"]),
]


def synthetic_arrivals(ctx: Ctx, count=None) -> dict:
    surge = ctx.st.config["scenario"] == "Surge"
    count = _int("count", count if count is not None else (10 if surge else 5), 1,
                 MAX_SYNTHETIC_ARRIVALS)
    rng = random.Random(f"arrivals:{ctx.st.run_id}:{ctx.st.last_event_id}")
    pool = [c for c in SYNTH_COMPLAINTS if c[1] <= 3] if surge else SYNTH_COMPLAINTS
    pids = []
    for _ in range(count):
        complaint, esi, needs = rng.choice(pool)
        sick = surge and rng.random() < 0.4
        vitals = {"rr": rng.randint(18, 23) if sick else rng.randint(13, 19),
                  "spo2": rng.randint(93, 96) if sick else rng.randint(96, 99),
                  "on_oxygen": False,
                  "sbp": rng.randint(105, 125) if sick else rng.randint(112, 138),
                  "hr": rng.randint(95, 115) if sick else rng.randint(68, 92),
                  "avpu": "A", "temp": round(rng.uniform(37.0, 38.6) if sick
                                             else rng.uniform(36.4, 37.6), 1)}
        pids.append(arrival(ctx, esi, complaint, needs, vitals, synthetic=True)["pid"])
    return {"message": f"Added {count} synthetic arrivals", "pids": pids}


def record_vitals(ctx: Ctx, pid, vitals, version=None) -> dict:
    p = _patient(ctx, pid)
    _check_version(p, version)
    if not p.active:
        raise Conflict(f"{p.pid} is {p.status}; vitals cannot be recorded")
    vitals = validate_vitals(vitals)
    ctx.emit("vitals_recorded", ctx.st.now, "patient", p.pid,
             {"vitals": vitals, "source": "manual", "summary": vitals_summary(vitals)})
    dispatch(ctx.st, ctx.st.now, ctx.emit)
    return {"message": f"Vitals recorded for {p.pid} (NEWS2 {p.news2})"}


def review(ctx: Ctx, pid, version=None, note="", esi=None) -> dict:
    p = _patient(ctx, pid)
    _check_version(p, version)
    if not p.active:
        raise Conflict(f"{p.pid} is {p.status}; it no longer needs review")
    note = _text("note", note, 0, 300, required=False) or "Reviewed by clinician"
    ev = evaluate(ctx.st, p, all_estimates(ctx.st))
    data = {"note": note, "news2": p.news2, "level_at_review": ev["level"],
            "acknowledged": [r["rule"] for r in ev["reasons"]]}
    if esi is not None:
        new = _int("esi", esi, 1, 5)
        if new != p.esi:
            data.update(esi_from=p.esi, esi_to=new)
    ctx.emit("clinician_reviewed", ctx.st.now, "patient", p.pid, data)
    dispatch(ctx.st, ctx.st.now, ctx.emit)
    return {"message": f"Review recorded for {p.pid}"}


def transition(ctx: Ctx, pid, to, version=None, note="") -> dict:
    p = _patient(ctx, pid)
    _check_version(p, version)
    if to not in TRANSITIONS:
        raise DomainError("Unknown status")
    if to not in TRANSITIONS[p.status]:
        raise Conflict(f"{p.pid} cannot move from {p.status} to {to}")
    note = _text("note", note, 0, 300, required=False)
    st, now = ctx.st, ctx.st.now
    ctx.emit("patient_status_changed", now, "patient", p.pid,
             {"from": p.status, "to": to, "note": note})
    if to in ("admitted", "discharged"):
        for j in st.patient_jobs(p.pid):
            if j.state == "active":
                ctx.emit("job_completed", now, "patient", p.pid,
                         {"job_id": j.jid, "resource": j.resource, "reason": "released"})
            elif j.state == "requested":
                ctx.emit("job_cancelled", now, "patient", p.pid,
                         {"job_id": j.jid, "resource": j.resource})
    dispatch(st, now, ctx.emit)
    return {"message": f"{p.pid} moved to {to.replace('_', ' ')}"}


def update_settings(ctx: Ctx, changes) -> dict:
    try:
        clean = cfg.validate_changes(ctx.st.config, changes)
    except ValueError as e:
        raise DomainError(str(e)) from None
    previous = {k: ctx.st.config[k] for k in clean}
    effective = {k: v for k, v in clean.items()
                 if (cfg.merge(ctx.st.config, {k: v})[k] != ctx.st.config[k])}
    if not effective:
        return {"message": "No changes", "changed": False}
    ctx.emit("settings_changed", ctx.st.now, "config", None,
             {"changes": effective, "previous": previous})
    dispatch(ctx.st, ctx.st.now, ctx.emit)
    return {"message": "Settings saved", "changed": True}


def ensure_run(store: Store, conn, actor, timezone: str) -> int:
    """Return the active run id, creating an empty run on a clean install."""
    run_id = store.active_run_id(conn)
    if run_id is not None:
        return run_id
    with store.write(conn):
        run_id = store.active_run_id(conn)
        if run_id is None:
            run_id = _create_run(conn, "Simulation run 1", False, actor, timezone)
        return run_id
