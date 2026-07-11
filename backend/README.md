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

## Tests

```bash
PYTHONPATH=. .venv/bin/python -m unittest discover -s tests -v
```

The tests cover the unlabelled anomaly detector, EWMA forecasting, restart-safe
SSE cursors, advisory recurrence, scenario scheduling, exact-once simulation
integration, and runtime metrics. Before a live demonstration, also apply both
migrations twice to a disposable PostgreSQL instance and run the scenario loop
to verify role denials and the atomic zero-sum journal in the target runtime.
