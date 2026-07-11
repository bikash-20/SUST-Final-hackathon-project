# 2. Data Flow and State Machines

This document describes the implemented runtime. The durable source of truth is PostgreSQL; the in-process queue, analytics state, metrics, and SSE broadcaster are delivery and computation layers around it.

## 2.1 End-to-end flow

```text
POST /v1/simulation/scenario or /tick
                   |
                   v
       validate provider and input
                   |
                   v
       bounded asyncio.Queue (10,000)
                   |
       tick.enqueued is appended to
       shared.simulation_events
                   |
                   v
       four asynchronous workers
                   |
        +----------+-------------------+
        |                              |
        v                              v
 provider/customer transaction     provider drain or
 shared SECURITY DEFINER call      shared-cash movement
        |                              |
        +--------------+---------------+
                       v
              committed ledger state
                       |
          +------------+-------------+
          |                          |
          v                          v
  12-minute EWMA forecast    12-minute anomaly window
  from committed deltas      from transaction behaviour
          |                          |
          +------------+-------------+
                       v
        advisory condition (when thresholds are met)
                       |
                       v
        coordination FSM commits alert + audit event
                       |
                       v
       tick.done and coordination.* broadcasts
                       |
          +------------+-------------+
          |                          |
          v                          v
 REST snapshot/event history     named SSE stream
          |                          |
          +------------+-------------+
                       v
       responsive Next.js role views
       (agent, operations, risk reviewer)
```

Provider/customer transactions use one database function to update shared physical cash, the inverse provider e-money position, and their audit rows in one transaction. Provider-drain ticks use the selected provider role; shared cash ticks use the shared aggregate root.

Online analytics run only after a ledger mutation commits. An idempotent replay of a provider/customer transaction returns the original committed balances and is not counted again by the forecaster or detector.

## 2.2 Simulation clock, queue, and durability

- The wall-clock pump advances simulation time by 60 simulated seconds once per wall-clock second.
- Scenario events are placed on a bounded `asyncio.Queue` and consumed by four workers.
- Scenario enqueue records `tick.enqueued` in `shared.simulation_events` before returning. Completion records `tick.done` with the domain result.
- Shared-cash optimistic-lock conflicts receive bounded jittered retries. Insufficient balances and exhausted or fatal processing failures are persisted in `shared.dead_letter_logs`; they are not retained only in memory.
- The engine restores its simulation-time watermark from durable simulation and coordination rows on startup, so a restart cannot move audit time backward.
- `GET /v1/telemetry/events?since=<timestamp>` exposes the append-only event history for durable catch-up. SSE is the low-latency delivery path, not the sole record.

## 2.3 Scenario flows

### Scenario A: hidden provider shortage

Scenario A schedules real provider e-money deductions and paired shared-cash credits. Each committed provider deduction is passed to the EWMA forecaster. The forecaster derives the drain rate, time to exhaustion, 95% interval, confidence, and status from the live balance and delta history; the scenario does not supply a TTE.

```text
provider_drain -> provider-scoped balance UPDATE -> committed balance
                                                 -> EWMA forecast
                                                 -> critical/exhausted?
                                                 -> PENDING coordination alert

cash_in       -> optimistic shared-cash UPDATE  -> committed balance
                                                 -> shared-cash EWMA forecast
```

An active liquidity condition reuses its durable `PENDING` or `ACKNOWLEDGED` alert. After that alert is resolved, a later recurrence can open a new alert.

### Scenario B: liquidity pressure and unusual activity

Scenario B mixes ordinary festival transactions with repeated BDT 4,999 transactions across synthetic account IDs. Every generated item has the same production-shaped fields. There is no anomaly flag, ground-truth label, expected outcome, or alternate stream supplied to the detector.

For each newly committed provider/customer transaction:

1. PostgreSQL atomically moves shared cash and inverse provider e-money.
2. The shared-cash EWMA receives the committed delta.
3. The detector evaluates only that provider's trailing 12-minute event-time window.
4. Its score combines repeated-amount frequency and share, velocity, account clustering, and cadence regularity.
5. A threshold crossing creates an explainable, human-review coordination alert. It never blocks an account or declares fraud.

Detector output includes quantitative evidence, risk score, confidence, uncertainty, a score interval, and possible benign explanations.

### Scenario C: degraded or inconsistent feed

Scenario C emits an `inconsistency` telemetry result with reduced confidence. It does not change shared cash, provider balances, or historical transactions. The frontend can therefore show degraded-data guidance without presenting a fabricated balance change.

### Scenario D: coordinated response

