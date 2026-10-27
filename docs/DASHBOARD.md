# TriageLoop technical reference

Setup and day-to-day use are in the [README](../README.md). This document explains how the
system works: architecture, rules, metric formulas, the resource model, the API and the data model.

## Architecture

```
Event sources ─► Append-only event log ─► Rebuild state ─► Flags ─► Dashboard
(clinicians,       (SQLite, edits and        (replay by        (explainable    (clinician view)
 simulation)        deletes blocked)          minute, id)       rules)              │
      ▲                                                                              │
      └────────────────────────── Clinician decision (recorded as an event) ◄────────┘
```

- **One source of truth.** A run's state is a pure function of its events
  (`triage/projection.py`). Nothing else stores patients, resources or settings.
- **Commands** (`triage/commands.py`) are the only way to change state. Each one runs in a
  single `BEGIN IMMEDIATE` transaction: rebuild → validate → append events. Any error rolls the
  whole command back.
- **Read model** (`triage/readmodel.py`) computes every displayed number once, on the server. The
  browser renders it and never recalculates scores, flags or metrics.
- **Runs.** Each simulation session is a run. New runs (including the demo) never delete older
  ones. Sign-ins, user creation and run changes are global audit events outside any run.

## Patient lifecycle

| From | Allowed to |
|---|---|
| waiting | in_treatment, discharged |
| in_treatment | admitted, discharged |
| admitted / discharged | — (closed) |

Admitting or discharging releases any resource the patient is using and cancels pending requests.
Mutations that act on a patient may send its `version` (the id of the last event that touched
it); a stale version is rejected with `409` so two clinicians cannot overwrite each other.

## Advisory flags (ruleset `tl-rules-2.0`)

Thresholds are those of the original dashboard and are configurable per run in Settings.

| Rule | Kind | Fires when |
|---|---|---|
| `R-NEWS2-HIGH` | danger, **urgent** | Latest NEWS2 ≥ `news2_urgent` (7) |
| `R-NEWS2-RISE` | danger | NEWS2 − baseline ≥ `news2_rise` (2). Baseline = NEWS2 at arrival or at the last clinician review. **Urgent** when NEWS2 ≥ `news2_urgent_rise` (5) |
| `R-RESOURCE-DELAY` | warn | A requested CT/Bed/Lab is estimated to start later than now (outage, all units busy, or zero capacity) |
| `R-REASSESS-OVERDUE` | warn | Minutes since last review (or arrival) > ESI target: ESI1 0, ESI2 30, ESI3 60, ESI4 120, ESI5 240 |
| `R-VITALS-MISSING` | warn | No observations recorded, so NEWS2 cannot be calculated |
| `R-VITALS-STALE` | warn | Latest observations older than `vitals_stale_min` (60). A data-quality notice, not a clinical threshold |
| `R-NEWS2-PARTIAL` | info | NEWS2 calculated with some parameters missing (true score may be higher) |

**Level:** `urgent` if an urgent rule fired, `reassess` for any other danger/warn reason, otherwise
`stable`. Informational notes never raise the level. An urgent patient always lists the rule that
made it urgent. Flags never change ESI or status; only a clinician review can change ESI, and the
UI requires explicit confirmation.

**Queue order:** level (urgent, reassess, stable), then ESI, then NEWS2 (higher first), then wait
(longer first), then patient id.

## Metric definitions

| Metric | Formula / population |
|---|---|
| Patients waiting | Patients with status `waiting` |
| Priority flags | Active patients (waiting or in treatment) whose level is `urgent`. Independent of any UI filter |
| Average wait time | Mean of (now − arrival) over waiting patients; shown as "—" when nobody is waiting. A review does not reset it |
| Since assessment | now − (last review or arrival); drives `R-REASSESS-OVERDUE` |
| Resources available | Resources that are online and have at least one free unit, out of 3 |
| Utilisation | Units in use ÷ configured capacity (0% when offline or capacity is 0) |
| Wait estimate | Simulated start time from the resource model below, minus now |
| Last successful update | Wall-clock time the browser last received fresh data (sidebar) |

All times shown are the simulation clock: the run's start time plus simulated minutes.

## Resource model

