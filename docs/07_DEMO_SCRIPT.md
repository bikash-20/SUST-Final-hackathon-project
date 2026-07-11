# Live Demo Script

This is a reliable four-to-five-minute walkthrough of the current build. Use a
fresh synthetic demo database when rehearsing if previous scenarios have
already changed the seeded balances.

## 1. Preflight

Start PostgreSQL, the API, and the web app in separate terminals:

```bash
make db-up
make backend
make frontend
```

Confirm both services before presenting:

```bash
curl -fsS http://localhost:8000/healthz
curl -fsS http://localhost:8000/v1/telemetry/snapshot
```

Open `http://localhost:3000`. Keep one terminal visible for the scenario
commands. The engine advances one simulated minute per wall-clock second, but
queued scenario events are processed asynchronously, so narrate observable
state rather than promising exact wall-clock timings.

## 2. Baseline — separated money positions

Start in **Agent Mobile** and point out:

- the live shared physical-cash drawer;
- three distinct bKash, Nagad, and Rocket e-money cards;
- freshness and confidence text sourced from telemetry; and
- no aggregate “wallet” balance.

Switch to **Ops Web** without reloading. The same mounted SSE connection feeds
the role views.

Suggested line: “Shared shop cash and provider e-money are different assets,
stored behind different PostgreSQL roles and shown separately here.”

## 3. Scenario A — provider liquidity pressure

Run:

```bash
make scenario-a
```

Observe in **Ops Web**:

- Nagad e-money decreases from committed provider-ledger movements;
- shared physical cash increases for the corresponding customer cash-in
  service;
- the forecast table fills with EWMA drain rate, sample count, confidence,
  computed TTE, and a 95% interval; and
- a high-severity coordination case appears if the computed forecast reaches
  the critical threshold.

The scenario sends amounts and timestamps, not a forecast. There is no fixed
9.5-minute value in the scenario or UI.

Suggested line: “The total shop position can look comfortable while one
provider rail approaches exhaustion. Every displayed estimate comes from
committed deltas.”

## 4. Scenario B — unlabelled behavioural detection

Run:

```bash
make scenario-b
```

Switch to **Risk Reviewer** and show the triggered evidence rows:

- a 12-minute provider-isolated lookback;
- observed outgoing frequency and velocity;
- repeated amount frequency and concentration;
- distinct account clustering and cadence regularity;
- risk score, confidence, and possible benign explanations.

Scenario B queues production-shaped synthetic transactions with no anomaly or
expected-result field. The detector independently decides whether the evidence
crosses its review threshold. Its output requests human review; it does not
freeze an account or call a user fraudulent.

Suggested line: “The generator cannot tell the detector which records are
special. The reviewer sees the measurable behaviour and uncertainty, including
benign context, before making a decision.”

## 5. Scenario D — audited human coordination

Run Scenario D before Scenario C so the final degraded-state demonstration does
not obscure the coordination controls:

```bash
make scenario-d
```

In **Ops Web**, find the new `PENDING` case, then:

1. Click **Acknowledge**. The only valid next state is `ACKNOWLEDGED`.
2. Click **Resolve**. The only valid next state is `RESOLVED`.
3. Point out the growing audited-transition count and actor.

The API commits the alert row and event-history row before broadcasting the
named `coordination.PENDING`, `coordination.ACKNOWLEDGED`, or
`coordination.RESOLVED` SSE event. Invalid or skipped transitions return a
conflict instead of changing state.

Suggested line: “Analytics creates evidence; a named human workflow owns the
decision and leaves an audit trail.”

## 6. Scenario C — uncertainty-safe fallback

Run this last among the UI scenarios because degraded mode deliberately remains
visible for the active demo session:

```bash
make scenario-c
```

Show the Bangla/Banglish uncertainty banner and the reported confidence of
`0.42`. The inconsistency tick changes telemetry presentation only; it does not
rewrite cash or provider ledger history. The guidance asks the operator to
verify the latest balance and does not invent a replacement TTE.

Suggested line: “When feed quality is insufficient, the interface becomes
more cautious and tells the human what needs verification.”

## 7. Measured evidence — no metrics mock page

The project exposes JSON metrics, not a separate `/metrics/page` UI:

```bash
curl -fsS http://localhost:8000/v1/metrics
```

Point out values accumulated during this process run:

- processing latency p50/p95 and sample count;
- tick success/failure count and measured success rate;
- anomaly evaluation, detection, and explanation-coverage counts;
- forecast count; and
- observed shortage lead-time samples when a warning is followed by a
  critical/exhausted position.

Undefined percentages and percentiles remain `null`; the endpoint does not
manufacture friendly demo values. It does not report precision or recall
because Scenario B intentionally contains no ground-truth labels.

For a protocol-level SSE proof, run this in another terminal while firing a
scenario:

```bash
curl -N http://localhost:8000/v1/telemetry/stream
```

Point out the explicit `event:` lines for `snapshot`, `ready`, `tick.done`, and
coordination transitions.

## 8. Close

“We did not move a real taka, block an account, or merge provider balances. We
turned synthetic operational signals into explainable forecasts, cautious
review evidence, and an audited human response.”

## Judge Q&A

| Question | Evidence-backed answer |
|---|---|
| How is provider isolation enforced? | Separate schemas/login grants and a fixed provider whitelist. `app_shared` has no direct provider-table privileges; customer exchanges execute one constrained `NOLOGIN`-owned function that atomically records equal-and-opposite cash/e-money legs. |
| Where does TTE come from? | A 12-minute rolling rate window and EWMA over committed ledger deltas. The response includes drain rate, samples, confidence, and a calculated 95% interval. |
| Is the anomaly detector using injected truth? | No. Its input type has transaction ID, event time, provider, account, amount, and direction only. Scenario metadata cannot enter the score. |
| Why use explainable statistics instead of opaque ML? | The deterministic features are reproducible, auditable, and appropriate for a human-review prototype. |
| Is customer data used? | No. Scenario identifiers are synthetic and deliberately not phone-number-shaped. |
| What happens when data quality falls? | The frontend exposes uncertainty and verification guidance; the inconsistency event does not mutate historical balances. |
| How is coordination audited? | The canonical alert stores its transition history, each transition is also appended to the durable simulation-event stream, and only legal FSM transitions are accepted. |
