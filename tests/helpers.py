"""Test helpers shared by unit and API tests (imported, not a conftest)."""
from __future__ import annotations

import itertools

from triage import commands as cmd
from triage.projection import rebuild
from triage.store import Store

NORMAL = dict(rr=16, spo2=97, on_oxygen=False, sbp=122, hr=78, avpu="A", temp=37.0)
PASSWORD = "correct-horse-battery"


# ---- engine-level helpers ---------------------------------------------------------

class Engine:
    """Run commands against a throwaway database, as the API would."""

    def __init__(self, path):
        self.store = Store(path)
        self.conn = self.store.connect()
        self.store.migrate(self.conn)
        cmd.ensure_run(self.store, self.conn, "tester", "UTC")

    def do(self, fn, *args, **kwargs):
        with cmd.command(self.store, self.conn, "tester") as ctx:
            return fn(ctx, *args, **kwargs)

    def state(self):
        return rebuild(self.store.run_events(self.conn, self.store.active_run_id(self.conn)))

    def configure(self, **changes):
        return self.do(cmd.update_settings, changes)

    def arrive(self, esi=3, complaint="Test complaint", needs=None, vitals=None):
        return self.do(cmd.arrival, esi, complaint, needs or [], NORMAL if vitals is None else vitals)["pid"]



# ---- web-level helpers --------------------------------------------------------------

_keys = itertools.count(1)


class Client:
    def __init__(self, flask_client):
        self.c = flask_client
        self.csrf = None

    def login(self, username, password=PASSWORD):
        self.c.get("/login")
        with self.c.session_transaction() as s:
            token = s.get("csrf")
        r = self.c.post("/login", data={"username": username, "password": password,
                                        "csrf_token": token})
        if r.status_code == 302:
            self.csrf = self.c.get("/api/session").get_json()["csrf"]
        return r

    def get(self, path, **kw):
        return self.c.get(path, **kw)

    def post(self, path, json=None, key=None, csrf=True, **kw):
        headers = kw.pop("headers", {})
        if csrf and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        if key is True:
            headers["Idempotency-Key"] = f"test-key-{next(_keys):06d}"
        elif key:
            headers["Idempotency-Key"] = key
        return self.c.post(path, json={} if json is None else json, headers=headers, **kw)

    def put(self, path, json=None, **kw):
        headers = kw.pop("headers", {})
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        return self.c.put(path, json=json, headers=headers, **kw)
