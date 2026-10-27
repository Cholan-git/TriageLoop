"""Domain engine tests: persistence, replay, rules, resources, lifecycle, metrics.

Intent carried over from the original engine tests (tests/test_engine.py etc.):
duplicate protection, order-independent replay, every flag explained, completed
resources clear delay flags, flags never change the clinician-set level, and
configurable reassessment targets.
"""
import random
import sqlite3

import pytest

from helpers import NORMAL, Engine
from triage import commands as cmd
from triage import simulator
from triage.errors import Conflict, DomainError, NotFound
from triage.projection import rebuild
from triage.readmodel import snapshot
from triage.store import Store

NEWS7 = {**NORMAL, "rr": 26, "spo2": 91, "hr": 95}      # 3 + 3 + 1
NEWS2_2 = {**NORMAL, "hr": 95, "temp": 38.5}            # 1 + 1
NEWS2_4 = {**NORMAL, "rr": 22, "hr": 112}               # 2 + 2
NEWS2_5 = {**NORMAL, "rr": 22, "hr": 95, "temp": 39.1}  # 2 + 1 + 2


def row(engine, pid):
    return next(p for p in snapshot(engine.state())["patients"] if p["pid"] == pid)


def rules(engine, pid):
    return [r["rule"] for r in row(engine, pid)["reasons"]]


def res(engine, code):
    return next(r for r in snapshot(engine.state())["resources"] if r["code"] == code)


# ---- persistence & integrity ---------------------------------------------------------

def test_migrations_are_repeatable(engine):
    assert engine.store.migrate(engine.conn) == []


