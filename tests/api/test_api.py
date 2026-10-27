"""HTTP API tests: schemas, status codes, auth, CSRF, idempotency, concurrency."""
import threading

import pytest

from helpers import NORMAL, PASSWORD, Client

NEWS7 = {**NORMAL, "rr": 26, "spo2": 91, "hr": 95}


def err(resp):
    body = resp.get_json()
    assert set(body["error"]) == {"code", "message", "details"}
    return body["error"]


def add(client, **kw):
    payload = {"esi": 3, "complaint": "Abdominal pain", "needs": ["CT"], "vitals": NORMAL, **kw}
    r = client.post("/api/patients", json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


# ---- authentication & authorisation -----------------------------------------------------

def test_api_requires_sign_in(anon):
    r = anon.get("/api/state")
    assert r.status_code == 401 and err(r)["code"] == "unauthenticated"
    assert anon.get("/").status_code == 302


def test_wrong_password_and_rate_limit(anon):
    for _ in range(5):
        assert anon.login("admin", "wrong-password").status_code == 401
    assert anon.login("admin", PASSWORD).status_code == 429


def test_unknown_user_gets_same_message(anon):
    r = anon.login("nobody", "whatever-password")
    assert r.status_code == 401 and b"Incorrect username or password" in r.data


def test_csrf_required_for_mutations(admin):
    r = admin.post("/api/sim/advance", json={"minutes": 5}, csrf=False)
    assert r.status_code == 403 and err(r)["code"] == "csrf_failed"


@pytest.mark.parametrize("method,path", [
    ("put", "/api/settings"), ("post", "/api/runs"), ("post", "/api/runs/demo"),
    ("post", "/api/users"), ("post", "/api/resources/CT/recover"),
])
def test_clinician_cannot_use_admin_endpoints(clinician, admin, method, path):
    r = getattr(clinician, method)(path, json={})
    assert r.status_code == 403 and err(r)["code"] == "forbidden"
    assert clinician.get("/api/events/export.csv").status_code == 403
    log = admin.get("/api/events?category=security&page_size=100").get_json()["items"]
    assert any(i["type"] == "access_denied" for i in log)


def test_clinician_can_operate_and_review(clinician):
    pid = add(clinician)["result"]["pid"]
    assert clinician.post("/api/sim/advance", json={"minutes": 10}).status_code == 200
    assert clinician.post(f"/api/patients/{pid}/review", json={"note": "ok"}).status_code == 200


def test_logout_clears_session(admin):
    admin.c.post("/logout", data={"csrf_token": admin.csrf})
    assert admin.get("/api/state").status_code == 401


def test_first_run_setup_creates_admin(tmp_path):
    from web import create_app
    app = create_app({"TESTING": True, "SECRET_KEY": "x", "DATABASE": str(tmp_path / "s.db")})
    c = app.test_client()
    assert c.get("/login").headers["Location"].endswith("/setup")
    c.get("/setup")
    with c.session_transaction() as s:
        token = s["csrf"]
    weak = c.post("/setup", data={"username": "boss", "password": "short", "confirm": "short",
                                  "csrf_token": token})
    assert weak.status_code == 400
    ok = c.post("/setup", data={"username": "boss", "password": PASSWORD, "confirm": PASSWORD,
                                "csrf_token": token})
    assert ok.status_code == 302
    assert c.get("/api/session").get_json()["user"]["role"] == "admin"
    assert c.get("/setup").status_code == 302           # closed once a user exists


# ---- schemas & validation -------------------------------------------------------------------

def test_state_schema(admin):
    s = admin.get("/api/state").get_json()
    assert {"run", "clock", "kpis", "patients", "resources", "impact", "analytics",
            "recent_events", "version", "ruleset", "config", "last_update"} <= set(s)
    assert s["patients"] == [] and s["kpis"]["avg_wait"] is None


def test_malformed_requests(admin):
    r = admin.c.post("/api/sim/advance", data="x", headers={"X-CSRF-Token": admin.csrf})
    assert r.status_code == 415
    r = admin.c.post("/api/sim/advance", json=[1], headers={"X-CSRF-Token": admin.csrf})
    assert r.status_code == 400
    assert admin.post("/api/sim/advance", json={"minutes": 999}).status_code == 400
    assert admin.post("/api/patients", json={"esi": 9, "complaint": "x"}).status_code == 400
    assert admin.post("/api/patients/P99/review", json={}).status_code == 404
    assert admin.post("/api/resources/MRI/outage", json={"minutes": 5}).status_code == 400
    assert admin.post("/api/sim/advance", json={}, key="bad key!").status_code == 400
    assert admin.get("/api/patients/P99").status_code == 404
    assert err(admin.get("/api/nope"))["code"] == "not_found"
    assert admin.c.delete("/api/state").status_code == 405


def test_mutation_then_read_is_consistent(admin):
    body = add(admin, vitals=NEWS7)
    pid = body["result"]["pid"]
    after = admin.get("/api/state").get_json()
    assert body["state"]["kpis"] == after["kpis"]
    assert after["kpis"]["flags"] == 1
    d = admin.get(f"/api/patients/{pid}").get_json()
    assert d["observations"][0]["news2"] == 7 and d["timeline"][0]["entity_id"] == pid


def test_idempotency_key_prevents_double_submit(admin):
    r1 = admin.post("/api/sim/arrivals", json={"count": 3}, key="same-click-001")
    r2 = admin.post("/api/sim/arrivals", json={"count": 3}, key="same-click-001")
    assert r1.status_code == r2.status_code == 200
    assert r2.get_json()["duplicate"] is True
    assert admin.get("/api/state").get_json()["kpis"]["waiting"] == 3


def test_version_conflict(admin, clinician):
    pid = add(admin)["result"]["pid"]
    v = admin.get(f"/api/patients/{pid}").get_json()["version"]
    assert clinician.post(f"/api/patients/{pid}/review", json={"version": v}).status_code == 200
    r = admin.post(f"/api/patients/{pid}/review", json={"version": v})
    assert r.status_code == 409 and err(r)["code"] == "conflict"


def test_concurrent_reviews_only_one_wins(app, admin):
    pid = add(admin)["result"]["pid"]
    v = admin.get(f"/api/patients/{pid}").get_json()["version"]
    clients = []
    for _ in range(6):
        c = Client(app.test_client())
        c.login("admin")
        clients.append(c)
    codes = []

    def go(c):
        codes.append(c.post(f"/api/patients/{pid}/review", json={"version": v}).status_code)
    threads = [threading.Thread(target=go, args=(c,)) for c in clients]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes) == [200] + [409] * 5
    assert admin.get(f"/api/patients/{pid}").get_json()["review_count"] == 1


