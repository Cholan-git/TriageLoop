"""Read model: the JSON the frontend renders. All metrics are computed here,
from the projected state, so every screen shows the same numbers.

Metric definitions
  waiting          patients with status "waiting"
  active           patients with status "waiting" or "in_treatment"
  flags            active patients whose level is "urgent" (independent of UI filters)
  reassess         active patients whose level is "reassess"
  avg_wait         mean of (now - arrival) over waiting patients; None when nobody waits
  resources_free   resources that are online and have at least one free unit
  utilisation      units in use / configured capacity (per resource)
"""
from __future__ import annotations

from .config import RESOURCE_LABELS, RESOURCES, RULESET_VERSION
from .projection import TRANSITIONS, State
from .rules import LEVEL_RANK, evaluate, ranked
from .scheduler import all_estimates, resource_summary
from .timefmt import sim_clock

WAIT_BUCKETS = [(0, 15), (15, 30), (30, 60), (60, 120), (120, None)]


def evaluate_all(st: State) -> tuple[dict, dict]:
    est = all_estimates(st)
    return est, {pid: evaluate(st, p, est) for pid, p in st.patients.items()}


def patient_row(st: State, p, ev: dict) -> dict:
    return {
        "pid": p.pid, "esi": p.esi, "complaint": p.complaint, "status": p.status,
        "needs": p.needs, "news2": p.news2, "baseline": p.baseline_news2,
        "arrival": sim_clock(st, p.arrival)["time"], "arrival_minute": p.arrival,
        "wait": ev["wait"], "since_review": ev["since_review"],
        "level": ev["level"], "reasons": ev["reasons"], "urgent_rules": ev["urgent_rules"],
        "reviewed": p.last_review is not None, "review_count": p.review_count,
        "version": p.version, "synthetic": p.synthetic,
        "allowed_transitions": list(TRANSITIONS[p.status]),
        "jobs": [{"resource": j.resource, "state": j.state,
                  "remaining": (j.expected_end - st.now) if j.state == "active" else None}
                 for j in st.patient_jobs(p.pid)],
    }


def kpis(st: State, evals: dict, resources: list) -> dict:
    active = [p for p in st.patients.values() if p.active]
    waiting = [p for p in active if p.status == "waiting"]
    waits = [st.now - p.arrival for p in waiting]
    return {
        "waiting": len(waiting),
        "in_treatment": len(active) - len(waiting),
        "active": len(active),
        "flags": sum(1 for p in active if evals[p.pid]["level"] == "urgent"),
        "reassess": sum(1 for p in active if evals[p.pid]["level"] == "reassess"),
        "avg_wait": round(sum(waits) / len(waits)) if waits else None,
        "resources_free": sum(1 for r in resources if r["available"]),
        "resources_total": len(resources),
        "closed": len(st.patients) - len(active),
    }


def analytics(st: State, evals: dict, resources: list) -> dict:
    waiting = [p for p in st.patients.values() if p.status == "waiting"]
    dist = []
    for lo, hi in WAIT_BUCKETS:
        n = sum(1 for p in waiting
                if st.now - p.arrival >= lo and (hi is None or st.now - p.arrival < hi))
        dist.append({"label": f"{lo}–{hi}" if hi else f"{lo}+", "count": n})
    flagged = {pid for pid, e in evals.items() if e["level"] in ("urgent", "reassess")}
    bottlenecks = sorted(
        ({"resource": r["code"], "label": RESOURCE_LABELS[r["code"]], "waiting": r["waiting"],
          "flagged": sum(1 for q in r["queue"] if q["patient"] in flagged),
          "max_wait_estimate": r["max_wait_estimate"], "status": r["status"]}
         for r in resources),
        key=lambda b: (-b["max_wait_estimate"], -b["waiting"], b["resource"]))
    series = [{"minute": m, "time": sim_clock(st, m)["time"], "waiting": n}
              for m, n in st.queue_series]
    return {"queue_trend": series[-60:], "wait_distribution": dist, "bottlenecks": bottlenecks}


def snapshot(st: State) -> dict:
    est, evals = evaluate_all(st)
    resources = resource_summary(st, est)
    for r in resources:
        r["label"] = RESOURCE_LABELS[r["code"]]
    pats = ranked(st, evals)
    offline = [r["label"] for r in resources if r["status"] == "Offline"]
    delayed = [r["label"] for r in resources if r["status"] != "Offline" and r["max_wait_estimate"] > 0]
    if offline:
        impact = (f"{', '.join(offline)} outage may increase waiting time for patients "
                  f"requiring {'imaging' if offline == ['CT'] else 'these resources'}.")
    elif delayed:
        impact = f"Queues are forming for {', '.join(delayed)}; estimated delays are shown per patient."
    elif any(r["status"] == "No capacity" for r in resources):
        impact = "A resource has zero configured capacity."
    else:
        impact = "All resources operational."
    return {
        "run": {"id": st.run_id, "name": st.name, "synthetic": st.synthetic},
        "clock": sim_clock(st, st.now),
        "last_update": st.last_recorded_at,
        "version": st.last_event_id,
        "ruleset": RULESET_VERSION,
        "config": st.config,
        "kpis": kpis(st, evals, resources),
        "patients": [patient_row(st, p, evals[p.pid]) for p in pats],
        "resources": resources,
        "impact": impact,
        "analytics": analytics(st, evals, resources),
        "recent_events": [describe(st, e) for e in reversed(st.events[-40:])
                          if e["type"] not in ("clock_advanced", "run_started")][:6],
    }


