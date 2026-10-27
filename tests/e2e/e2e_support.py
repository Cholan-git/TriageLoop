"""Helpers for the end-to-end tests (imported, not a conftest)."""
from __future__ import annotations

import socket
import threading

from werkzeug.serving import make_server

ADMIN_PW = "admin-password-123"
NURSE_PW = "nurse-password-123"


class Server:
    def __init__(self, db_path):
        self.db_path = db_path
        self.port = None
        self._srv = None

    def start(self):
        from web import create_app
        app = create_app({"SECRET_KEY": "e2e-secret", "DATABASE": self.db_path,
                          "TIMEZONE": "UTC", "LOGIN_MAX_FAILURES": 50})
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self._srv = make_server("127.0.0.1", self.port, app, threaded=True)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        self.app = app
        return self

    def stop(self):
        self._srv.shutdown()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"
