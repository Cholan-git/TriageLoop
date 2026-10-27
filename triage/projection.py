"""Rebuild the current state of a run by replaying its events in order.

Events are replayed by (sim_minute, id). Nothing here writes anything; the
projection is a pure function of the event list.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config as cfg
from .scoring import NEWS2Result, score_vitals

ACTIVE_STATUSES = ("waiting", "in_treatment")
STATUSES = ("waiting", "in_treatment", "admitted", "discharged")

# Allowed clinician lifecycle transitions.
TRANSITIONS = {
    "waiting": ("in_treatment", "discharged"),
    "in_treatment": ("admitted", "discharged"),
    "admitted": (),
    "discharged": (),
}


@dataclass
class Observation:
    minute: int
    vitals: dict
    score: NEWS2Result
    source: str            # manual | simulated | arrival
    event_id: int


@dataclass
class Patient:
    pid: str
    esi: int
    complaint: str
    needs: list
    arrival: int
    synthetic: bool
    status: str = "waiting"
    observations: list = field(default_factory=list)
    baseline_news2: int | None = None   # NEWS2 at arrival or at the last clinician review
    last_review: int | None = None
    review_count: int = 0
    version: int = 0                    # id of the latest event that touched this patient

    @property
    def latest(self) -> Observation | None:
        return self.observations[-1] if self.observations else None

    @property
    def news2(self) -> int | None:
        return self.latest.score.total if self.latest else None

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES


@dataclass
class Job:
    """One patient's use of one resource (CT scan, bed, lab work-up)."""
    jid: str
    pid: str
    resource: str
    requested_at: int
    state: str = "requested"           # requested | active | done | cancelled
    started_at: int | None = None
    expected_end: int | None = None
    ended_at: int | None = None
    interruptions: int = 0


@dataclass
class Outage:
    resource: str
    start: int
    end: int
    reason: str = ""


@dataclass
class State:
    run_id: int | None = None
    name: str = ""
    synthetic: bool = False
    config: dict = field(default_factory=cfg.default_config)
    start_local: str = "2025-01-01T00:00"
    timezone: str = "UTC"
    now: int = 0
    patients: dict = field(default_factory=dict)
    jobs: dict = field(default_factory=dict)
    outages: dict = field(default_factory=dict)           # resource -> current Outage
    outage_history: list = field(default_factory=list)
    events: list = field(default_factory=list)
    queue_series: list = field(default_factory=list)      # (minute, waiting count)
    last_event_id: int = 0
    last_recorded_at: str | None = None

    def job_for(self, pid: str, resource: str) -> Job | None:
        return self.jobs.get(job_id(pid, resource))

    def patient_jobs(self, pid: str) -> list:
        return [self.jobs[j] for j in (job_id(pid, r) for r in cfg.RESOURCES) if j in self.jobs]

    def next_pid(self) -> str:
        nums = [int(p[1:]) for p in self.patients if p[1:].isdigit()]
        return f"P{(max(nums) if nums else 0) + 1:02d}"

    def waiting_count(self) -> int:
        return sum(1 for p in self.patients.values() if p.status == "waiting")


def job_id(pid: str, resource: str) -> str:
    return f"{pid}:{resource}"


def sort_key(ev: dict) -> tuple:
    return (ev["sim_minute"], ev["id"])


def rebuild(events: list) -> State:
    st = State()
    for ev in sorted((e for e in events if e.get("sim_minute") is not None), key=sort_key):
        apply(st, ev)
    return st


def apply(st: State, ev: dict) -> None:
    """Apply one event to the state. Unknown types are kept in the history only."""
    t, m, d = ev["type"], ev["sim_minute"], ev["data"]
    st.now = max(st.now, m)
    st.last_event_id = max(st.last_event_id, ev["id"])
    st.last_recorded_at = ev["recorded_at"]
    st.events.append(ev)
    handler = _HANDLERS.get(t)
    if handler:
        handler(st, ev, m, d)
    if st.queue_series and st.queue_series[-1][0] == m:
        st.queue_series[-1] = (m, st.waiting_count())
    else:
        st.queue_series.append((m, st.waiting_count()))


