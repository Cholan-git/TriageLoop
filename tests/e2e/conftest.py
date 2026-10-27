"""End-to-end fixtures: a real server on a free port with its own database,
driven by a real Chromium through Playwright.

Run with:  python -m pytest tests/e2e
Set TRIAGELOOP_E2E_CHROMIUM to a Chromium executable if Playwright's bundled
browser is not installed (python -m playwright install chromium).
"""
from __future__ import annotations

import os
import pytest
from werkzeug.security import generate_password_hash

pw = pytest.importorskip("playwright.sync_api")

from e2e_support import ADMIN_PW, NURSE_PW, Server  # noqa: E402


@pytest.fixture
def server(tmp_path):
    srv = Server(str(tmp_path / "e2e.db")).start()
    from triage.store import Store
    conn = srv.app.extensions["store"].connect()
    with Store.write(conn):
        Store.create_user(conn, "admin", generate_password_hash(ADMIN_PW), "admin")
        Store.create_user(conn, "nurse", generate_password_hash(NURSE_PW), "clinician")
    conn.close()
    yield srv
    srv.stop()


@pytest.fixture(scope="session")
def playwright_instance():
    with pw.sync_playwright() as p:
        yield p


@pytest.fixture
def browser(playwright_instance):
    # One browser per test: some sandboxed Chromium builds must run with
    # --single-process and cannot survive a closed context.
    exe = os.environ.get("TRIAGELOOP_E2E_CHROMIUM")
    args = ["--no-sandbox", "--disable-gpu"]
    if exe:
        args += ["--single-process", "--no-zygote"]
    try:
        b = playwright_instance.chromium.launch(executable_path=exe or None, args=args)
    except Exception as e:  # browser not installed
        pytest.skip(f"Chromium not available: {e}")
    yield b
    b.close()


@pytest.fixture
def make_page(browser, server):
    contexts = []

    def _make(user="admin", viewport=(1440, 960), color_scheme="light", login=True):
        ctx = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]},
                                  color_scheme=color_scheme)
        contexts.append(ctx)
        page = ctx.new_page()
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
        if login:
            page.goto(server.url + "/login")
            page.fill("input[name=username]", user)
            page.fill("input[name=password]", ADMIN_PW if user == "admin" else NURSE_PW)
            page.click("button[type=submit]")
            pw.expect(page.locator("#k-waiting")).to_have_text(__import__("re").compile(r"^\d+$"))
        return page

    yield _make
