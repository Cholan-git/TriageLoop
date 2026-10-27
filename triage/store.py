"""SQLite persistence: migrations, the append-only event table, runs and users.

Uses only the standard-library sqlite3 module. All SQL is parameterised.
Writers take ``BEGIN IMMEDIATE`` so concurrent mutations are serialised by the
database itself, not only by the web process.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager

from .timefmt import utc_now_iso

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")


class Store:
    def __init__(self, path: str):
        self.path = path

    # -- connections ----------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @staticmethod
    @contextmanager
    def write(conn: sqlite3.Connection):
        """A write transaction. Rolls back on any exception."""
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    # -- migrations -----------------------------------------------------------
    def migrate(self, conn: sqlite3.Connection | None = None) -> list[str]:
        """Apply pending migrations in filename order. Safe to run repeatedly."""
        own = conn is None
        conn = conn or self.connect()
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                         "name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
            done = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
            applied = []
            for name in sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql")):
                if name in done:
                    continue
                with open(os.path.join(MIGRATIONS_DIR, name), encoding="utf-8") as fh:
                    sql = fh.read()
                with self.write(conn):
                    for stmt in _split_sql(sql):
                        conn.execute(stmt)
                    conn.execute("INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                                 (name, utc_now_iso()))
                applied.append(name)
            return applied
        finally:
            if own:
                conn.close()

    # -- events ---------------------------------------------------------------
    @staticmethod
    def insert_event(conn, *, type, run_id=None, sim_minute=None, actor=None, entity_type=None,
                     entity_id=None, synthetic=False, data=None, recorded_at=None) -> dict:
        ev = {"event_uid": uuid.uuid4().hex, "run_id": run_id, "type": type,
              "sim_minute": sim_minute, "recorded_at": recorded_at or utc_now_iso(),
              "actor": actor, "entity_type": entity_type, "entity_id": entity_id,
              "synthetic": 1 if synthetic else 0, "data": data or {}}
        cur = conn.execute(
            "INSERT INTO events (event_uid, run_id, type, sim_minute, recorded_at, actor, "
            "entity_type, entity_id, synthetic, data) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ev["event_uid"], run_id, type, sim_minute, ev["recorded_at"], actor, entity_type,
             entity_id, ev["synthetic"], json.dumps(ev["data"], separators=(",", ":"))))
        ev["id"] = cur.lastrowid
        return ev

    @staticmethod
    def run_events(conn, run_id: int) -> list[dict]:
        rows = conn.execute("SELECT * FROM events WHERE run_id = ? ORDER BY sim_minute, id",
                            (run_id,)).fetchall()
        return [_row_event(r) for r in rows]

    @staticmethod
    def run_version(conn, run_id: int) -> int:
        return conn.execute("SELECT COALESCE(MAX(id), 0) FROM events WHERE run_id = ?",
                            (run_id,)).fetchone()[0]

    @staticmethod
    def query_events(conn, run_id: int | None, *, types=None, entity_id=None, search=None,
                     include_global=False, limit=50, offset=0) -> tuple[list[dict], int]:
        where, args = [], []
        if include_global:
            where.append("(run_id = ? OR run_id IS NULL)")
        else:
            where.append("run_id = ?")
        args.append(run_id)
        if types:
            where.append(f"type IN ({','.join('?' * len(types))})")
            args += list(types)
        if entity_id:
            where.append("entity_id = ? COLLATE NOCASE")
            args.append(entity_id)
        if search:
            where.append("(type LIKE ? ESCAPE '\\' OR entity_id LIKE ? ESCAPE '\\' "
                         "OR actor LIKE ? ESCAPE '\\' OR data LIKE ? ESCAPE '\\')")
            like = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            args += [like] * 4
        sql_where = " AND ".join(where)
        total = conn.execute(f"SELECT COUNT(*) FROM events WHERE {sql_where}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM events WHERE {sql_where} "
            "ORDER BY COALESCE(sim_minute, -1) DESC, id DESC LIMIT ? OFFSET ?",
            args + [limit, offset]).fetchall()
        return [_row_event(r) for r in rows], total

    # -- idempotency -------------------------------------------------------------
    @staticmethod
    def claim_key(conn, key: str, endpoint: str) -> bool:
        """Record a request key inside the current transaction. False if already used."""
        try:
            conn.execute("INSERT INTO request_keys (key, endpoint, created_at) VALUES (?,?,?)",
                         (key, endpoint, utc_now_iso()))
            return True
        except sqlite3.IntegrityError:
            return False

    # -- runs -------------------------------------------------------------------
    @staticmethod
    def active_run_id(conn) -> int | None:
        row = conn.execute("SELECT value FROM app_state WHERE key = 'active_run'").fetchone()
        return int(row["value"]) if row else None

    @staticmethod
    def set_active_run(conn, run_id: int) -> None:
        conn.execute("INSERT INTO app_state (key, value) VALUES ('active_run', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(run_id),))

    @staticmethod
    def create_run(conn, name: str, synthetic: bool, actor: str | None) -> int:
        cur = conn.execute("INSERT INTO runs (name, synthetic, created_at, created_by) "
                           "VALUES (?,?,?,?)", (name, 1 if synthetic else 0, utc_now_iso(), actor))
        return cur.lastrowid

    @staticmethod
    def get_run(conn, run_id: int):
        return conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()

    @staticmethod
    def list_runs(conn) -> list[dict]:
        rows = conn.execute(
            "SELECT r.*, (SELECT COUNT(*) FROM events e WHERE e.run_id = r.id) AS events "
            "FROM runs r ORDER BY r.id DESC").fetchall()
        return [dict(r) for r in rows]

    # -- users ------------------------------------------------------------------
    @staticmethod
    def user_by_name(conn, username: str):
        return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    @staticmethod
    def user_by_id(conn, uid: int):
        return conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()

    @staticmethod
    def count_users(conn) -> int:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    @staticmethod
    def create_user(conn, username: str, password_hash: str, role: str) -> int:
        cur = conn.execute("INSERT INTO users (username, password_hash, role, created_at) "
                           "VALUES (?,?,?,?)", (username, password_hash, role, utc_now_iso()))
        return cur.lastrowid

    @staticmethod
    def list_users(conn) -> list[dict]:
        return [dict(id=r["id"], username=r["username"], role=r["role"], active=bool(r["active"]),
                     created_at=r["created_at"])
                for r in conn.execute("SELECT * FROM users ORDER BY id")]


def _row_event(r) -> dict:
    d = dict(r)
    d["data"] = json.loads(d["data"])
    return d


def _split_sql(sql: str) -> list[str]:
    """Split a migration file into statements, keeping trigger bodies intact."""
    stmts, buf, in_trigger = [], [], False
    for line in sql.splitlines():
        stripped = line.strip()
        if not buf and (not stripped or stripped.startswith("--")):
            continue
        buf.append(line)
        upper = stripped.upper()
        if upper.startswith("CREATE TRIGGER"):
            in_trigger = True
        if in_trigger:
            if upper.endswith("END;"):
                stmts.append("\n".join(buf))
                buf, in_trigger = [], False
        elif stripped.endswith(";"):
            stmts.append("\n".join(buf))
            buf = []
    if "".join(buf).strip():
        stmts.append("\n".join(buf))
    return stmts
