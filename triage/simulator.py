"""Opt-in demo scenario. Never runs automatically.

``load_demo`` creates a new run flagged synthetic and replays a fixed script
through the normal command path, so demo data obeys the same validation and
produces the same events as real data entry. Every event it writes is marked
synthetic and the UI labels the run as demo data.
"""
from __future__ import annotations

from datetime import datetime

from . import commands as cmd
from .scheduler import settle
from .store import Store
from .timefmt import local_now

# Vital-sign sets chosen to produce specific NEWS2 totals (RCP 2017 chart, scale 1).
NORMAL = {"rr": 16, "spo2": 97, "on_oxygen": False, "sbp": 122, "hr": 78, "avpu": "A", "temp": 37.0}


def v(**changes) -> dict:
    return {**NORMAL, **changes}


# (minute from 09:30, action, arguments)
SCRIPT = [
    (0, "arrive", dict(pid="P04", esi=3, complaint="Abdominal pain", needs=["BEDS"], vitals=v(hr=95))),
    (2, "arrive", dict(pid="P10", esi=2, complaint="Allergic reaction", needs=["BEDS"], vitals=v(hr=98, rr=21))),
    (3, "arrive", dict(pid="P12", esi=3, complaint="Dizziness", needs=["BEDS"], vitals=v())),
    (4, "arrive", dict(pid="P13", esi=4, complaint="Laceration", needs=["BEDS"], vitals=v())),
    (5, "status", dict(pid="P04", to="in_treatment", note="Moved to treatment bay")),
    (6, "status", dict(pid="P10", to="in_treatment", note="Moved to treatment bay")),
    (7, "arrive", dict(pid="P11", esi=2, complaint="Shortness of breath", needs=["CT"], vitals=v(rr=22, hr=95))),
    (8, "status", dict(pid="P12", to="in_treatment", note="Moved to treatment bay")),
    (9, "status", dict(pid="P13", to="in_treatment", note="Wound care")),
    (12, "arrive", dict(pid="P01", esi=3, complaint="Headache", needs=["CT"], vitals=v(hr=95))),
    (24, "arrive", dict(pid="P03", esi=3, complaint="Chest pain", needs=["CT", "LAB"], vitals=v(hr=95, temp=38.5))),
    (30, "arrive", dict(pid="P14", esi=3, complaint="Fever", needs=["LAB"], vitals=v(hr=95, temp=38.4))),
    (36, "arrive", dict(pid="P02", esi=4, complaint="Back pain", needs=[], vitals=v())),
    (40, "arrive", dict(pid="P05", esi=2, complaint="Abdominal pain", needs=["CT"], vitals=v(hr=95))),
    (46, "arrive", dict(pid="P06", esi=4, complaint="Laceration", needs=["BEDS"], vitals=v())),
    (50, "vitals", dict(pid="P11", vitals=v(rr=26, spo2=91, hr=95))),        # NEWS2 7
    (54, "arrive", dict(pid="P08", esi=4, complaint="Minor injury", needs=[], vitals=v(hr=92))),
    (58, "arrive", dict(pid="P09", esi=5, complaint="Prescription refill", needs=[], vitals=v())),
    (60, "vitals", dict(pid="P14", vitals=v(rr=22, hr=95, temp=39.1))),      # NEWS2 5
    (62, "vitals", dict(pid="P05", vitals=v(rr=22, hr=112))),                # NEWS2 4
    (65, "review", dict(pid="P05", note="Troponin negative; continue observation")),
    (68, "arrive", dict(pid="P07", esi=3, complaint="Abdominal pain", needs=["CT"], vitals=v(hr=93))),
    (70, "outage", dict(resource="CT", minutes=120, reason="Scheduled maintenance (simulated)")),
    (72, "vitals", dict(pid="P03", vitals=v(rr=22, spo2=92, hr=112))),       # NEWS2 6
]


def _goto(ctx: cmd.Ctx, minute: int) -> None:
    """Move the scripted clock forward. Every scripted event carries its own
    minute, so replay reaches the same time without per-step clock events."""
    if minute > ctx.st.now:
        settle(ctx.st, ctx.st.now, minute, ctx.emit)
        ctx.st.now = minute


def load_demo(store: Store, conn, actor, timezone: str) -> int:
    today = local_now(timezone)
    start = datetime(today.year, today.month, today.day, 9, 30).isoformat(timespec="minutes")
    run_id = cmd.new_run(store, conn, "Demo scenario", actor, timezone, synthetic=True,
                         start_local=start)
    with cmd.command(store, conn, actor, timezone=timezone) as ctx:
        for minute, action, args in SCRIPT:
            _goto(ctx, minute)
            if action == "arrive":
                cmd.arrival(ctx, **args)
            elif action == "status":
                cmd.transition(ctx, args["pid"], args["to"], note=args["note"])
            elif action == "vitals":
                cmd.record_vitals(ctx, args["pid"], args["vitals"])
            elif action == "review":
                cmd.review(ctx, args["pid"], note=args["note"])
            elif action == "outage":
                cmd.outage(ctx, **args)
    return run_id
