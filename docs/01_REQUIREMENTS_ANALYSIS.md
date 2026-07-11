# 1. Requirements-to-Evidence Matrix

This document distinguishes requirements from current, runnable evidence. It
does not treat a design intention, a scenario parameter, or a synthetic label
as proof that a capability works.

Status vocabulary:

- **Implemented** — there is an end-to-end runtime path and a concrete way to
  inspect it.
- **Partial** — the core path exists, but a stated requirement is not yet fully
  represented or automated.
- **Out of scope** — deliberately excluded by the problem's safety boundary.

## 1.1 Product and safety boundary

The prototype supports a multi-provider agent who has one physical cash drawer
and three separate, simulated provider e-money positions. It is decision
support: it may forecast pressure, surface unusual behaviour, and open a case,
but it must not transfer money, block an account, or declare fraud.

All demo counterparties use non-phone-shaped `SYN-ACC-*` identifiers. There is
no real-wallet connector in the application. This is an implementation
boundary, not a claim of regulatory approval or production certification.

| Stakeholder | Supported task | Guardrail |
|---|---|---|
| Multi-provider agent | See shared cash and each provider position; receive a cautious shortage advisory | Provider balances remain separate; no cross-provider transfer action |
| Provider operations | Review and progress durable coordination cases | Only legal FSM transitions are accepted and all accepted transitions are audited |
| Risk reviewer | Inspect detector score, component evidence, confidence, and benign explanations | The signal says “requires human review”; it is not a fraud verdict |
| Demo operator | Inject deterministic synthetic scenarios and inspect live evidence | Scenario inputs do not constitute detector ground truth |

## 1.2 Mandatory requirements mapped to implementation

| Requirement | Status | Current evidence | Honest limitation / verification |
|---|---|---|---|
| Shared cash and provider e-money shown side-by-side and visibly separate | **Implemented** | `GET /v1/telemetry/snapshot` returns `shared_cash_balance`, `provider_balances`, and provider position metadata. `frontend/src/features/agent/AgentMobileView.tsx` renders the cash drawer separately from bKash, Nagad, and Rocket cards; `frontend/src/features/ops/OpsWebView.tsx` does the same for operations. | The prototype is seeded for one demo agent. Verify after starting the stack with `curl http://127.0.0.1:8000/v1/telemetry/snapshot`. |
| Provider isolation in storage and access | **Implemented** | `backend/infra/001_init.sql` creates `shared`, `bkash`, `nagad`, and `rocket` schemas and separate login roles, revokes peer-schema privileges, and sets role-specific search paths. `backend/app/infrastructure/database.py` validates provider IDs against a fixed allowlist and opens provider-scoped connections. | This is schema/role isolation, **not PostgreSQL row-level security**. The current code does not claim a `security_violations` table or automatic intrusion logging. Verify grants directly with `has_schema_privilege` and `has_table_privilege`. |
| Customer cash/e-money movement remains balanced and replay-safe | **Implemented** | `shared.apply_provider_customer_transaction(...)` in `backend/infra/001_init.sql` commits the cash leg, inverse provider leg, immutable transaction rows, and journal row in one database transaction. A transaction UUID makes an identical replay a no-op and rejects reuse with different inputs. `backend/app/domain/provider/ledger.py` is the application boundary. | This function is for simulated customer transactions only; it does not settle between providers. Database constraints enforce equal-and-opposite legs and non-negative balances. |
| Forward-looking liquidity estimate from live movements | **Implemented** | `backend/app/domain/liquidity/forecaster.py` computes an online EWMA drain rate, dynamic TTE, sample count, confidence score, and `ci95` interval from committed deltas. `backend/app/simulation/simulation_engine.py` feeds it the post-commit database balance and movement, while Scenario A supplies movements rather than a TTE answer. | The `ci95` value is a normal-approximation uncertainty band over the bounded rate sample, not a calibrated guarantee. A stable/replenishing position correctly reports no finite TTE. Covered by `backend/tests/test_liquidity_forecaster.py`, `test_scenario_a.py`, and `test_simulation_engine.py`. |
| Behavioural anomaly detection without source labels | **Implemented** | `backend/app/domain/risk/anomaly_detector.py` evaluates provider-isolated 12-minute windows using repeated-amount frequency/share, velocity, account clustering, and cadence. `backend/app/simulation/scenarios/scenario_b.py` emits one homogeneous transaction schema with no anomaly/ground-truth field. | Scores are advisory and algorithmic. A triggered result carries evidence, score interval, uncertainty, possible benign explanations, and `requires_human_review=true`. Covered by `backend/tests/test_anomaly_detector.py` and the engine integration test. |
| Careful, responsible language | **Implemented** | Detector results use “requires human review”; `frontend/src/features/risk/RiskReviewerView.tsx` explicitly says signals are not fraud declarations. Liquidity advice in `frontend/src/features/advisory/AdvisoryCard.tsx` asks the operator to review/verify a plan rather than moving funds. | Copy review is still required before any production use. No automated account freeze, accusation, or provider transfer is exposed. |
| Durable coordination workflow and immediate live updates | **Implemented** | `backend/app/domain/coordination/state_machine.py` enforces `PENDING -> ACKNOWLEDGED -> RESOLVED`, appends actor/time/reason transition history, mirrors each state to `shared.simulation_events`, commits, and only then broadcasts. `POST /v1/coordination/transit` performs transitions; `GET /v1/coordination/alerts` exposes cases. The advisory text names the owner and safe next step. | Owner and next step are readable evidence inside the advisory reason rather than separate columns; a larger assignment/escalation model remains an extension. For the judged human-in-loop demo, run Scenario D with `auto_ack=false` and acknowledge through the API/UI. |
| Live telemetry bridge, reconnection, and replay path | **Implemented** | `GET /v1/telemetry/stream` sends named SSE events, beginning with `snapshot` and `ready`; event IDs include a process epoch. `frontend/src/features/telemetry/useTelemetryStream.ts` registers listeners for named tick and coordination events. Durable history is available from `GET /v1/telemetry/events`. | The in-memory broadcast buffer is process-local; PostgreSQL event history is the durable recovery source. Covered by `backend/tests/test_broadcaster.py`, frontend type checking, and a live SSE curl smoke test. |
| Degraded-data fallback | **Implemented** | Scenario C emits telemetry-only inconsistency evidence without changing ledger history. `frontend/src/features/safety/SafeFallbackLayout.tsx` visibly lowers confidence and asks the user to verify the latest balance before a new cash-out. | The demonstrated fallback is driven by a synthetic inconsistent-feed event; an independent external-feed watchdog is outside this local prototype. |
| At least three measured prototype metrics | **Implemented** | `GET /v1/metrics` reports measured tick success/failure, processing-latency percentiles, detector evaluation/detection/explanation coverage, forecast counts, and shortage lead-time samples. `backend/app/domain/metrics/collector.py` returns `null` for undefined rates/percentiles instead of inventing demo values. | Metrics are process-local and reset on backend restart. Never quote a score from this document; capture the live endpoint during the judged run. Covered by `backend/tests/test_runtime_metrics.py`. |

