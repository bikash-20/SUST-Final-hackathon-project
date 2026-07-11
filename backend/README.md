# Backend

FastAPI, PostgreSQL, and deterministic synthetic scenarios for the
multi-provider decision-support prototype.

## Database

Run all migrations in order. They are independently rerunnable and never
reset existing balances:

```bash
psql -U postgres -d codex_demo -f infra/001_init.sql
psql -U postgres -d codex_demo -f infra/002_hardening.sql
psql -U postgres -d codex_demo -f infra/003_case_notes.sql
psql -U postgres -d codex_demo -f infra/004_historical_analytics.sql
```

The root `docker-compose.yml` mounts this directory into PostgreSQL's ordered
initialization directory, so a fresh container applies all automatically.

## Application

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Swagger is available at `http://localhost:8000/docs`. Important endpoints:

- `POST /v1/simulation/scenario` - run scenario A, B, C, or D.
- `GET /v1/telemetry/snapshot` - current separated balances and cases.
- `GET /v1/telemetry/stream` - replay-aware named SSE events.
- `POST /v1/coordination/transit` - acknowledged/resolved human actions.
- `GET /v1/metrics` - measured analytics and reliability evidence.
- `GET /health` - process-only liveness for an external keep-alive.
- `GET /healthz` - database readiness used by Render.

## Render

The repository-root `render.yaml` provisions a PostgreSQL 16 database and a
single Docker web service. The service uses `backend` as its root directory,
builds `Dockerfile`, and runs the executable `scripts/start.sh`, which applies
all migrations before starting:

```bash
uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
```

The startup migration is intentional for the Free web instance because Render
pre-deploy commands are paid-only. For a paid service, move
`sh scripts/migrate.sh` to the Render **Pre-Deploy Command** and leave only the
uvicorn command as the Docker command.

If configuring the Docker service manually, use:

```text
Root Directory: backend
Dockerfile Path: ./Dockerfile
Docker Build Context: .
Docker Command: ./scripts/start.sh
Health Check Path: /healthz
```

`DATABASE_URL` and `MIGRATION_DATABASE_URL` must use Render's direct internal
connection string, not its PgBouncer URL. `DATABASE_URL` supplies only the
host, port, and database; runtime connections replace its owner credentials
with the four scoped role credentials. The migration preflight requires the
original database owner to have `CREATEROLE`; keep that owner credential stable
so it retains administration of the SQL-created roles. The complete variable
list is in the root README and `.env.example`.

After the backend is healthy, populate the 30-day lookback with 60 committed,
synthetic salary-window transactions and print the resulting analytics:

```bash
sh backend/scripts/seed_historical_demo.sh https://your-api.onrender.com
```

This uses the public simulation API and normal atomic ledger path. It creates
real committed demo records inside the 30-day lookback; it does not fabricate
30 elapsed days of activity.

## Tests

```bash
PYTHONPATH=. .venv/bin/python -m unittest discover -s tests -v
```

The tests cover the unlabelled anomaly detector, EWMA forecasting, restart-safe
SSE cursors, advisory recurrence, scenario scheduling, exact-once simulation
integration, and runtime metrics. Before a live demonstration, also apply all
migrations twice to a disposable PostgreSQL instance and run the scenario loop
to verify role denials and the atomic zero-sum journal in the target runtime.
