# TriageLoop — Emergency Care Intelligence

TriageLoop is a full-stack **simulation** of an emergency-department triage queue. It ranks
waiting patients, raises explainable advisory flags (NEWS2 score and rise, resource delays,
overdue reassessment), models CT / Beds / Lab capacity and outages, and records every change in
an append-only event log. **The system suggests; the clinician decides.**

> **Disclaimer.** TriageLoop is an educational prototype for training and demonstration. It is
> not a medical device, has not been clinically validated, and must not be used for real patient
> care. Scores and flags are advisory only.

## Features

- **Patient queue** ranked by flag level, ESI, NEWS2 and wait, with search, filters, sorting and
  pagination; patient detail page with observations, resources and a full timeline.
- **Explainable flags.** Every flag shows its rule ID, ruleset version and the recorded facts behind
  it (e.g. which vital signs drove the NEWS2 score). NEWS2 is calculated by the server from
  recorded vital signs (RCP 2017 chart, SpO₂ scale 1).
- **Clinician actions:** record a review (optionally change ESI with explicit confirmation),
  record vitals, move to treatment, admit or discharge. All changes are validated server-side
  and protected against duplicate clicks and conflicting edits.
- **Resource model:** configurable capacity and time-per-patient for CT, Beds and Lab, priority
  dispatch, simulated outages that interrupt work and recover at the right minute, and per-patient
  wait estimates.
- **Simulation controls:** advance the clock, simulate a CT outage, add synthetic arrivals, switch
  between Normal and Surge scenarios. Synthetic data is always labelled.
- **Event log / audit trail:** append-only (enforced by database triggers), filterable,
  paginated, CSV export for administrators. Sign-ins and denied actions are audited too.
- **Accounts and roles:** Clinician and Administrator, server-side permission checks, CSRF
  protection, hardened session cookies, login rate limiting and a strict Content-Security-Policy.
- **Interface:** Light, Dark and System themes; collapsible sidebar and dashboard panels;
  mobile navigation drawer; keyboard accessible; works offline (fonts and icons are bundled).

## Requirements

- Python 3.10 or newer
- No other services: data is stored in a local SQLite file.

## Quick start

**Windows (Git Bash)**

```bash
cd TriageLoop
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
python app.py
```

**macOS / Linux**

```bash
cd TriageLoop
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5000>.

1. **First run:** with no accounts, the app opens a setup page that creates the first
   **administrator** (password at least 10 characters). Alternatively create one from the command
   line: `flask --app app create-user admin --role admin`.
2. A clean install starts with an **empty** simulation run. To populate it for a demonstration,
   open **Settings → Load demo scenario**. This creates a new run of synthetic patients, labelled
   as demo data throughout the app. Your empty run is kept.
3. Add clinician accounts in **Settings → Users** (or `flask --app app create-user NAME`).

The database schema is created and upgraded automatically on start. `flask --app app init-db`
does the same explicitly; both are safe to repeat.

## Configuration

Settings are read from environment variables. A `.env` file in the project root is also read
(copy `.env.example`); real environment variables take precedence.

| Variable | Default | Purpose |
|---|---|---|
| `TRIAGELOOP_ENV` | `development` | Set to `production` to require a secret key, mark cookies `Secure` and send HSTS. |
| `TRIAGELOOP_SECRET_KEY` | generated | Session signing key. In development a random key is generated once into `instance/secret_key`. **Required in production.** |
| `TRIAGELOOP_DATABASE` | `instance/triageloop.db` | SQLite database path. |
| `TRIAGELOOP_TIMEZONE` | `Asia/Kolkata` | IANA timezone used to start the simulation clock for new runs. |
| `TRIAGELOOP_HOST` / `TRIAGELOOP_PORT` | `127.0.0.1` / `5000` | Address for `python app.py`. |
| `TRIAGELOOP_BEHIND_PROXY` | off | `1` trusts one proxy hop's `X-Forwarded-*` headers (HTTPS detection, client IP for rate limiting). |
| `FLASK_DEBUG` | off | `1` enables the debugger in development only; it is never enabled in production. |

Operational settings (capacities, durations, reassessment targets, flag thresholds, scenario,
simulated vitals drift) are edited by administrators in **Settings**. They are recorded as
events, so every run replays with the settings that applied at the time.

## Deployment

**Render (recommended, uses `render.yaml`):** push the repository to GitHub, then in Render choose
**New → Blueprint**, select the repository and apply. Render builds with
`pip install -r requirements.txt`, starts `gunicorn --workers 1 --threads 8 app:app`, generates
`TRIAGELOOP_SECRET_KEY`, and mounts a 1 GB disk at `/var/data` for the database. Open the
`*.onrender.com` URL and create the administrator on the setup page; do this straight away, since
the first visitor to an empty deployment becomes the administrator. A persistent disk needs a
paid instance (Starter); on a free instance the database is lost whenever the service restarts.

**Any Linux host:** set `TRIAGELOOP_ENV=production`, `TRIAGELOOP_SECRET_KEY`,
`TRIAGELOOP_DATABASE` (a path on persistent storage) and, behind a reverse proxy,
`TRIAGELOOP_BEHIND_PROXY=1`; serve over HTTPS with
`gunicorn --workers 1 --threads 8 --bind 0.0.0.0:8000 app:app`. Keep one worker: the login rate
limiter is in memory.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/unit tests/api        # domain engine, API, auth and security
python -m playwright install chromium        # once, for browser tests
python -m pytest tests/e2e                   # end-to-end journeys in a real browser
```

