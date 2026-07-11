# 6. Implemented Decision-Support Capabilities

> Every capability below maps to checked-in code and observable runtime evidence. Forecasts and review signals are computed from committed events; they are not scenario answers embedded in the payload.

---

## 6.1 Backend Capabilities

### B1. Database-Enforced Provider Isolation

- PostgreSQL has separate `bkash`, `nagad`, and `rocket` schemas and corresponding application roles.
- `app_shared` has no direct provider-table privileges; each provider session authenticates with only its provider role.
- Provider identifiers pass through a fixed allowlist before they can select a schema.
- Public schema/table access is revoked before explicit grants are applied.

Evidence: `backend/infra/001_init.sql`, `backend/app/infrastructure/database.py`, and `backend/app/domain/provider/ledger.py`.

### B2. Atomic, Replay-Safe Ledger Movements

Customer transactions affect two real positions: shared physical cash and the inverse provider e-money balance. `shared.apply_provider_customer_transaction(...)` commits both movements and their audit rows in one PostgreSQL transaction. A unique transaction UUID makes a replay return the prior result without applying the movement or analytics twice.

Direct shared-cash updates use a `version_id` optimistic-lock token. Contention is retried with non-blocking jitter; exhausted or invalid work is parked in `shared.dead_letter_logs` for inspection.

### B3. Queue-Backed 60x Simulation

- One wall-clock second advances the model by 60 simulated seconds.
- Four asynchronous workers drain a bounded queue.
- Enqueued and completed outcomes are appended to `shared.simulation_events`.
- Version conflicts have bounded retries; terminal failures are durable dead-letter records.
- The clock restores the greatest persisted event/coordination watermark after restart so later audit actions do not move backward in simulation time.

This design makes the demo trace inspectable through `/v1/telemetry/events` and `/v1/coordination/dead_letter`.

### B4. Live EWMA Time-to-Exhaustion Forecast

`EWMALiquidityForecaster` consumes committed balance deltas independently for shared cash and each provider position:

```text
instantaneous_rate = -delta_bdt / elapsed_minutes
ewma_rate          = max(0, 0.35 * instantaneous_rate + 0.65 * prior_rate)
tte_minutes        = current_balance / ewma_rate
```

The forecaster retains a bounded 12-minute rate window. It returns the current balance, EWMA drain per minute, TTE, a 95% interval derived from observed rate variability, sample count, confidence, and status. When the net trend is stable or replenishing, it returns no exhaustion estimate instead of inventing one.

### B5. Unlabelled Behavioural Anomaly Detector

The detector input contains only transaction ID, event time, provider, synthetic account ID, amount, and direction. It has no scenario name, expected outcome, or anomaly flag.

Within a provider-isolated 12-minute event-time window it scores:

- dominant repeated-amount frequency and share;
- repeated-amount velocity;
- concentration across a small account cluster; and
- cadence regularity.

The output includes component scores, quantitative evidence, confidence, uncertainty, a score interval, and possible benign explanations. Crossing the threshold opens a human-review case; it does not declare wrongdoing or execute an account action.

### B6. Durable Human Coordination State Machine

The allowed lifecycle is:

```text
PENDING -> ACKNOWLEDGED -> RESOLVED
```

Illegal transitions return a conflict. Each accepted transition updates the canonical `shared.coordination_alerts` row, extends its JSONB transition history, and appends a matching `shared.simulation_events` record. Only after the transaction commits does `state_machine.py` broadcast `coordination.PENDING`, `coordination.ACKNOWLEDGED`, or `coordination.RESOLVED`.

Continuing analytical conditions reuse their active case. A resolved condition can open a new case if it later recurs.

---

## 6.2 Scenario Injectors

