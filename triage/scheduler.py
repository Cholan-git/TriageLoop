"""Resource model: capacity, outages, dispatch, completion and wait estimates.

Model (documented in docs/DASHBOARD.md):
* Each resource has ``capacity`` units. A job (one patient's use of one
  resource) occupies one unit for ``duration`` minutes.
* Requested jobs start as soon as a unit is free and the resource is online,
  in clinical priority order: ESI (lower first), then NEWS2 (higher first),
  then arrival time, then patient id.
* An outage takes the whole resource offline; jobs in progress are
  interrupted and go back to the front of their priority position.
* Completions and recoveries are recorded as events at the minute they occur.
"""
from __future__ import annotations

from typing import Callable

from .config import RESOURCES
from .projection import Patient, State

Emit = Callable[..., dict]


def priority_key(p: Patient) -> tuple:
    return (p.esi, -(p.news2 or 0), p.arrival, p.pid)


def _queue(st: State, resource: str) -> list:
    jobs = [j for j in st.jobs.values()
            if j.resource == resource and j.state == "requested" and st.patients[j.pid].active]
    return sorted(jobs, key=lambda j: priority_key(st.patients[j.pid]))


def _active(st: State, resource: str) -> list:
    return [j for j in st.jobs.values() if j.resource == resource and j.state == "active"]


def is_offline(st: State, resource: str, at: int | None = None) -> bool:
    o = st.outages.get(resource)
    return o is not None and o.end > (st.now if at is None else at)


def settle(st: State, start: int, until: int, emit: Emit) -> None:
    """Process completions, recoveries and dispatch for minutes start..until."""
    t = start
    while True:
        for r in RESOURCES:
            for j in sorted(_active(st, r), key=lambda j: (j.expected_end, j.jid)):
                if j.expected_end <= t:
                    emit("job_completed", t, "patient", j.pid,
                         {"job_id": j.jid, "resource": r, "reason": "completed"})
            o = st.outages.get(r)
            if o is not None and o.end <= t:
                emit("outage_ended", t, "resource", r, {"resource": r, "early": False})
        dispatch(st, t, emit)
        upcoming = [j.expected_end for j in st.jobs.values() if j.state == "active"]
        upcoming += [o.end for o in st.outages.values()]
        upcoming = [x for x in upcoming if t < x <= until]
        if not upcoming:
            return
        t = min(upcoming)


def dispatch(st: State, t: int, emit: Emit) -> None:
    for r in RESOURCES:
        if is_offline(st, r, t):
            continue
        free = st.config["capacity"][r] - len(_active(st, r))
        for j in _queue(st, r)[:max(free, 0)]:
            emit("job_started", t, "patient", j.pid,
                 {"job_id": j.jid, "resource": r,
                  "expected_end": t + st.config["duration"][r]})


def interrupt(st: State, resource: str, t: int, emit: Emit) -> None:
    for j in sorted(_active(st, resource), key=lambda j: j.jid):
        emit("job_interrupted", t, "patient", j.pid, {"job_id": j.jid, "resource": resource})


def estimate_starts(st: State, resource: str) -> dict:
    """Estimated start minute for each requested job (None = cannot start)."""
    cap = st.config["capacity"][resource]
    queue = _queue(st, resource)
    if cap <= 0:
        return {j.jid: None for j in queue}
    available_from = st.now
    o = st.outages.get(resource)
    if o is not None and o.end > st.now:
        available_from = o.end
    ends = sorted(max(j.expected_end, available_from) for j in _active(st, resource))
    if len(ends) >= cap:
        slots = ends[len(ends) - cap:]
    else:
        slots = ends + [available_from] * (cap - len(ends))
    duration = st.config["duration"][resource]
    out = {}
    for j in queue:
        i = min(range(len(slots)), key=lambda k: slots[k])
        out[j.jid] = slots[i]
        slots[i] += duration
    return out


def all_estimates(st: State) -> dict:
    est = {}
    for r in RESOURCES:
        est.update(estimate_starts(st, r))
    return est


def resource_summary(st: State, estimates: dict) -> list:
    """Per-resource figures shared by the dashboard card, Resources page and KPIs."""
    out = []
    for r in RESOURCES:
        cap = st.config["capacity"][r]
        active = _active(st, r)
        queue = _queue(st, r)
        offline = is_offline(st, r)
        in_use = len(active)
        if offline:
            status = "Offline"
        elif cap == 0:
            status = "No capacity"
        elif in_use >= cap:
            status = "Full"
        elif in_use == 0:
            status = "Free"
        else:
            status = "In use"
        waits = [estimates[j.jid] - st.now for j in queue if estimates.get(j.jid) is not None]
        o = st.outages.get(r) if offline else None
        out.append({
            "code": r,
            "capacity": cap,
            "in_use": in_use,
            "free": 0 if offline else max(cap - in_use, 0),
            "status": status,
            "available": (not offline) and cap - in_use > 0,
            # Utilisation = units in use / configured capacity.
            "utilisation": round(100 * in_use / cap) if cap else 0,
            "waiting": len(queue),
            "max_wait_estimate": max(waits) if waits else 0,
            "outage": ({"start": o.start, "end": o.end, "remaining": o.end - st.now,
                        "reason": o.reason} if o else None),
            "active_jobs": [{"patient": j.pid, "started": j.started_at,
                             "remaining": j.expected_end - st.now}
                            for j in sorted(active, key=lambda j: j.expected_end)],
            "queue": [{"patient": j.pid, "requested": j.requested_at,
                       "estimate": (estimates[j.jid] - st.now)
                       if estimates.get(j.jid) is not None else None}
                      for j in queue],
        })
    return out