def test_transition_endpoint(admin):
    pid = add(admin)["result"]["pid"]
    assert admin.post(f"/api/patients/{pid}/transition", json={"to": "admitted"}).status_code == 409
    r = admin.post(f"/api/patients/{pid}/transition", json={"to": "in_treatment"})
    assert r.status_code == 200
    row = next(p for p in r.get_json()["state"]["patients"] if p["pid"] == pid)
    assert row["status"] == "in_treatment" and row["allowed_transitions"] == ["admitted", "discharged"]


def test_outage_and_recover(admin):
    s = admin.post("/api/resources/CT/outage", json={"minutes": 60, "reason": "test"}).get_json()
    ct = next(r for r in s["state"]["resources"] if r["code"] == "CT")
    assert ct["status"] == "Offline"
    s = admin.post("/api/resources/CT/recover", json={}).get_json()
    assert next(r for r in s["state"]["resources"] if r["code"] == "CT")["status"] == "Free"


def test_settings_roundtrip(admin):
    r = admin.put("/api/settings", json={"changes": {"capacity": {"BEDS": 20}}})
    assert r.status_code == 200
    assert admin.get("/api/settings").get_json()["config"]["capacity"]["BEDS"] == 20
    assert admin.put("/api/settings", json={"changes": {"capacity": {"BEDS": -2}}}).status_code == 400


def test_scenario_toggle_keeps_history(admin):
    add(admin)
    before = admin.get("/api/events?page_size=100").get_json()["total"]
    r = admin.post("/api/sim/scenario", json={"scenario": "Surge"})
    assert r.get_json()["state"]["config"]["scenario"] == "Surge"
    assert r.get_json()["state"]["kpis"]["waiting"] == 1          # nothing wiped
    assert admin.get("/api/events?page_size=100").get_json()["total"] == before + 1