## 1.3 Scenario evidence

| Scenario | What it proves | What it must not be used to claim |
|---|---|---|
| A — provider drain | Database balance mutation and TTE derived from committed deltas | A fixed, predetermined TTE or guaranteed forecasting accuracy |
| B — festival traffic | The detector can discover a repeated-amount/velocity/account-cluster pattern in unlabelled synthetic events | Ground-truth fraud, model precision/recall, or real customer behaviour |
| C — inconsistent feed | Low-confidence telemetry activates a safe visual fallback without rewriting balances | Automatic detection of every real feed outage |
| D — coordination | Durable case creation, legal transitions, transition audit, and named SSE updates | Automatic operational resolution or authorization from a real provider |

Scenarios are submitted with `POST /v1/simulation/scenario`. Simulation state
is visible at `GET /v1/simulation/control/state`; durable failures are visible
at `GET /v1/coordination/dead_letter`.

## 1.4 Explicitly unsupported claims

The repository does **not** currently provide evidence for any of the
following, so these must not appear in a pitch as completed features:

- real provider API or wallet integration;
- automatic settlement, conversion, blocking, or fraud determination;
- PostgreSQL row-level security, a `security_violations` table, or a PIN/OTP
  payload scanner;
- a structured escalation/assignment model, hotspot map, nearby-agent search,
  or graph investigation interface;
- a measured “100 agents under 50 ms” load-test result;
- production-grade identity, authorization, encryption/key management, data
  retention, or regulatory certification.

## 1.5 Acceptance evidence for the current prototype

Before a judged demo, use a fresh database and retain the command output for:

1. Apply `backend/infra/001_init.sql` and `002_hardening.sql` twice with
   `psql -v ON_ERROR_STOP=1` to prove rerunnability without resetting balances.
2. Query PostgreSQL privileges to prove each application role cannot use peer
   provider schemas.
3. Run `PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -v`.
4. Run `npm run typecheck`, `npm run lint`, and `npm run build` in `frontend/`.
5. Run Scenarios A–D, inspect `/v1/telemetry/snapshot`, `/v1/metrics`, and
   `/v1/coordination/dead_letter`, and capture the named events from
   `/v1/telemetry/stream`.
6. For any advisory shown, demonstrate its evidence and uncertainty, identify
   the human decision point, and confirm that no automated money movement or
   account action was offered.

Passing these checks demonstrates the implemented prototype. It does not turn
the explicitly unsupported production claims above into evidence.
