# Runtime API Contract

This document describes the implemented FastAPI service. The generated
OpenAPI document at `GET /openapi.json` and Swagger UI at `GET /docs` are the
machine-readable authorities.

The API is a synthetic hackathon prototype: it has no real-wallet connector,
no transfer/blocking endpoint, and no automated fraud-decision endpoint.

## Conventions

| Item | Runtime contract |
|---|---|
| Base path | `/v1` except `GET /healthz` |
| Content type | JSON; telemetry stream is `text/event-stream` |
| Time | Timezone-aware ISO-8601 strings |
| Providers | Exact allowlist: `bkash`, `nagad`, `rocket` |
| Money | JSON numbers in BDT; PostgreSQL stores `NUMERIC(14,2)` |
| IDs | UUID strings for ticks, transactions, agents, and alerts |
| Validation errors | Standard FastAPI `422` response |

Authentication is not implemented in this local demo build. Do not expose it
to an untrusted network without adding identity, authorization, rate limits,
and secret management.

## Health

### `GET /healthz`

```json
{"ok": true, "engine_running": true}
```

`ok` is a live database probe. `engine_running` indicates that simulation
workers are active.

## Simulation

### `POST /v1/simulation/scenario`

Queues one synthetic scenario and returns HTTP 200.

```json
{
  "scenario": "A",
  "params": {"providers": ["nagad"]}
}
```

`scenario` is one of `A`, `B`, `C`, or `D`.

- A queues actual provider drains and matching shared-cash credits. TTE is not
  accepted as input; the engine derives it from committed deltas.
- B queues ordinary transaction-shaped records with no anomaly/label/truth
  field. Its directions are planned against current live debit budgets so
  concurrent workers cannot overdraw a ledger.
- C queues a telemetry-only inconsistency with reduced confidence and does not
  rewrite balance history.
- D raises a coordination case; `auto_ack` defaults to true and can be set
  false for a human-driven walkthrough.

### Simulation controls

| Endpoint | Result |
|---|---|
| `POST /v1/simulation/control/start` | Start workers and the 60× pump; idempotent. |
| `POST /v1/simulation/control/stop` | Stop workers and the pump. |
| `POST /v1/simulation/control/pause` | Pause worker/pump progress. |
| `POST /v1/simulation/control/resume` | Resume progress. |
| `GET /v1/simulation/control/state` | Return `sim_time`, `queue_depth`, durable dead-letter count, and pause state. |

At startup, `sim_time` is restored from the durable event/coordination
watermark, so a process restart cannot backdate later audit actions.

### `POST /v1/simulation/tick`

Queues a validated manual tick. `kind` is one of `cash_out`, `cash_in`,
`inconsistency`, or `noop`; `payload` is an object. Provider customer
transactions are generated through the scenarios and always use the atomic
cross-ledger boundary.

## Telemetry

### `GET /v1/telemetry/snapshot`

Hydrates the frontend from current database state:

```json
{
  "agent_id": "00000000-0000-0000-0000-000000000001",
  "sim_time": "2026-07-11T09:00:00+00:00",
  "shared_cash_balance": 500000.0,
  "shared_cash_version": 1,
  "provider_balances": {
    "bkash": 120000.0,
    "nagad": 30000.0,
    "rocket": 90000.0
  },
  "provider_positions": {},
  "alerts": []
}
```

`provider_positions` contains a separate balance, version, and update time for
each provider. The API never returns a merged provider-wallet balance.

### `GET /v1/telemetry/stream`

Returns named Server-Sent Events. Every connection receives `snapshot` and
`ready` first, followed by committed events such as:

- `tick.enqueued`, `tick.done`, `tick.dead_letter`, `tick.fatal`
- `coordination.PENDING`
- `coordination.ACKNOWLEDGED`
- `coordination.RESOLVED`

Frame example:

```text
id: 84b49c870f824823abba90e94e07ec18:132
event: coordination.RESOLVED
data: {"id":132,"sim_time":"...","event_type":"coordination.RESOLVED","payload":{...}}
```

The SSE `id` is an opaque `process-epoch:sequence` cursor. IDs from a previous
backend process are recognized as stale instead of stalling reconnection. A
snapshot boundary prevents older buffered events from overwriting current
state, while events arriving during hydration are delivered afterwards.

### `GET /v1/telemetry/events`

Reads the append-only PostgreSQL event history. Optional query parameters:

- `since`: return rows with `sim_time > since`;
- `limit`: 1–1000, default 200.

This is the durable audit/replay interface; SSE is the low-latency bridge.

## Coordination

The only legal lifecycle is:

```text
PENDING -> ACKNOWLEDGED -> RESOLVED
```

### `POST /v1/coordination/transit`

```json
{
  "alert_token": "fd008dc1-37fd-4be6-acd2-c1bcc5049b53",
  "to": "ACKNOWLEDGED",
  "actor": "field_officer_demo",
  "reason": "Evidence reviewed"
}
```

Returns the canonical alert and complete transition list. An illegal or
skipped transition returns `409`; an unknown token returns `404`. The alert
row and durable event row commit before the named SSE broadcast.

### Coordination reads

| Endpoint | Result |
|---|---|
| `GET /v1/coordination/alerts` | Latest alerts; optional `status` and `limit`. |
| `GET /v1/coordination/dead_letter` | Durable processing failures; optional `limit`. |

## Measured metrics

### `GET /v1/metrics`

Returns measurements accumulated in the current API process:

- processing latency sample count, p50, and p95;
- tick total/success/failure counts and success rate;
- anomaly evaluation/detection counts and explanation coverage;
- forecast count and pending/observed/matched shortage counts;
- observed shortage lead-time distribution.

Undefined ratios and percentiles are `null`; the endpoint does not invent
defaults. It intentionally does not report precision/recall because Scenario B
contains no ground-truth labels.

## Ledger guarantees behind the API

Customer exchanges use a global transaction UUID. One constrained
`SECURITY DEFINER` PostgreSQL function commits all of the following or none:

1. shared physical-cash update and version increment;
2. equal-and-opposite provider e-money update and version increment;
3. provider transaction row;
4. shared cash movement row; and
5. a zero-sum idempotency journal row.

An exact retry returns `applied=false` with the original committed result. Reuse
of the UUID with different request content is rejected. `app_shared` retains
no direct provider-schema privilege, and provider roles cannot bypass the
function by inserting transaction rows directly.
