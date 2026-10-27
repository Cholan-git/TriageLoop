"""Shared fixtures. Every test gets its own temporary database; synthetic test
data never touches the application's real database."""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if path not in sys.path:
        sys.path.insert(0, path)

from werkzeug.security import generate_password_hash  # noqa: E402

from helpers import PASSWORD, Client, Engine  # noqa: E402
from triage.store import Store  # noqa: E402


@pytest.fixture
def engine(tmp_path):
    e = Engine(str(tmp_path / "engine.db"))
    # Deterministic tests: no synthetic drift unless a test opts in.
    e.configure(vitals_drift=False)
    yield e
    e.conn.close()


@pytest.fixture
def app(tmp_path):
    from web import create_app
    app = create_app({"TESTING": True, "SECRET_KEY": "test-secret",
                      "DATABASE": str(tmp_path / "app.db"), "TIMEZONE": "UTC"})
    store = app.extensions["store"]
    conn = store.connect()
    with Store.write(conn):
        Store.create_user(conn, "admin", generate_password_hash(PASSWORD), "admin")
        Store.create_user(conn, "nurse", generate_password_hash(PASSWORD), "clinician")
    conn.close()
    from web.security import limiter
    limiter._fails.clear()
    yield app


@pytest.fixture
def admin(app):
    c = Client(app.test_client())
    assert c.login("admin").status_code == 302
    c.put("/api/settings", json={"changes": {"vitals_drift": False}})
    return c


@pytest.fixture
def clinician(app):
    c = Client(app.test_client())
    assert c.login("nurse").status_code == 302
    return c


@pytest.fixture
def anon(app):
    return Client(app.test_client())