Scenario D creates a durable `PENDING` alert. With `auto_ack=true` (the default demo setting), it immediately performs the legal transition to `ACKNOWLEDGED`; otherwise it remains pending. It then emits a coordination-awaiting tick for the live view. Resolution is a human/API action through `POST /v1/coordination/transit`.

## 2.4 Canonical coordination FSM

```text
        raise_alert()
             |
             v
        +---------+
        | PENDING |
        +----+----+
             | ACKNOWLEDGED
             v
     +----------------+
     | ACKNOWLEDGED   |
     +--------+-------+
              | RESOLVED
              v
        +----------+
        | RESOLVED |  terminal
        +----------+
```

The only legal transitions are:

| Current state | Allowed next state |
|---|---|
| `PENDING` | `ACKNOWLEDGED` |
| `ACKNOWLEDGED` | `RESOLVED` |
| `RESOLVED` | none |

The state machine locks the alert row with `SELECT ... FOR UPDATE`, rejects illegal transitions, appends `{from, to, at, by, reason}` to the row's `transitions` JSON array, and mirrors the accepted transition to `shared.simulation_events`. Only after the database transaction commits does it broadcast `coordination.PENDING`, `coordination.ACKNOWLEDGED`, or `coordination.RESOLVED` to SSE subscribers. The API maps an invalid transition to HTTP 409 and an unknown token to HTTP 404.

## 2.5 Named SSE protocol

`GET /v1/telemetry/stream` uses standard Server-Sent Events. The event name is meaningful; clients register custom `EventSource.addEventListener(...)` handlers rather than relying only on `onmessage`.

Implemented event names are:

- `snapshot` and `ready` during connection hydration;
- `tick.enqueued`, `tick.done`, `tick.dead_letter`, and `tick.fatal` for simulation processing;
- `coordination.PENDING`, `coordination.ACKNOWLEDGED`, and `coordination.RESOLVED` for alert state.

Each event ID is `<process-epoch>:<sequence>`. A connection captures a broadcaster high-water mark, receives a current operational snapshot first, then receives events created after that boundary. A stale ID from an earlier backend process is ignored safely. A comment heartbeat is emitted after 15 seconds of silence, and the stream exits when the request disconnects.

The initial snapshot contains the shared cash balance, three provider-separated balances and positions, simulation time, and up to 100 recent coordination alerts. The in-process broadcaster has a bounded 1,024-event buffer; durable history remains in `shared.simulation_events`.

## 2.6 Runtime state ownership

| State | Authoritative location | Notes |
|---|---|---|
| Shared physical cash | `shared.shared_cash_ledger` | Versioned; movements are append-only |
| Provider e-money | `<provider>.provider_balance` | Accessed with the matching provider role |
| Customer transaction audit | `shared.provider_customer_journal`, `shared.shared_cash_movement`, `<provider>.provider_txn` | One transaction UUID links the zero-sum legs |
| Simulation history | `shared.simulation_events` | Append-only REST/SSE source |
| Coordination state/audit | `shared.coordination_alerts` | Current status plus append-only JSON transitions |
| Exhausted/fatal work | `shared.dead_letter_logs` | Durable operator inspection |
| EWMA and anomaly windows | Process memory | Rebuilt by a new run; outputs are carried in durable tick results |
| Runtime metrics | Process memory | Bounded samples and cumulative counters at `GET /v1/metrics` |
| Live delivery buffer | Process memory | Bounded and process-epoch scoped; never the durable source |

## 2.7 Failure handling

| Failure | Implemented outcome |
|---|---|
| Shared cash write loses an optimistic-lock race | Jittered bounded retry; then durable dead letter |
| Cash or provider balance would become negative | Mutation rejected and tick written to dead letter |
| Unexpected worker exception | Tick written to dead letter and `tick.fatal` persisted/broadcast |
| Browser disconnects | SSE generator closes; reconnect begins with a fresh snapshot |
| Backend restarts | New SSE epoch; durable simulation-time watermark is restored |
| Provider feed is inconsistent | Telemetry-only degraded result; no historical ledger mutation |
| Illegal coordination transition | No state change; HTTP 409 |

## 2.8 Implemented endpoints in this flow

- `POST /v1/simulation/scenario`
- `POST /v1/simulation/tick`
- `POST /v1/simulation/control/{start|stop|pause|resume}`
- `GET /v1/simulation/control/state`
- `GET /v1/telemetry/snapshot`
- `GET /v1/telemetry/events`
- `GET /v1/telemetry/stream`
- `GET /v1/coordination/alerts`
- `POST /v1/coordination/transit`
- `GET /v1/coordination/dead_letter`
- `GET /v1/metrics`