def _touch(st: State, ev: dict) -> Patient | None:
    p = st.patients.get(ev["entity_id"]) if ev.get("entity_type") == "patient" else None
    if p is None and "patient_id" in ev["data"]:
        p = st.patients.get(ev["data"]["patient_id"])
    if p is not None:
        p.version = ev["id"]
    return p


def _run_started(st, ev, m, d):
    st.run_id = ev["run_id"]
    st.name = d.get("name", "")
    st.synthetic = bool(d.get("synthetic"))
    st.config = cfg.merge(cfg.default_config(), d.get("config", {}))
    st.start_local = d["start_local"]
    st.timezone = d.get("timezone", "UTC")


def _settings_changed(st, ev, m, d):
    st.config = cfg.merge(st.config, d["changes"])


def _patient_arrived(st, ev, m, d):
    p = Patient(pid=ev["entity_id"], esi=int(d["esi"]), complaint=d["complaint"],
                needs=list(d.get("needs", [])), arrival=m, synthetic=bool(ev.get("synthetic")),
                version=ev["id"])
    st.patients[p.pid] = p
    if d.get("vitals"):
        _add_obs(p, m, d["vitals"], "arrival", ev["id"])
    p.baseline_news2 = p.news2
    for r in p.needs:
        st.jobs[job_id(p.pid, r)] = Job(job_id(p.pid, r), p.pid, r, requested_at=m)


def _add_obs(p: Patient, m: int, vitals: dict, source: str, eid: int) -> None:
    p.observations.append(Observation(m, dict(vitals), score_vitals(vitals), source, eid))
    if p.baseline_news2 is None:
        p.baseline_news2 = p.news2


def _vitals_recorded(st, ev, m, d):
    p = _touch(st, ev)
    if p:
        _add_obs(p, m, d["vitals"], d.get("source", "manual"), ev["id"])


def _clinician_reviewed(st, ev, m, d):
    p = _touch(st, ev)
    if p:
        p.baseline_news2 = p.news2
        p.last_review = m
        p.review_count += 1
        if d.get("esi_to"):
            p.esi = int(d["esi_to"])


def _status_changed(st, ev, m, d):
    p = _touch(st, ev)
    if p:
        p.status = d["to"]


def _job_started(st, ev, m, d):
    _touch(st, ev)
    j = st.jobs[d["job_id"]]
    j.state, j.started_at, j.expected_end = "active", m, d["expected_end"]


def _job_completed(st, ev, m, d):
    _touch(st, ev)
    j = st.jobs[d["job_id"]]
    j.state, j.ended_at = "done", m


def _job_interrupted(st, ev, m, d):
    _touch(st, ev)
    j = st.jobs[d["job_id"]]
    j.state, j.started_at, j.expected_end = "requested", None, None
    j.interruptions += 1


def _job_cancelled(st, ev, m, d):
    _touch(st, ev)
    j = st.jobs[d["job_id"]]
    j.state, j.ended_at = "cancelled", m


def _outage_started(st, ev, m, d):
    o = Outage(d["resource"], m, d["end"], d.get("reason", ""))
    st.outages[d["resource"]] = o
    st.outage_history.append(o)


def _outage_extended(st, ev, m, d):
    st.outages[d["resource"]].end = d["end"]


def _outage_ended(st, ev, m, d):
    o = st.outages.pop(d["resource"], None)
    if o:
        o.end = m


_HANDLERS = {
    "run_started": _run_started,
    "settings_changed": _settings_changed,
    "patient_arrived": _patient_arrived,
    "vitals_recorded": _vitals_recorded,
    "clinician_reviewed": _clinician_reviewed,
    "patient_status_changed": _status_changed,
    "job_started": _job_started,
    "job_completed": _job_completed,
    "job_interrupted": _job_interrupted,
    "job_cancelled": _job_cancelled,
    "outage_started": _outage_started,
    "outage_extended": _outage_extended,
    "outage_ended": _outage_ended,
}
