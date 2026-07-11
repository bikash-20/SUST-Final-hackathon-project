# Multi-Provider Decision Support — Documentation

This repository contains a runnable, synthetic-data-only prototype for the
bKash presents SUST CSE Carnival 2026 hackathon. It helps one mobile-money
agent and human operations teams inspect shared physical cash, separate
provider e-money positions, liquidity pressure, and unusual transaction
behaviour.

The prototype does not connect to real wallets, transfer funds, block an
account, or declare fraud.

## Implemented system

```text
synthetic scenarios
        |
        v
queue-backed 60x simulation engine
        |
        +--> shared cash ledger (app_shared role)
        +--> bKash ledger      (app_bkash role)
        +--> Nagad ledger      (app_nagad role)
        +--> Rocket ledger     (app_rocket role)
        |
        +--> 12-minute EWMA liquidity forecast
        +--> 12-minute behavioural anomaly detector
        +--> audited coordination state machine
        |
        v
PostgreSQL event history + named SSE events
        |
        +--> Agent Mobile
        +--> Ops Web
        +--> Risk Reviewer
```

The database login used for shared state has no direct privileges on provider
schemas. Provider reads and upstream balance drains use provider-specific
authenticated sessions. Customer exchanges pass through a fixed-provider,
`SECURITY DEFINER` ledger function owned by a constrained `NOLOGIN` role; its
zero-sum journal, shared-cash leg, inverse provider leg, and audit rows commit
in one PostgreSQL transaction with a global idempotency UUID.

The anomaly detector receives ordinary transaction fields only. It calculates
review evidence from frequency, repeated-amount concentration, velocity,
account clustering, and cadence; Scenario B does not pass a ground-truth
label. Liquidity TTE is calculated from committed balance deltas with an EWMA
rate and a rolling uncertainty interval; scenarios do not inject a TTE value.

## Runtime interfaces

| Interface | Purpose |
|---|---|
| `POST /v1/simulation/scenario` | Queue Scenario A, B, C, or D. |
| `GET /v1/simulation/control/state` | Inspect simulation time and queue state. |
| `GET /v1/telemetry/snapshot` | Hydrate current shared and provider-separated balances and cases. |
| `GET /v1/telemetry/stream` | Receive named SSE events such as `snapshot`, `tick.done`, and `coordination.PENDING`. |
| `GET /v1/telemetry/events` | Read persisted event history after a timestamp. |
| `GET /v1/coordination/alerts` | Inspect durable alert state and transition history. |
| `POST /v1/coordination/transit` | Apply a valid human-owned acknowledgement or resolution. |
| `GET /v1/coordination/dead_letter` | Inspect ticks parked after processing failure. |
| `GET /v1/metrics` | Return measured runtime latency, reliability, explanation coverage, forecast, and lead-time evidence. |

FastAPI also publishes the live OpenAPI explorer at
`http://localhost:8000/docs`.

## Run and verify

Use the root [`README.md`](../README.md) for clean-machine setup. The shortest
workflow after dependencies are installed is:

```bash
make db-up
make backend     # terminal 1
make frontend    # terminal 2
make verify
```

Then open `http://localhost:3000` and follow
[`07_DEMO_SCRIPT.md`](./07_DEMO_SCRIPT.md).

## Document map

| Document | Role |
|---|---|
| [`01_REQUIREMENTS_ANALYSIS.md`](./01_REQUIREMENTS_ANALYSIS.md) | Original requirements and safety-boundary analysis. |
| [`02_DATA_FLOW_AND_STATE_MACHINES.md`](./02_DATA_FLOW_AND_STATE_MACHINES.md) | Design rationale for scenario flows and state machines. |
| [`03_DATABASE_DESIGN.md`](./03_DATABASE_DESIGN.md) | Database design rationale; executable DDL lives in `backend/infra`. |
| [`04_API_CONTRACT.md`](./04_API_CONTRACT.md) | Implemented REST/SSE contract; generated FastAPI OpenAPI remains machine authority. |
| [`05_MONOREPO_LAYOUT.md`](./05_MONOREPO_LAYOUT.md) | Original repository layout proposal. |
| [`06_DOMAIN_INJECTIONS.md`](./06_DOMAIN_INJECTIONS.md) | Architectural design notes and extension points. |
| [`07_DEMO_SCRIPT.md`](./07_DEMO_SCRIPT.md) | Commands and observable evidence for the current build. |

Documents 01–03 and 05–06 preserve design history and may describe proposed
extensions. Document 04, the executable migrations, generated OpenAPI schema,
tests, and current source code describe implemented behaviour.
