# LiquiGuard — Multi-Provider Decision Support

A working, synthetic-data-only prototype for bKash presents SUST CSE Carnival
2026. It keeps shared physical cash separate from the bKash, Nagad, and Rocket
e-money ledgers; computes online liquidity forecasts; detects unusual behaviour
without scenario labels; and routes important evidence into a human-owned case.

The system never connects to a real wallet, moves funds, blocks an account, or
makes a final fraud determination.

## Architecture

```text
synthetic scenarios -> queue-backed simulation engine -> isolated ledgers
                                      |                 -> PostgreSQL history
                                      |                 -> EWMA TTE + history context
                                      |                 -> anomaly detector
                                      v
                         durable event/audit tables -> SSE stream
                                      |                 -> Agent view
                                      |                 -> Operations view
                                      +-----------------> Risk review view
```

PostgreSQL login roles are provider-scoped. `app_shared` cannot directly read
or mutate provider schemas. Provider reads and upstream drains use separately
authenticated sessions; customer exchanges use one allowlisted
`SECURITY DEFINER` function owned by a constrained `NOLOGIN` role so shared
cash, inverse provider e-money, and both audit legs commit atomically.

## Run from a clean machine

Requirements: Docker, Python 3.11+, `uv`, Node.js 20+, and npm.

```bash
docker compose up -d --wait postgres

UV_CACHE_DIR=/tmp/liquiguard-uv-cache uv venv backend/.venv
UV_CACHE_DIR=/tmp/liquiguard-uv-cache uv pip install --python backend/.venv/bin/python \
  'fastapi>=0.111,<0.112' 'uvicorn[standard]>=0.30,<0.31' \
  'sqlalchemy[asyncio]>=2,<3' 'asyncpg>=0.29,<0.30' 'pydantic>=2.6,<3'

cd backend
.venv/bin/uvicorn app.main:app --port 8000
```

In a second terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:3000`. The Next.js proxy sends `/v1/*` to the backend.

## Verified demo flow

```bash
make scenario-a  # actual provider drain -> computed EWMA TTE -> liquidity case
make scenario-b  # unlabeled transactions -> detector evidence -> review case
make scenario-c  # stale/conflicting feed -> lower-confidence safe fallback
make scenario-d  # explicit coordination lifecycle demonstration
```

Switch between Agent Mobile, Ops Web, and Risk Reviewer without reloading. The
single SSE connection stays mounted at the application provider boundary.

## Evidence and checks

```bash
make verify
curl -fsS http://localhost:8000/v1/metrics
curl -fsS http://localhost:8000/v1/telemetry/snapshot
```

Runtime metrics contain measured processing p50/p95, tick reliability,
explanation coverage, forecast counts, and observed shortage lead time. Empty
metrics return `null` or zero rather than invented demo values.

Historical forecast context defaults to the last 30 simulated days and can be
configured with `HISTORICAL_WINDOW_DAYS` (1–365). It enriches confidence metadata
without replacing the live 12-minute EWMA or changing its original confidence.

## Vercel and Render deployment

This is an isolated monorepo. In Vercel, connect this GitHub repository and set
the project **Root Directory** to `frontend`. Set `NEXT_PUBLIC_BACKEND_URL` to the
public HTTPS domain of the Render backend; Vercel then builds the Next.js app
using `frontend/vercel.json` and proxies `/v1/*` to Render.

The repository-root `render.yaml` is the preferred deployment path. It creates
a PostgreSQL 16 database and a one-instance Docker web service, generates the
four application-role secrets, runs the rerunnable migrations, starts uvicorn
on Render's `$PORT`, and checks `/healthz`. During Blueprint creation, set
`CORS_ALLOWED_ORIGINS` to the exact Vercel origin, for example
`https://your-project.vercel.app` (no path or trailing slash).

These are all backend variables. The Blueprint supplies them automatically;
use the same list if configuring the Render dashboard by hand:

```text
DATABASE_URL=<Render direct internal connection string>
MIGRATION_DATABASE_URL=<same direct internal owner connection string>
DB_APP_USER=app_shared
DB_APP_PASSWORD=<unique generated secret>
DB_BKASH_USER=app_bkash
DB_BKASH_PASSWORD=<unique generated secret>
DB_NAGAD_USER=app_nagad
DB_NAGAD_PASSWORD=<unique generated secret>
DB_ROCKET_USER=app_rocket
DB_ROCKET_PASSWORD=<unique generated secret>
DEMO_AGENT_ID=00000000-0000-0000-0000-000000000001
ANOMALY_ALLOWLISTED_PROVIDERS=
HISTORICAL_WINDOW_DAYS=30
CORS_ALLOWED_ORIGINS=https://your-project.vercel.app
```

Use `connectionString`, never `connectionPoolString`, for migrations. If
`DATABASE_URL` is omitted, local-style `DB_HOST`, `DB_PORT`, and `DB_NAME` are
the supported alternative. Do not set `PORT`; Render supplies it. Keep the
backend at one replica because its queue, EWMA state, broadcaster, and
deterministic clock are process-local. An external monitor can request
`GET /health` every 10 minutes; `/healthz` remains the database readiness
check. Render's Free PostgreSQL instance expires after 30 days, so upgrade or
replace it before the judged deployment exceeds that age.

GitHub Actions runs database migrations twice, all backend tests, frontend
type-check/lint/build, and a production backend container build. Vercel and
Render Git integrations then create deployments from commits that pass the
repository's required checks; enable the `Backend tests and migrations`,
`Frontend quality and production build`, and `Backend container build` branch
protection checks on `main`.

The current implementation guide and demo choreography live under
[`docs/`](docs/); the older design documents there are labelled as design
history where they differ from the runtime. Runnable source is under
[`backend/`](backend/) and [`frontend/`](frontend/).
