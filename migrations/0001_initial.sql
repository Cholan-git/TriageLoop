-- TriageLoop initial schema.
-- The events table is the single source of truth for simulation state; every
-- other table holds identity, access control or request bookkeeping.

CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE
                          CHECK (length(username) BETWEEN 3 AND 40),
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('clinician', 'admin')),
    active        INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at    TEXT    NOT NULL
);

-- A run is one simulation session. Starting a new run never deletes the
-- events of earlier runs; they stay in the audit trail.
CREATE TABLE runs (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    synthetic   INTEGER NOT NULL DEFAULT 0 CHECK (synthetic IN (0, 1)),
    created_at  TEXT    NOT NULL,
    created_by  TEXT
);

-- Small key/value table for application-wide pointers (e.g. active run).
CREATE TABLE app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,   -- global sequence number
    event_uid   TEXT    NOT NULL UNIQUE,
    run_id      INTEGER REFERENCES runs(id),         -- NULL for global events (auth, users)
    type        TEXT    NOT NULL,
    sim_minute  INTEGER CHECK (sim_minute IS NULL OR sim_minute >= 0),
    recorded_at TEXT    NOT NULL,                    -- wall clock, UTC ISO-8601
    actor       TEXT,
    entity_type TEXT,
    entity_id   TEXT,
    synthetic   INTEGER NOT NULL DEFAULT 0 CHECK (synthetic IN (0, 1)),
    data        TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(data)),
    CHECK ((run_id IS NULL) = (sim_minute IS NULL))
);

CREATE INDEX idx_events_run_order ON events (run_id, sim_minute, id);
CREATE INDEX idx_events_run_type  ON events (run_id, type);
CREATE INDEX idx_events_entity    ON events (run_id, entity_type, entity_id);

-- Append-only: history cannot be edited or removed through the database.
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;

CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;

-- Idempotency keys for mutating requests (duplicate clicks / retries).
CREATE TABLE request_keys (
    key        TEXT PRIMARY KEY CHECK (length(key) BETWEEN 8 AND 100),
    endpoint   TEXT NOT NULL,
    created_at TEXT NOT NULL
);