def test_events_are_append_only(engine):
    engine.arrive()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        engine.conn.execute("UPDATE events SET type = 'x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        engine.conn.execute("DELETE FROM events")


def test_state_survives_reconnect(tmp_path):
    e = Engine(str(tmp_path / "p.db"))
    pid = e.arrive(esi=2, complaint="Chest pain", needs=["CT"])
    e.conn.close()
    again = Engine(str(tmp_path / "p.db"))
    st = again.state()
    assert st.patients[pid].complaint == "Chest pain" and st.patients[pid].esi == 2


def test_failed_command_rolls_back_everything(engine):
    before = len(engine.store.run_events(engine.conn, engine.state().run_id))

    def half_done(ctx):
        cmd.arrival(ctx, 3, "Will be rolled back", [], NORMAL)
        raise DomainError("boom")

    with pytest.raises(DomainError):
        engine.do(half_done)
    assert len(engine.store.run_events(engine.conn, engine.state().run_id)) == before


def test_duplicate_request_key_is_applied_once(engine):
    with cmd.command(engine.store, engine.conn, "t", key="dup-key-0001") as ctx:
        cmd.arrival(ctx, 3, "Once", [], NORMAL)
    with pytest.raises(cmd.DuplicateRequest):
        with cmd.command(engine.store, engine.conn, "t", key="dup-key-0001") as ctx:
            cmd.arrival(ctx, 3, "Twice", [], NORMAL)
    assert len(engine.state().patients) == 1


def test_replay_is_independent_of_storage_order(engine):
    engine.arrive(needs=["CT"])
    engine.arrive(esi=2, needs=["CT", "LAB"], vitals=NEWS7)
    engine.do(cmd.advance, 20)
    engine.do(cmd.outage, "CT", 30)
    events = engine.store.run_events(engine.conn, engine.state().run_id)
    expected = snapshot(rebuild(events))
    for seed in range(5):
        shuffled = events[:]
        random.Random(seed).shuffle(shuffled)
        assert snapshot(rebuild(shuffled)) == expected


# ---- arrivals & validation --------------------------------------------------------------

def test_arrivals_get_unique_sequential_ids(engine):
    assert [engine.arrive() for _ in range(3)] == ["P01", "P02", "P03"]


@pytest.mark.parametrize("kwargs,msg", [
    (dict(esi=0, complaint="Pain"), "esi"),
    (dict(esi=6, complaint="Pain"), "esi"),
    (dict(esi="x", complaint="Pain"), "esi"),
    (dict(esi=True, complaint="Pain"), "esi"),
    (dict(esi=3, complaint="x"), "complaint"),
    (dict(esi=3, complaint="Pain", needs=["MRI"]), "resource"),
    (dict(esi=3, complaint="Pain", needs="CT"), "needs"),
    (dict(esi=3, complaint="Pain", vitals={"hr": 400}), "hr"),
    (dict(esi=3, complaint="Pain", vitals={"bp": 120}), "Unknown vital"),
    (dict(esi=3, complaint="Pain", vitals={"avpu": "Z"}), "avpu"),
    (dict(esi=3, complaint="Pain", vitals={"on_oxygen": "yes"}), "on_oxygen"),
])
def test_arrival_validation(engine, kwargs, msg):
    with pytest.raises(DomainError, match=msg):
        engine.do(cmd.arrival, **kwargs)
    assert engine.state().patients == {}


def test_clean_install_has_no_patients(engine):
    snap = snapshot(engine.state())
    assert snap["patients"] == [] and snap["kpis"]["waiting"] == 0
    assert snap["kpis"]["avg_wait"] is None and snap["kpis"]["flags"] == 0


# ---- explainable rules ------------------------------------------------------------------

def test_every_reason_is_explained_with_rule_and_text(engine):
    engine.do(cmd.outage, "CT", 300)
    pid = engine.arrive(esi=2, needs=["CT"], vitals=NEWS2_2)
    engine.do(cmd.record_vitals, pid, NEWS7)
    engine.do(cmd.advance, 70)
    reasons = row(engine, pid)["reasons"]
    assert {"R-NEWS2-HIGH", "R-NEWS2-RISE", "R-RESOURCE-DELAY", "R-REASSESS-OVERDUE",
            "R-VITALS-STALE"} <= {r["rule"] for r in reasons}
    for r in reasons:
        assert r["kind"] in ("danger", "warn", "info") and r["text"] and r["why"]
    assert row(engine, pid)["level"] == "urgent"


def test_urgent_patient_always_lists_its_trigger_after_review(engine):
    """Regression: the old dashboard showed P11 as urgent with only a CT reason."""
    pid = engine.arrive(esi=2, vitals=NEWS2_2)
    engine.do(cmd.record_vitals, pid, NEWS7)
    engine.do(cmd.review, pid)
    p = row(engine, pid)
    assert p["level"] == "urgent"
    assert "R-NEWS2-HIGH" in p["urgent_rules"]
    assert any(r["rule"] == "R-NEWS2-HIGH" for r in p["reasons"])
    assert "R-NEWS2-RISE" not in rules(engine, pid)      # baseline reset by the review


def test_news2_rise_below_urgent_threshold_is_reassess(engine):
    pid = engine.arrive(vitals=NEWS2_2)
    engine.do(cmd.record_vitals, pid, NEWS2_4)
    p = row(engine, pid)
    assert p["level"] == "reassess" and "R-NEWS2-RISE" in rules(engine, pid)
    engine.do(cmd.record_vitals, pid, NEWS2_5)
    assert row(engine, pid)["level"] == "urgent"


def test_missing_and_partial_vitals_are_explicit(engine):
    pid = engine.arrive(vitals={})
    assert "R-VITALS-MISSING" in rules(engine, pid) and row(engine, pid)["level"] == "reassess"
    engine.do(cmd.record_vitals, pid, {"hr": 80, "rr": 16})
    assert "R-NEWS2-PARTIAL" in rules(engine, pid)
    assert row(engine, pid)["level"] == "stable"         # informational note only


def test_rules_never_change_esi_or_status(engine):
    pid = engine.arrive(esi=4, vitals=NEWS7)
    engine.do(cmd.advance, 200)
    p = engine.state().patients[pid]
    assert p.esi == 4 and p.status == "waiting"


def test_review_resets_baseline_and_timer_and_can_change_esi(engine):
    pid = engine.arrive(esi=3)
    engine.do(cmd.advance, 70)
    assert "R-REASSESS-OVERDUE" in rules(engine, pid)
    engine.do(cmd.review, pid, note="Seen", esi=2)
    p = row(engine, pid)
    assert p["since_review"] == 0 and p["wait"] == 70 and p["esi"] == 2
    ev = [e for e in engine.store.run_events(engine.conn, engine.state().run_id)
          if e["type"] == "clinician_reviewed"][-1]
    assert ev["data"]["esi_from"] == 3 and ev["data"]["esi_to"] == 2


def test_reassessment_target_is_configurable(engine):
    pid = engine.arrive(esi=3)
    engine.do(cmd.advance, 70)
    assert "R-REASSESS-OVERDUE" in rules(engine, pid)
    engine.configure(reassess_target={"3": 120})
    assert "R-REASSESS-OVERDUE" not in rules(engine, pid)


def test_flag_count_matches_urgent_patients(engine):
    for v in (NEWS7, NEWS7, NORMAL):
        engine.arrive(vitals=v)
    snap = snapshot(engine.state())
    assert snap["kpis"]["flags"] == sum(p["level"] == "urgent" for p in snap["patients"]) == 2


# ---- resources ------------------------------------------------------------------------------

def test_capacity_queue_estimates_and_completion(engine):
    a = engine.arrive(esi=2, needs=["CT"])
    b = engine.arrive(esi=3, needs=["CT"])
    ct = res(engine, "CT")
    assert ct["in_use"] == 1 and ct["utilisation"] == 100 and ct["status"] == "Full"
    assert ct["queue"] == [{"patient": b, "requested": 0, "estimate": 30}]
    assert "R-RESOURCE-DELAY" in rules(engine, b) and "R-RESOURCE-DELAY" not in rules(engine, a)
    engine.do(cmd.advance, 30)
    st = engine.state()
    assert st.job_for(a, "CT").state == "done" and st.job_for(a, "CT").ended_at == 30
    assert st.job_for(b, "CT").state == "active" and st.job_for(b, "CT").started_at == 30
    engine.do(cmd.advance, 40)
    assert res(engine, "CT")["status"] == "Free" and res(engine, "CT")["utilisation"] == 0


def test_dispatch_order_follows_clinical_priority(engine):
    engine.arrive(esi=3, needs=["CT"])                      # takes the unit
    low = engine.arrive(esi=4, needs=["CT"])
    high = engine.arrive(esi=2, needs=["CT"])
    assert [q["patient"] for q in res(engine, "CT")["queue"]] == [high, low]


def test_outage_interrupts_and_recovers_at_the_right_minute(engine):
    a = engine.arrive(needs=["CT"])
    engine.do(cmd.outage, "CT", 60)
    ct = res(engine, "CT")
    assert ct["status"] == "Offline" and ct["in_use"] == 0 and ct["outage"]["remaining"] == 60
    assert any(r["text"].startswith("CT unavailable ~60") for r in row(engine, a)["reasons"])
    engine.do(cmd.advance, 100)
    evs = engine.store.run_events(engine.conn, engine.state().run_id)
    ended = [e for e in evs if e["type"] == "outage_ended"]
    assert len(ended) == 1 and ended[0]["sim_minute"] == 60
    started = [e for e in evs if e["type"] == "job_started"]
    assert started[-1]["sim_minute"] == 60
    assert engine.state().job_for(a, "CT").state == "done"


def test_overlapping_outage_never_shortens(engine):
    """Regression: 'CT down 60' used to cut a 120-minute outage to 60."""
    engine.do(cmd.outage, "CT", 120)
    result = engine.do(cmd.outage, "CT", 60)
    assert result["changed"] is False and res(engine, "CT")["outage"]["remaining"] == 120
    engine.do(cmd.outage, "CT", 180)
    assert res(engine, "CT")["outage"]["remaining"] == 180


def test_end_outage_early(engine):
    engine.do(cmd.outage, "LAB", 60)
    engine.do(cmd.end_outage, "LAB")
    assert res(engine, "LAB")["status"] == "Free"
    with pytest.raises(Conflict):
        engine.do(cmd.end_outage, "LAB")


def test_zero_capacity_is_explained(engine):
    engine.configure(capacity={"LAB": 0})
    pid = engine.arrive(needs=["LAB"])
    assert res(engine, "LAB")["status"] == "No capacity"
    assert any("no capacity" in r["text"] for r in row(engine, pid)["reasons"])


def test_resources_available_excludes_full_and_offline(engine):
    engine.arrive(needs=["CT"])            # CT full
    engine.do(cmd.outage, "LAB", 30)       # LAB offline
    k = snapshot(engine.state())["kpis"]
    assert (k["resources_free"], k["resources_total"]) == (1, 3)


# ---- lifecycle -------------------------------------------------------------------------------

def test_lifecycle_transitions_and_release(engine):
    a = engine.arrive(needs=["CT"])
    b = engine.arrive(needs=["CT"])
    engine.do(cmd.transition, a, "in_treatment")
    with pytest.raises(Conflict):
        engine.do(cmd.transition, a, "waiting")
    engine.do(cmd.transition, a, "admitted")
    st = engine.state()
    assert st.job_for(a, "CT").state == "done"          # unit released
    assert st.job_for(b, "CT").state == "active"        # next patient dispatched
    engine.do(cmd.transition, b, "discharged")
    k = snapshot(engine.state())["kpis"]
    assert k["waiting"] == 0 and k["closed"] == 2
    with pytest.raises(Conflict):
        engine.do(cmd.review, a)


def test_discharge_cancels_pending_requests(engine):
    engine.arrive(needs=["CT"])
    b = engine.arrive(needs=["CT"])
    engine.do(cmd.transition, b, "discharged")
    assert engine.state().job_for(b, "CT").state == "cancelled"


def test_stale_version_is_rejected(engine):
    pid = engine.arrive()
    v = engine.state().patients[pid].version
    engine.do(cmd.record_vitals, pid, NEWS2_2, v)
    with pytest.raises(Conflict):
        engine.do(cmd.review, pid, v)


def test_unknown_patient(engine):
    with pytest.raises(NotFound):
        engine.do(cmd.review, "P99")


# ---- clock & metrics ------------------------------------------------------------------------

def test_advance_bounds_and_wait_metrics(engine):
    for bad in (0, 241, "ten", True):
        with pytest.raises(DomainError):
            engine.do(cmd.advance, bad)
    engine.arrive()
    engine.do(cmd.advance, 20)
    engine.arrive()
    engine.do(cmd.advance, 10)
    k = snapshot(engine.state())["kpis"]
    assert k["waiting"] == 2 and k["avg_wait"] == 20     # (30 + 10) / 2


def test_drift_is_seeded_and_marked_synthetic(tmp_path):
    def run(path):
        e = Engine(path)
        for _ in range(4):
            e.arrive()
        e.do(cmd.advance, 10)
        evs = e.store.run_events(e.conn, e.state().run_id)
        return [(x["entity_id"], x["data"]["vitals"], x["synthetic"]) for x in evs
                if x["type"] == "vitals_recorded"]
    first, second = run(str(tmp_path / "a.db")), run(str(tmp_path / "b.db"))
    assert first == second
    assert all(s == 1 for _, _, s in first)


def test_synthetic_arrivals_and_scenario(engine):
    engine.do(cmd.synthetic_arrivals)
    assert len(engine.state().patients) == 5
    assert all(p.synthetic for p in engine.state().patients.values())
    engine.configure(scenario="Surge")
    engine.do(cmd.synthetic_arrivals)
    assert len(engine.state().patients) == 15


def test_settings_validation(engine):
    for bad in ({"capacity": {"CT": -1}}, {"capacity": {"MRI": 1}}, {"nope": 1},
                {"scenario": "Chaos"}, {"news2_urgent_rise": 9}, {"vitals_drift": "no"}, {}):
        with pytest.raises(DomainError):
            engine.do(cmd.update_settings, bad)
    assert engine.configure(capacity={"CT": 2})["changed"] is True
    assert engine.configure(capacity={"CT": 2})["changed"] is False


# ---- demo scenario ------------------------------------------------------------------------

def test_demo_is_synthetic_and_matches_reference_screen(engine):
    simulator.load_demo(engine.store, engine.conn, "tester", "UTC")
    st = engine.state()
    snap = snapshot(st)
    assert st.synthetic and snap["run"]["synthetic"]
    assert all(e["synthetic"] for e in engine.store.run_events(engine.conn, st.run_id))
    assert snap["clock"]["time"] == "10:42"
    assert snap["kpis"]["waiting"] == 10 and snap["kpis"]["flags"] == 3
    assert [p["pid"] for p in snap["patients"][:3]] == ["P11", "P03", "P14"]
    ct = res(engine, "CT")
    assert ct["status"] == "Offline" and ct["outage"]["remaining"] == 118
    # The earlier run is preserved, not deleted.
    assert len(Store.list_runs(engine.conn)) == 2