- Each resource has `capacity` units; each job (one patient's use of one resource) occupies a unit
  for `duration` minutes (defaults: CT 1×30 min, Beds 12×120 min, Lab 3×20 min).
- Requested jobs start as soon as a unit is free and the resource is online, in clinical priority
  order: ESI, then NEWS2 (higher first), then arrival time.
- Completions and recoveries are recorded as events **at the minute they occur**, even when the
  clock is advanced in one step.
- An outage takes the whole resource offline and interrupts jobs in progress (they return to the
  queue). A second outage that ends later **extends** the current one; one that ends sooner leaves
  it unchanged. Administrators can end an outage early.
- Reducing capacity below the number of units in use lets current jobs finish; nothing new starts
  until usage drops below capacity.
- Outages are simulated. TriageLoop does not connect to or control any equipment.

## Simulation and synthetic data

- **Add arrivals** creates 5 synthetic patients (10 in the Surge scenario, which also skews toward
  higher acuity) through the same validation path as manual registration.
- **Advance** moves the clock and, when `vitals_drift` is on, records seeded simulated vital-sign
  changes for up to three waiting patients. The same state always produces the same drift.
- Synthetic events and patients are flagged `synthetic` in the database and labelled in the UI. The
  demo scenario is a separate run that is synthetic in full. Nothing is inserted automatically on
  start-up.

## API

All endpoints require a signed-in session. Mutating requests must send `X-CSRF-Token` (from the
page's `csrf-token` meta tag) and a JSON body, and may send `Idempotency-Key` (8–100 characters):
a repeated key returns `"duplicate": true` without applying the change twice.

Success: `{"ok": true, "message": str, "result": {...}, "state": <snapshot>}`
Error: `{"error": {"code": str, "message": str, "details": {}}}` with 400/401/403/404/409/413/415/500.

| Method & path | Role | Purpose |
|---|---|---|
| `GET /api/session` | any | Current user, permissions, CSRF token |
| `GET /api/state` | any | Full snapshot: clock, KPIs, ranked patients, resources, analytics |
| `GET /api/version` | any | Latest event id (cheap change detection for polling) |
| `GET /api/patients/<pid>` | any | Patient detail: observations, jobs, timeline |
| `POST /api/patients` | clinician | Register arrival `{esi, complaint, needs[], vitals{}}` |
| `POST /api/patients/<pid>/vitals` | clinician | `{vitals, version?}` |
| `POST /api/patients/<pid>/review` | clinician | `{note?, esi?, version?}` |
| `POST /api/patients/<pid>/transition` | clinician | `{to, note?, version?}` |
| `POST /api/sim/advance` | clinician | `{minutes: 1–240}` |
| `POST /api/sim/arrivals` | clinician | `{count?: 1–20}` synthetic arrivals |
| `POST /api/sim/scenario` | clinician | `{scenario: "Normal"/"Surge"}` |
| `POST /api/resources/<CT/BEDS/LAB>/outage` | clinician | `{minutes: 1–1440, reason?}` |
| `POST /api/resources/<code>/recover` | admin | End an outage early |
| `GET /api/settings` · `PUT /api/settings` | any · admin | Read / change run configuration `{changes}` |
| `GET /api/events` | any | `?category=&patient=&q=&page=&page_size=&scope=all` (security events: admin) |
| `GET /api/events/export.csv` | admin | Event log export (formula-injection safe) |
| `GET /api/runs` · `POST /api/runs` | any · admin | List runs / start an empty run `{name}` |
| `POST /api/runs/demo` | admin | Start the synthetic demo scenario |
| `POST /api/runs/<id>/activate` | admin | Reopen an earlier run |
| `GET /api/users` · `POST /api/users` | admin | List / create users `{username, password, role}` |

## Data model (`migrations/0001_initial.sql`)

| Table | Contents |
|---|---|
| `events` | Global sequence `id`, `event_uid`, `run_id`, `type`, `sim_minute`, `recorded_at` (UTC), `actor`, `entity_type`, `entity_id`, `synthetic`, JSON `data`. Triggers reject `UPDATE` and `DELETE`. Indexed by run/minute, run/type and entity |
| `runs` | Simulation sessions (name, synthetic flag, creator) |
| `app_state` | Pointer to the active run |
| `users` | Username (unique, case-insensitive), password hash (Werkzeug scrypt), role, active flag |
| `request_keys` | Used idempotency keys |
| `schema_migrations` | Applied migration files |

Event types: `run_started`, `settings_changed`, `patient_arrived`, `vitals_recorded`,
`clinician_reviewed`, `patient_status_changed`, `job_started`, `job_completed`,
`job_interrupted`, `job_cancelled`, `outage_started`, `outage_extended`, `outage_ended`,
`clock_advanced`; global: `login`, `login_failed`, `logout`, `access_denied`, `user_created`,
`run_created`, `run_activated`, `audit_exported`. Audit events never contain passwords or tokens.

> Simulation only — not for clinical use.
