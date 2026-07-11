# 3. Database Design

The executable source of truth is [`backend/infra/001_init.sql`](../backend/infra/001_init.sql) followed by [`backend/infra/002_hardening.sql`](../backend/infra/002_hardening.sql). Both migrations are rerunnable and target PostgreSQL.

The design separates the single physical cash drawer from each provider's e-money. Isolation is enforced with schema privileges and distinct login roles. There is no cross-read role and no row-level-security policy in the current schema.

## 3.1 Schemas, roles, and tables

```text
shared
  shared_cash_ledger          current physical-cash balance and version
  shared_cash_movement        append-only physical-cash movements
  provider_customer_journal   idempotency and zero-sum transaction journal
  simulation_events           append-only queue/result/coordination history
  coordination_alerts         current FSM state plus transition history
  dead_letter_logs            exhausted, rejected, or fatal ticks

bkash                         nagad                         rocket
  provider_balance              provider_balance              provider_balance
  provider_txn                  provider_txn                  provider_txn
```

All four application roles are login roles:

- `app_shared` accesses the shared runtime tables and is the backend's default connection role.
- `app_bkash`, `app_nagad`, and `app_rocket` each access only their matching schema.
- `ledger_executor` is a constrained `NOLOGIN`, non-superuser function owner. It exists only to execute the atomic cross-ledger operation with narrowly granted table rights.

Schema ownership remains with the migration owner; application access is granted explicitly. Every custom schema is revoked from `PUBLIC`, and each login role's default `search_path` is limited to its own schema plus `pg_catalog`.

## 3.2 Logical relationships

```text
shared.shared_cash_ledger (agent_id)
           | 1
           | N
shared.shared_cash_movement
           | transaction_id (for provider/customer movements)
           |
           +------ shared.provider_customer_journal ------+
                       transaction_id                      |
                                                           |
                                  exactly one provider leg |
             +---------------------+-----------------------+
             |                     |                       |
    bkash.provider_txn    nagad.provider_txn     rocket.provider_txn

bkash.provider_balance   nagad.provider_balance   rocket.provider_balance
             \                    |                    /
              \---------- same agent_id -------------/

shared.coordination_alerts -- mirrored transitions --> shared.simulation_events
simulation worker failures --------------------------> shared.dead_letter_logs
```

The schema deliberately has no foreign keys across provider boundaries. `agent_id` is the operational partition key. For a provider/customer transaction, one UUID is present in the shared journal, shared movement, and exactly one provider transaction row; unique indexes prevent duplicate legs.

## 3.3 Atomic zero-sum customer transaction

`shared.apply_provider_customer_transaction(...)` is the only path available to `app_shared` for changing shared cash and provider e-money together. It is `SECURITY DEFINER`, owned by `ledger_executor`, uses `SET search_path = pg_catalog, pg_temp`, validates the provider against `bkash`, `nagad`, and `rocket`, and has all `PUBLIC` execution privileges revoked.

The function performs these steps in one PostgreSQL transaction:

1. Validate transaction UUID, provider, positive amount, direction, and freshness.
2. Calculate inverse legs:
   - customer cash-out: `cash_delta = -amount`, `provider_delta = +amount`;
   - customer cash-in: `cash_delta = +amount`, `provider_delta = -amount`.
3. Lock the shared balance row, then the selected provider balance row with `FOR UPDATE`. This fixed order avoids cross-provider deadlocks.
4. Look up `transaction_id` in `shared.provider_customer_journal`.
5. For an identical retry, return the original post-commit balances with `applied = false`. Reuse of the UUID with different inputs raises an error.
6. Reject either leg if it would make a balance negative.
7. Update both balance rows and increment both versions.
8. Insert the shared movement, provider transaction, and canonical journal row.

The journal enforces the accounting invariant in DDL:

```text
cash_delta_bdt + provider_delta_bdt = 0
abs(cash_delta_bdt) = abs(provider_delta_bdt) = amount_bdt
```

The function call either commits all balance and audit changes or rolls all of them back. Analytics are advanced only when the function reports `applied = true`, so retries do not duplicate forecast or anomaly evidence.

## 3.4 Other balance mutations

Provider-drain simulation ticks authenticate as the selected provider role and update only that provider's `provider_balance`, with a non-negative-balance predicate and version increment. They cannot read or mutate another provider schema.