# ---- event log & export ------------------------------------------------------------------------

def test_event_log_filters_and_pagination(admin):
    pid = add(admin)["result"]["pid"]
    add(admin)
    admin.post("/api/sim/advance", json={"minutes": 5})
    all_ = admin.get("/api/events?page_size=2").get_json()
    assert all_["page_size"] == 2 and len(all_["items"]) == 2 and all_["pages"] >= 2
    minutes = [i["minute"] for i in admin.get("/api/events?page_size=100").get_json()["items"]]
    assert minutes == sorted(minutes, reverse=True)
    only = admin.get(f"/api/events?patient={pid}").get_json()["items"]
    assert only and all(i["entity_id"] == pid for i in only)
    clock = admin.get("/api/events?category=clock").get_json()["items"]
    assert [i["type"] for i in clock] == ["clock_advanced"]
    assert admin.get("/api/events?q=Abdominal").get_json()["total"] == 2
    assert admin.get("/api/events?category=bogus").status_code == 400


def test_security_events_are_admin_only(clinician):
    assert clinician.get("/api/events?category=security").status_code == 403


def test_csv_export(admin):
    from web.api import _csv_cell
    add(admin, complaint="Chest pain")
    r = admin.get("/api/events/export.csv")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    lines = r.get_data(as_text=True).splitlines()
    assert lines[0].startswith("id,sim_time") and any("Chest pain" in ln for ln in lines)
    assert _csv_cell("=HYPERLINK(1)") == "'=HYPERLINK(1)" and _csv_cell("ok") == "ok"


def test_audit_log_never_contains_passwords(admin, anon):
    anon.login("admin", "a-secret-guess-123")
    text = admin.get("/api/events?category=security&page_size=100").get_data(as_text=True)
    assert "a-secret-guess-123" not in text and PASSWORD not in text


# ---- runs & users -------------------------------------------------------------------------------

def test_demo_and_runs_preserve_history(admin):
    add(admin)
    first = admin.get("/api/state").get_json()["run"]["id"]
    demo = admin.post("/api/runs/demo").get_json()["state"]
    assert demo["run"]["synthetic"] is True and demo["kpis"]["flags"] == 3
    fresh = admin.post("/api/runs", json={"name": "Afternoon shift"}).get_json()["state"]
    assert fresh["patients"] == [] and fresh["run"]["name"] == "Afternoon shift"
    back = admin.post(f"/api/runs/{first}/activate").get_json()["state"]
    assert back["kpis"]["waiting"] == 1
    assert len(admin.get("/api/runs").get_json()["items"]) == 3


def test_user_management(admin):
    r = admin.post("/api/users", json={"username": "dr-lee", "password": PASSWORD, "role": "clinician"})
    assert r.status_code == 201
    assert admin.post("/api/users", json={"username": "dr-lee", "password": PASSWORD,
                                          "role": "clinician"}).status_code == 409
    assert admin.post("/api/users", json={"username": "x", "password": "short",
                                          "role": "clinician"}).status_code == 400
    assert "password_hash" not in admin.get("/api/users").get_data(as_text=True)


# ---- HTTP hardening -----------------------------------------------------------------------------

def test_security_headers_and_cookie_flags(anon, admin):
    r = admin.get("/")
    assert r.status_code == 200
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"
    r = anon.get("/login")
    cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_unknown_page_renders_friendly_404(admin):
    r = admin.get("/does-not-exist")
    assert r.status_code == 404 and b"Page not found" in r.data


def test_server_errors_hide_internals(app, admin, monkeypatch):
    app.config["PROPAGATE_EXCEPTIONS"] = False
    import triage.commands as cmd

    def broken(ctx, minutes):
        raise RuntimeError("secret internal detail")
    monkeypatch.setattr(cmd, "advance", broken)
    r = admin.post("/api/sim/advance", json={"minutes": 5})
    assert r.status_code == 500 and err(r)["code"] == "server_error"
    assert b"secret internal detail" not in r.data and b"Traceback" not in r.data