def patient_detail(st: State, pid: str) -> dict | None:
    p = st.patients.get(pid)
    if p is None:
        return None
    est, evals = evaluate_all(st)
    row = patient_row(st, p, evals[pid])
    row["observations"] = [{
        "time": sim_clock(st, o.minute)["time"], "minute": o.minute, "source": o.source,
        "vitals": o.vitals, "news2": o.score.total, "parts": o.score.parts,
        "missing": o.score.missing} for o in reversed(p.observations)]
    row["timeline"] = [describe(st, e) for e in reversed(st.events)
                       if e.get("entity_id") == pid]
    row["jobs"] = [{"resource": j.resource, "label": RESOURCE_LABELS[j.resource],
                    "state": j.state, "requested": sim_clock(st, j.requested_at)["time"],
                    "started": sim_clock(st, j.started_at)["time"] if j.started_at is not None else None,
                    "remaining": (j.expected_end - st.now) if j.state == "active" else None,
                    "estimate": (est[j.jid] - st.now) if est.get(j.jid) is not None else None,
                    "interruptions": j.interruptions}
                   for j in st.patient_jobs(pid)]
    return row


# ---- event descriptions ---------------------------------------------------

CATEGORY = {
    "patient_arrived": "patient", "vitals_recorded": "patient", "clinician_reviewed": "review",
    "patient_status_changed": "patient", "job_started": "resource", "job_completed": "resource",
    "job_interrupted": "resource", "job_cancelled": "resource", "outage_started": "resource",
    "outage_extended": "resource", "outage_ended": "resource", "clock_advanced": "clock",
    "settings_changed": "config", "run_started": "config",
}

STATUS_LABEL = {"waiting": "Waiting", "in_treatment": "In treatment",
                "admitted": "Admitted", "discharged": "Discharged"}


def describe(st: State | None, e: dict) -> dict:
    t, d = e["type"], e["data"]
    title, detail, color = t.replace("_", " ").capitalize(), "", "blue"
    label = RESOURCE_LABELS.get(d.get("resource", ""), d.get("resource", ""))
    if t == "patient_arrived":
        title = "New patient arrival"
        detail = f"ESI Level {d['esi']} · {d['complaint']}"
    elif t == "vitals_recorded":
        title, color = "Vitals recorded", "red"
        detail = d.get("summary") or ""
        if d.get("source") == "simulated":
            detail = (detail + " · simulated").strip(" ·")
    elif t == "clinician_reviewed":
        title, color = "Clinician review", "green"
        detail = d.get("note", "")
        if d.get("esi_to"):
            detail = f"ESI {d['esi_from']} → {d['esi_to']}" + (f" · {detail}" if detail else "")
    elif t == "patient_status_changed":
        title, color = "Status changed", "green"
        detail = f"{STATUS_LABEL[d['from']]} → {STATUS_LABEL[d['to']]}"
        if d.get("note"):
            detail += f" · {d['note']}"
    elif t == "job_started":
        title, detail, color = f"{label} started", f"Expected to finish in {d['expected_end'] - e['sim_minute']} min", "blue"
    elif t == "job_completed":
        title, detail, color = f"{label} completed", d.get("reason", "").replace("_", " "), "green"
    elif t == "job_interrupted":
        title, detail, color = f"{label} interrupted", "Returned to queue (resource offline)", "orange"
    elif t == "job_cancelled":
        title, detail, color = f"{label} request cancelled", "Patient left the department", "grey"
    elif t == "outage_started":
        title, color = "Resource status changed", "orange"
        detail = f"{label} set to offline for {d['minutes']} min" + (f" · {d['reason']}" if d.get("reason") else "")
    elif t == "outage_extended":
        title, color = "Outage extended", "orange"
        detail = f"{label} offline extended by {d['added']} min"
    elif t == "outage_ended":
        title, color = "Resource recovered", "green"
        detail = f"{label} back online" + (" (ended early)" if d.get("early") else "")
    elif t == "clock_advanced":
        title, detail, color = "Clock advanced", f"+{d['to'] - d['from']} min", "grey"
    elif t == "settings_changed":
        title, color = "Settings changed", "grey"
        detail = ", ".join(sorted(d["changes"]))
    elif t == "run_started":
        title, color = "Simulation run started", "grey"
        detail = d.get("name", "") + (" · demo data (synthetic)" if d.get("synthetic") else "")
    elif t in ("login", "logout", "login_failed", "user_created", "run_created", "run_activated"):
        title = {"login": "Signed in", "logout": "Signed out", "login_failed": "Failed sign-in",
                 "user_created": "User created", "run_created": "Run created",
                 "run_activated": "Run activated"}[t]
        color = "red" if t == "login_failed" else "grey"
        detail = d.get("detail", "")
    clock = sim_clock(st, e["sim_minute"]) if st is not None and e.get("sim_minute") is not None else None
    return {
        "id": e["id"], "type": t, "category": CATEGORY.get(t, "security"),
        "time": clock["time"] if clock else None, "minute": e.get("sim_minute"),
        "recorded_at": e["recorded_at"], "actor": e.get("actor"),
        "entity_type": e.get("entity_type"), "entity_id": e.get("entity_id"),
        "tag": e.get("entity_id") or "", "title": title, "detail": detail, "color": color,
        "synthetic": bool(e.get("synthetic")),
    }


__all__ = ["snapshot", "patient_detail", "describe", "LEVEL_RANK", "RESOURCES"]