Direct shared-cash credits and deductions use `shared.shared_cash_ledger.version_id` as an optimistic-lock token under `REPEATABLE READ`. A successful update appends `shared.shared_cash_movement`; a concurrent loser retries with bounded jitter.

## 3.5 Privilege matrix

| Principal | Shared tables | Own provider balance | Own provider transactions | Other provider schemas | Atomic function |
|---|---|---|---|---|---|
| `app_shared` | Runtime-specific grants | No direct access | No direct access | No access | Execute |
| `app_bkash` | No access | Select/insert/update bKash | Select bKash | No access | No access |
| `app_nagad` | No access | Select/insert/update Nagad | Select Nagad | No access | No access |
| `app_rocket` | No access | Select/insert/update Rocket | Select Rocket | No access | No access |
| `ledger_executor` | Only function-required rights | Select/update all three | Insert all three | Function-required only | Owner; cannot log in |
| `PUBLIC` | None on custom schemas/function | None | None | None | None |

`app_shared` receives:

- select/insert/update on `shared.shared_cash_ledger`;
- select/insert on `shared.shared_cash_movement` and `shared.simulation_events`;
- select/insert on `shared.dead_letter_logs`;
- select/insert/update on `shared.coordination_alerts`;
- sequence usage required by those inserts.

Provider roles receive no insert, update, or delete privilege on `provider_txn`; the atomic function appends those rows. Cross-schema schema, table, and sequence privileges are explicitly revoked on every migration run, cleaning up stale development grants as well as establishing fresh-cluster security.

## 3.6 Constraints and indexes

- All balances and safety buffers must be non-negative.
- All movement amounts are positive at the API boundary; the journal legs encode direction by sign.
- Providers are restricted to `bkash`, `nagad`, or `rocket` wherever a provider ID is stored.
- Provider transaction direction is `in` or `out`; freshness is `fresh`, `degraded`, `stale`, or `conflicting`.
- `provider_txn.transaction_id`, `shared_cash_movement.transaction_id`, and `provider_customer_journal.transaction_id` are unique idempotency keys.
- Ledger version values must be positive.
- Coordination status is exactly `PENDING`, `ACKNOWLEDGED`, or `RESOLVED`; `transitions` must be a JSON array.
- Dead-letter retry counts cannot be negative.
- Time/agent, counterparty/time, status, and transaction-ID indexes support telemetry, audit, detector, and operator queries.

## 3.7 Durable event and coordination records

`shared.simulation_events` stores the simulation time, event type, optional agent/provider IDs, and JSON payload for every persisted lifecycle event. Tick rows use names such as `tick.enqueued`, `tick.done`, `tick.dead_letter`, and `tick.fatal`. Coordination transitions use `coordination.PENDING`, `coordination.ACKNOWLEDGED`, and `coordination.RESOLVED`.

`shared.coordination_alerts` holds one row per alert token. Its `status` is the current FSM state; `transitions` is the append-only JSON audit sequence. The state-machine transaction updates this row and inserts the corresponding simulation event before the live SSE broadcast occurs.

`shared.dead_letter_logs` is the durable sink for ticks that cannot be completed. It retains the tick ID, agent/provider, simulation time, kind, payload, retry count, and final error.

Runtime metrics are intentionally process-local bounded measurements exposed by `GET /v1/metrics`; there is no `shared.metrics` table.

## 3.8 Idempotent migration behavior

`001_init.sql` is ordered so roles exist before any grant or revoke. It uses guarded role creation, `IF NOT EXISTS` for extensions, schemas, tables, columns, and indexes, `CREATE OR REPLACE FUNCTION` for the atomic boundary, and `ON CONFLICT DO NOTHING` for demo seeds. Reapplying it never resets live balances.

`002_hardening.sql` independently guards creation of the shared role and schema, creates coordination and dead-letter tables and indexes if absent, removes broad legacy DML grants, then reapplies the narrow runtime grants.

Run both with fail-fast behavior:

```bash
psql -v ON_ERROR_STOP=1 -f backend/infra/001_init.sql
psql -v ON_ERROR_STOP=1 -f backend/infra/002_hardening.sql
```

The checked-in passwords are local demo defaults. Runtime connection settings can be supplied through the database environment variables documented in `backend/.env.example`.
