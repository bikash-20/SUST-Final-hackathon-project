# 5. Monorepo Layout

> This is the checked-in implementation, not a target-state diagram. The demo has two application processes (FastAPI and Next.js) backed by PostgreSQL. The agent-sized mobile experience is a responsive web view; there is no separate mobile deployable.

---

## 5.1 Runtime Topology

```text
Browser :3000
  └─ Next.js App Router
       ├─ responsive Agent / Ops / Risk views
       └─ /v1/* rewrite
             └─ FastAPI :8000
                  ├─ simulation queue + online analytics
                  ├─ named Server-Sent Events (SSE)
                  └─ PostgreSQL 16
                       ├─ shared schema
                       ├─ bkash schema
                       ├─ nagad schema
                       └─ rocket schema
```

The browser uses one same-origin SSE connection at `/v1/telemetry/stream`. Next.js proxies that request to FastAPI, so role changes do not create separate data pipelines.

---

## 5.2 Checked-In Tree

```text
HACKATHON FINAL/
├── README.md
├── Makefile
├── docker-compose.yml                 # PostgreSQL 16 service
├── problem.pdf
├── docs/
│   ├── 00_README.md
│   ├── 01_REQUIREMENTS_ANALYSIS.md
│   ├── 02_DATA_FLOW_AND_STATE_MACHINES.md
│   ├── 03_DATABASE_DESIGN.md
│   ├── 04_API_CONTRACT.md
│   ├── 05_MONOREPO_LAYOUT.md
│   ├── 06_DOMAIN_INJECTIONS.md
│   └── 07_DEMO_SCRIPT.md
│
├── backend/
│   ├── pyproject.toml
│   ├── .env.example
│   ├── README.md
│   ├── infra/
│   │   ├── 001_init.sql               # roles, schemas, ledgers, atomic txn function
│   │   └── 002_hardening.sql          # dead letters and coordination records
│   ├── app/
│   │   ├── main.py                    # FastAPI lifecycle and router wiring
│   │   ├── api/
│   │   │   ├── routes_simulation.py   # clock controls and scenarios A-D
│   │   │   ├── routes_telemetry.py    # snapshot, replay query, and SSE
│   │   │   ├── routes_coordination.py # alert inspection and human transitions
│   │   │   └── routes_metrics.py      # measured runtime evidence
│   │   ├── domain/
│   │   │   ├── shared/cash_ledger.py  # physical cash + optimistic locking
│   │   │   ├── provider/ledger.py     # provider-scoped e-money operations
│   │   │   ├── liquidity/forecaster.py # 12-minute EWMA TTE
│   │   │   ├── risk/anomaly_detector.py
│   │   │   ├── coordination/state_machine.py
│   │   │   └── metrics/collector.py
│   │   ├── infrastructure/
│   │   │   ├── database.py            # shared/provider engines and role sessions
│   │   │   └── broadcaster.py         # bounded in-process SSE fan-out
│   │   └── simulation/
│   │       ├── simulation_engine.py   # 60x clock, workers, persistence, analytics
│   │       └── scenarios/
│   │           ├── scenario_a.py      # provider liquidity pressure
│   │           ├── scenario_b.py      # unlabelled transaction behaviour
│   │           └── scenario_d.py      # coordination drill
│   └── tests/
│       ├── test_anomaly_detector.py
│       ├── test_broadcaster.py
│       ├── test_liquidity_forecaster.py
│       ├── test_runtime_metrics.py
│       ├── test_scenario_a.py
│       └── test_simulation_engine.py
│
└── frontend/
    ├── package.json
    ├── package-lock.json
    ├── next.config.js                 # /v1/* backend rewrite
    ├── eslint.config.mjs
    ├── tailwind.config.js
    ├── tsconfig.json
    └── src/
        ├── app/
        │   ├── layout.tsx
        │   ├── page.tsx               # typed role-selected view
        │   └── globals.css
        └── features/
            ├── shell/                 # provider boundary + role store/switcher
            ├── telemetry/             # EventSource parser and typed Zustand state
            ├── advisory/              # TTE evidence and uncertainty
            ├── safety/                # stale-feed fallback
            ├── agent/                 # responsive field-agent view
            ├── ops/                   # liquidity and coordination cockpit
            └── risk/                  # behavioural evidence review
```

Generated directories such as `backend/.venv`, `frontend/node_modules`, and `frontend/.next` are local build artifacts, not architecture components.

---

## 5.3 Actual Module Boundaries

1. `infrastructure/database.py` owns connection creation, provider-ID validation, and provider-specific PostgreSQL roles.
2. Shared cash and provider e-money remain separate persisted positions. Cross-ledger customer transactions use the allowlisted `shared.apply_provider_customer_transaction(...)` database function for one atomic, idempotent commit.
3. The liquidity and risk modules consume committed deltas or production-shaped observations. They do not accept injected TTE values or scenario truth labels.
4. `simulation_engine.py` is the application orchestrator: it dispatches ticks, invokes ledger operations, updates analytics, persists event outcomes, and opens coordination cases.
5. `state_machine.py` is the only coordination transition path. It commits the alert/audit state before publishing the corresponding SSE event.
6. Frontend telemetry is normalized once into a typed Zustand store. Agent, Ops, and Risk views select from that shared live state.

The analytical modules are pure Python. The two ledger services intentionally depend on the database session boundary because their core guarantees are transactional.

---

## 5.4 Runtime Sources of Truth

| Concern | Source of truth |
|---|---|
| Database image | `docker-compose.yml` (`postgres:16-alpine`) |
| Backend dependencies | `backend/pyproject.toml` |
| Frontend dependencies | `frontend/package-lock.json` |
| Schema and grants | `backend/infra/001_init.sql`, `002_hardening.sql` |
| API routes | `backend/app/main.py` and `backend/app/api/` |
| Verification commands | root `Makefile` |

The current frontend is Next.js 16.2.10 with React 18.3.1 and TypeScript. The backend uses FastAPI, SQLAlchemy asyncio, and asyncpg.

---

## 5.5 Supported Commands

| Command | Current behavior |
|---|---|
| `make db-up` | starts PostgreSQL and applies SQL init scripts on a fresh volume |
| `make db-down` | stops the Compose stack |
| `make backend` | runs FastAPI on port 8000 from `backend/.venv` |
| `make frontend` | runs Next.js development mode on port 3000 |
| `make scenario-a` ... `make scenario-d` | submits one real scenario through the API |
| `make test` | runs backend unit tests, frontend typecheck, and ESLint |
| `make build` | creates the production Next.js build |
| `make verify` | runs `make test` and `make build` |

For the demo, start the database, backend, and frontend in separate terminals, then trigger scenarios through the UI/API or the scenario Make targets. The simulation clock advances by 60 simulated seconds per wall-clock second.