Each test uses its own temporary database; test data never touches `instance/`. If Playwright's
bundled browser cannot be installed, point `TRIAGELOOP_E2E_CHROMIUM` at a Chromium executable.
Browser tests are skipped automatically when no browser is available.

## Project structure

```
app.py                 Entry point (python app.py)
triage/                Domain engine (no web code)
  scoring.py           NEWS2 scoring (RCP 2017, scale 1)
  config.py            Run configuration, defaults and validation
  projection.py        Rebuild state by replaying events
  scheduler.py         Resource capacity, dispatch, outages, wait estimates
  rules.py             Explainable advisory flags (ruleset tl-rules-2.0)
  readmodel.py         Metrics and the JSON the UI renders
  commands.py          Validated, transactional state changes
  simulator.py         Opt-in demo scenario (synthetic)
  store.py             SQLite access, migrations, users, runs
web/                   Flask app: auth, API, security headers, CLI
migrations/            Numbered SQL migrations
templates/, static/    UI (vanilla JS modules, no build step)
tests/                 unit/, api/, e2e/
docs/DASHBOARD.md      Technical reference: rules, metrics, resource model, API, data model
```

## Data, backup and retention

- All state lives in one SQLite file. Simulation state is **never edited in place**: it is
  rebuilt by replaying the append-only `events` table, which rejects updates and deletes.
- **Back up** by stopping the app and copying the database file (and its `-wal` file if present),
  or while running with `sqlite3 instance/triageloop.db ".backup backup.db"`.
- **Restore** by putting the backup file back at `TRIAGELOOP_DATABASE` and restarting.
- Starting a new run or loading the demo never deletes earlier runs; administrators can reopen
  them in Settings. To discard everything, stop the app and delete the database file.
- No patient data, credentials or tokens are stored in the browser; only theme, sidebar and
  panel preferences are kept in `localStorage`.

## Known limitations

- Single-site, single-process deployment with SQLite; suited to demonstrations and training,
  not to high-volume or multi-server use. The login rate limiter is in-memory.
- The resource model is deliberately simple (fixed time per patient, whole-resource outages).
- Data refreshes by polling every 8 seconds rather than by push.
- User management covers creating accounts; password change, deactivation and role change are
  not yet available in the UI.
- The legacy `data/events.jsonl` file from the earlier prototype is not imported: it stored NEWS2
  totals without the underlying vital signs, so it cannot be converted without inventing
  observations. The file is left untouched if present.

## Team responsibilities

- **Person A — Engine Lead:** Event processing, patient state reconstruction, scoring, alerts, and automated tests.
- **Person B:** Project component and tasks assigned by the team.
- **Person C:** Dashboard development and integration.

## Credits

Icons from [Lucide](https://lucide.dev) (ISC License, `static/icons/LICENSE.txt`). Inter typeface
by Rasmus Andersson (SIL Open Font License, `static/fonts/Inter-LICENSE.txt`).