| Scenario | What is injected | What is computed at runtime |
|---|---|---|
| A — provider shortage | provider e-money drains paired with physical-cash credits | committed balances, EWMA drain, TTE/interval/confidence, coordination case |
| B — festival activity | ordinary and repeated-amount transactions with one uniform, unlabelled schema | atomic ledger effects, 12-minute risk score/evidence, human-review case |
| C — inconsistent feed | telemetry-only inconsistency with low feed confidence | degraded frontend state; historical balances are not rewritten |
| D — coordination drill | a real `PENDING` alert and optional acknowledgement | audited state changes and immediate SSE updates |

Scenario B account identifiers use the explicit `SYN-ACC-####` format and are not phone-number-shaped. Scenario A contains no precomputed TTE field, and the engine rejects the legacy `advisory_tte` tick kind.

---

## 6.3 Frontend Capabilities

### F1. Named SSE Telemetry Bridge

The backend emits standards-compliant named SSE events. The client registers `addEventListener(...)` handlers for snapshots, readiness, tick outcomes, and every coordination state. A connection is hydrated with a current operational snapshot before live events are applied.

Event cursors include a process epoch, so an ID from an earlier backend process cannot stall a new connection. The in-memory buffer is bounded; PostgreSQL remains the durable event record.

### F2. One Typed Live State for Three Roles

`useTelemetryStream.ts` validates and normalizes stream payloads into one typed Zustand store. The persistent role switcher selects:

- **Agent Mobile:** shared cash, three separate provider e-money positions, freshness, confidence, and recent provider activity;
- **Ops Web:** balance trajectories, EWMA evidence, open coordination cases, and explicit acknowledge/resolve actions;
- **Risk Reviewer:** ranked detector evidence, component scores, account-cluster context, confidence, and benign alternatives.

Changing role does not recreate the telemetry connection or substitute hardcoded balances.

### F3. Explainable Advisory Presentation

`AdvisoryCard` renders recommendation text beside its quantitative basis and uncertainty:

- current ledger balance;
- observed EWMA drain rate;
- predicted TTE;
- 95% interval;
- sample count; and
- confidence.

The copy asks a human to review support options. It does not move funds, restrict transactions, or make an accusation.

### F4. Safe Degraded-Feed Mode

Scenario C lowers feed confidence without mutating ledger history. `SafeFallbackLayout` visibly marks the view as uncertain and asks the operator to verify the latest balance before relying on the feed. All three role views consume the same degraded-state flag.

### F5. Provider Separation in the UI

bKash, Nagad, and Rocket retain separate labels, balances, chart series, provider tabs, and visual tokens. Shared physical cash is displayed as its own position; there is no merged-wallet total.

---

## 6.4 Measured Runtime Evidence

`GET /v1/metrics` exposes observations collected by the live process, with `null` where there is not enough evidence rather than demo defaults:

- tick success/failure counts and success rate;
- processing-latency p50 and p95;
- anomaly evaluation/detection counts and explanation coverage;
- forecast count and pending warnings; and
- matched shortage lead-time samples with min/mean/p50/p90/max.

The endpoint is JSON only. There is no separate metrics page.

---

## 6.5 Verification Map

| Claim | Automated evidence |
|---|---|
| 12-minute detector, no labels, idempotence, uncertainty | `test_anomaly_detector.py` |
| EWMA rate, dynamic TTE, confidence interval | `test_liquidity_forecaster.py` |
| real provider mutation and detector/forecast integration | `test_simulation_engine.py` |
| scenario A contains no injected forecast | `test_scenario_a.py` |
| named-stream cursor and restart behavior | `test_broadcaster.py` |
| metrics use measured values and bounded samples | `test_runtime_metrics.py` |
| frontend types, selectors, and production compilation | `npm run typecheck`, `npm run lint`, `npm run build` |

Run the complete repository gate with `make verify`.

---

## 6.6 Responsible-Design Boundary

- Signals are advisory and require human review.
- Evidence, uncertainty, and plausible benign context travel with the signal.
- Provider balances remain isolated in storage and presentation.
- No real wallet integration, autonomous money movement, or automated punitive decision exists.
- Synthetic transaction identifiers are visibly synthetic and contain no production customer data.
