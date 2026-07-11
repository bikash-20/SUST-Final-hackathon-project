-- =====================================================================
-- 001_init.sql -- initial, rerunnable PostgreSQL schema
--
-- Security invariants:
--   * shared physical cash and each provider's e-money live in separate
--     schemas.
--   * each login role receives privileges only in its own schema.
--   * provider transaction rows are appended only by a constrained atomic
--     ledger function; provider application roles receive read-only history.
--
-- Concurrency invariant:
--   shared.shared_cash_ledger.version_id is an optimistic-lock token. A
--   writer must include the version it read in its UPDATE predicate.
--
-- This file is safe to run repeatedly. Seed rows use ON CONFLICT DO
-- NOTHING so replaying a migration never resets live balances.
-- =====================================================================

-- gen_random_uuid() is used by the coordination migration. PostgreSQL 15
-- exposes it in core, while pgcrypto keeps this migration compatible with
-- older supported clusters.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------
-- 1. Application roles. Roles must exist before any GRANT or REVOKE.
-- ---------------------------------------------------------------------
DO $roles$
DECLARE
    role_name      TEXT;
    role_password  TEXT;
BEGIN
    FOR role_name, role_password IN
        SELECT *
          FROM (VALUES
                ('app_shared', 'change_me_shared'),
                ('app_bkash',  'change_me_bkash'),
                ('app_nagad',  'change_me_nagad'),
                ('app_rocket', 'change_me_rocket')
          ) AS configured_roles(name, password)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = role_name
        ) THEN
            EXECUTE format(
                'CREATE ROLE %I LOGIN PASSWORD %L',
                role_name,
                role_password
            );
        END IF;
    END LOOP;
END
$roles$;

-- The atomic ledger function runs as a dedicated, non-login owner rather
-- than as the migration superuser.  It receives only the exact table rights
-- required below and is never granted to an application role.
DO $ledger_role$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'ledger_executor'
    ) THEN
        CREATE ROLE ledger_executor
            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS;
    END IF;
END
$ledger_role$;

-- PostgreSQL 16+ no longer gives a CREATEROLE user SET permission on roles
-- it creates. Grant temporary membership so the migration can create and
-- replace the SECURITY DEFINER function as its constrained NOLOGIN owner.
-- The membership is revoked after the routine grants are configured.
GRANT ledger_executor TO CURRENT_USER;

-- ---------------------------------------------------------------------
-- 2. Schemas (one per provider plus the shared physical-cash domain).
-- ---------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS shared;
CREATE SCHEMA IF NOT EXISTS bkash;
CREATE SCHEMA IF NOT EXISTS nagad;
CREATE SCHEMA IF NOT EXISTS rocket;

-- Custom schemas should never be reachable through PUBLIC privileges.
REVOKE ALL PRIVILEGES ON SCHEMA shared, bkash, nagad, rocket FROM PUBLIC;

-- ---------------------------------------------------------------------
-- 3. Shared cash ledger and append-only movement audit.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shared.shared_cash_ledger (
    agent_id        UUID            NOT NULL,
    balance_bdt     NUMERIC(14,2)   NOT NULL CHECK (balance_bdt >= 0),
    safety_buffer   NUMERIC(14,2)   NOT NULL DEFAULT 0
                                      CHECK (safety_buffer >= 0),
    version_id      INTEGER         NOT NULL DEFAULT 1
                                      CHECK (version_id > 0),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id)
);

CREATE TABLE IF NOT EXISTS shared.shared_cash_movement (
    id              BIGSERIAL       PRIMARY KEY,
    transaction_id  UUID,
    agent_id        UUID            NOT NULL,
    delta_bdt       NUMERIC(14,2)   NOT NULL,
    reason          TEXT            NOT NULL,
    sim_time        TIMESTAMPTZ     NOT NULL,
    version_after   INTEGER         NOT NULL CHECK (version_after > 0),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);
ALTER TABLE shared.shared_cash_movement
    ADD COLUMN IF NOT EXISTS transaction_id UUID;
CREATE INDEX IF NOT EXISTS idx_shared_cash_movement_agent_time
    ON shared.shared_cash_movement (agent_id, sim_time DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_shared_cash_movement_transaction
    ON shared.shared_cash_movement (transaction_id)
    WHERE transaction_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- 4. Durable, append-only simulation stream consumed by REST/SSE.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shared.simulation_events (
    id              BIGSERIAL       PRIMARY KEY,
    sim_time        TIMESTAMPTZ     NOT NULL,
    event_type      TEXT            NOT NULL,
    agent_id        UUID,
    provider_id     TEXT
                                      CHECK (provider_id IS NULL OR provider_id IN
                                             ('bkash', 'nagad', 'rocket')),
    payload         JSONB           NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sim_events_time
    ON shared.simulation_events (sim_time DESC);
CREATE INDEX IF NOT EXISTS idx_sim_events_agent_time
    ON shared.simulation_events (agent_id, sim_time DESC);

-- ---------------------------------------------------------------------
-- 5. Per-provider aggregate roots. The schemas deliberately have the
--    same table shape, but no cross-provider foreign keys or grants.
-- ---------------------------------------------------------------------
DO $provider_tables$
DECLARE
    provider_name TEXT;
BEGIN
    FOR provider_name IN
        SELECT unnest(ARRAY['bkash', 'nagad', 'rocket'])
    LOOP
        EXECUTE format($ddl$
            CREATE TABLE IF NOT EXISTS %I.provider_balance (
                agent_id        UUID            NOT NULL,
                balance_bdt     NUMERIC(14,2)   NOT NULL
                                                  CHECK (balance_bdt >= 0),
                version_id      INTEGER         NOT NULL DEFAULT 1
                                                  CHECK (version_id > 0),
                updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
                PRIMARY KEY (agent_id)
            )
        $ddl$, provider_name);

        EXECUTE format($ddl$
            CREATE TABLE IF NOT EXISTS %I.provider_txn (
                id                  BIGSERIAL       PRIMARY KEY,
                transaction_id      UUID            NOT NULL,
                agent_id            UUID            NOT NULL,
                counterparty_msisdn  TEXT            NOT NULL,
                amount_bdt          NUMERIC(14,2)   NOT NULL
                                                      CHECK (amount_bdt > 0),
                direction           TEXT            NOT NULL
                                                      CHECK (direction IN ('in', 'out')),
                freshness           TEXT            NOT NULL DEFAULT 'fresh'
                                                      CHECK (freshness IN
                                                             ('fresh', 'degraded',
                                                              'stale', 'conflicting')),
                sim_time            TIMESTAMPTZ     NOT NULL,
                created_at          TIMESTAMPTZ     NOT NULL DEFAULT now()
            )
        $ddl$, provider_name);

        -- Existing development databases predate the idempotency key.  Keep
        -- the migration rerunnable while making every new application write
        -- provide a transaction UUID through the atomic function below.
        EXECUTE format(
            'ALTER TABLE %I.provider_txn '
            'ADD COLUMN IF NOT EXISTS transaction_id UUID',
            provider_name
        );
        EXECUTE format(
            'UPDATE %I.provider_txn SET transaction_id = gen_random_uuid() '
            'WHERE transaction_id IS NULL',
            provider_name
        );
        EXECUTE format(
            'ALTER TABLE %I.provider_txn '
            'ALTER COLUMN transaction_id SET NOT NULL',
            provider_name
        );

        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON %I.provider_txn '
            '(agent_id, sim_time DESC)',
            'idx_' || provider_name || '_txn_agent_time',
            provider_name
        );
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON %I.provider_txn '
            '(counterparty_msisdn, sim_time DESC)',
            'idx_' || provider_name || '_txn_counterparty_time',
            provider_name
        );
        EXECUTE format(
            'CREATE UNIQUE INDEX IF NOT EXISTS %I ON %I.provider_txn '
            '(transaction_id) WHERE transaction_id IS NOT NULL',
            'uq_' || provider_name || '_txn_transaction_id',
            provider_name
        );
    END LOOP;
END
$provider_tables$;

CREATE TABLE IF NOT EXISTS shared.provider_customer_journal (
    transaction_id          UUID            PRIMARY KEY,
    agent_id                UUID            NOT NULL,
    provider_id             TEXT            NOT NULL
                                              CHECK (provider_id IN
                                                     ('bkash', 'nagad', 'rocket')),
    counterparty_id         TEXT            NOT NULL,
    amount_bdt              NUMERIC(14,2)   NOT NULL CHECK (amount_bdt > 0),
    direction               TEXT            NOT NULL
                                              CHECK (direction IN ('in', 'out')),
    freshness               TEXT            NOT NULL
                                              CHECK (freshness IN
                                                     ('fresh', 'degraded', 'stale',
                                                      'conflicting')),
    sim_time                TIMESTAMPTZ     NOT NULL,
    cash_delta_bdt          NUMERIC(14,2)   NOT NULL,
    provider_delta_bdt      NUMERIC(14,2)   NOT NULL,
    shared_balance_after    NUMERIC(14,2)   NOT NULL CHECK (shared_balance_after >= 0),
    shared_version_after    INTEGER         NOT NULL CHECK (shared_version_after > 0),
    provider_balance_after  NUMERIC(14,2)   NOT NULL CHECK (provider_balance_after >= 0),
    provider_version_after  INTEGER         NOT NULL CHECK (provider_version_after > 0),
    provider_updated_at     TIMESTAMPTZ     NOT NULL,
    committed_at            TIMESTAMPTZ     NOT NULL DEFAULT now(),
    CONSTRAINT ck_provider_customer_double_entry
        CHECK (cash_delta_bdt + provider_delta_bdt = 0),
    CONSTRAINT ck_provider_customer_amount_legs
        CHECK (abs(cash_delta_bdt) = amount_bdt
               AND abs(provider_delta_bdt) = amount_bdt),
    CONSTRAINT ck_provider_customer_direction_legs
        CHECK ((direction = 'out'
                AND cash_delta_bdt = -amount_bdt
                AND provider_delta_bdt = amount_bdt)
               OR
               (direction = 'in'
                AND cash_delta_bdt = amount_bdt
                AND provider_delta_bdt = -amount_bdt))
);
CREATE INDEX IF NOT EXISTS idx_provider_customer_journal_agent_time
    ON shared.provider_customer_journal (agent_id, sim_time DESC);

-- ---------------------------------------------------------------------
-- 6. Atomic cross-ledger customer transaction boundary.
--
-- app_shared still has no table privilege in any provider schema.  It can
-- execute only this narrowly-scoped SECURITY DEFINER function, which
-- validates the provider allowlist and commits the physical-cash movement,
-- inverse e-money movement, and provider audit row in one PostgreSQL
-- transaction.  transaction_id makes retries and scenario replays safe.
-- ---------------------------------------------------------------------
GRANT USAGE ON SCHEMA shared, bkash, nagad, rocket TO ledger_executor;
GRANT SELECT, UPDATE ON shared.shared_cash_ledger TO ledger_executor;
GRANT INSERT ON shared.shared_cash_movement TO ledger_executor;
GRANT SELECT, INSERT ON shared.provider_customer_journal TO ledger_executor;
GRANT SELECT, UPDATE ON bkash.provider_balance TO ledger_executor;
GRANT SELECT, UPDATE ON nagad.provider_balance TO ledger_executor;
GRANT SELECT, UPDATE ON rocket.provider_balance TO ledger_executor;
GRANT INSERT ON bkash.provider_txn TO ledger_executor;
GRANT INSERT ON nagad.provider_txn TO ledger_executor;
GRANT INSERT ON rocket.provider_txn TO ledger_executor;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA shared TO ledger_executor;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA bkash TO ledger_executor;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA nagad TO ledger_executor;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA rocket TO ledger_executor;

-- Create the routine while acting as its final constrained owner. This is
-- what makes CREATE OR REPLACE work on both the first run and every rerun
-- without requiring the managed PostgreSQL owner to be a superuser.
GRANT CREATE ON SCHEMA shared TO ledger_executor;
SET ROLE ledger_executor;

CREATE OR REPLACE FUNCTION shared.apply_provider_customer_transaction(
    p_transaction_id UUID,
    p_agent_id UUID,
    p_provider_id TEXT,
    p_counterparty_id TEXT,
    p_amount_bdt NUMERIC,
    p_direction TEXT,
    p_freshness TEXT,
    p_sim_time TIMESTAMPTZ
)
RETURNS TABLE (
    applied BOOLEAN,
    shared_balance_bdt NUMERIC,
    shared_version_id INTEGER,
    provider_balance_bdt NUMERIC,
    provider_version_id INTEGER,
    provider_updated_at TIMESTAMPTZ
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $atomic_provider_transaction$
DECLARE
    cash_delta       NUMERIC(14,2);
    provider_delta   NUMERIC(14,2);
    current_cash     NUMERIC(14,2);
    current_cash_ver INTEGER;
    current_provider NUMERIC(14,2);
    current_prov_ver INTEGER;
    current_prov_at  TIMESTAMPTZ;
    prior             shared.provider_customer_journal%ROWTYPE;
BEGIN
    IF p_transaction_id IS NULL THEN
        RAISE EXCEPTION 'transaction_id is required' USING ERRCODE = '22004';
    END IF;
    IF p_provider_id NOT IN ('bkash', 'nagad', 'rocket') THEN
        RAISE EXCEPTION 'unknown provider_id %', p_provider_id
            USING ERRCODE = '22023';
    END IF;
    IF p_amount_bdt IS NULL OR p_amount_bdt <= 0 THEN
        RAISE EXCEPTION 'amount_bdt must be positive' USING ERRCODE = '22023';
    END IF;
    IF p_direction NOT IN ('in', 'out') THEN
        RAISE EXCEPTION 'direction must be in or out' USING ERRCODE = '22023';
    END IF;
    IF p_freshness NOT IN ('fresh', 'degraded', 'stale', 'conflicting') THEN
        RAISE EXCEPTION 'invalid freshness %', p_freshness
            USING ERRCODE = '22023';
    END IF;

    -- Customer cash-out: physical cash leaves the drawer and the agent's
    -- provider e-money rises.  Cash-in is the exact inverse.
    IF p_direction = 'out' THEN
        cash_delta := -p_amount_bdt;
        provider_delta := p_amount_bdt;
    ELSE
        cash_delta := p_amount_bdt;
        provider_delta := -p_amount_bdt;
    END IF;

    -- A consistent lock order (shared row, then provider row) prevents
    -- cross-provider deadlocks while allowing unrelated agents to proceed.
    SELECT balance_bdt, version_id
      INTO current_cash, current_cash_ver
      FROM shared.shared_cash_ledger
     WHERE agent_id = p_agent_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no shared cash balance for agent %', p_agent_id
            USING ERRCODE = 'P0002';
    END IF;

    EXECUTE format(
        'SELECT balance_bdt, version_id, updated_at '
        'FROM %I.provider_balance WHERE agent_id = $1 FOR UPDATE',
        p_provider_id
    )
    INTO current_provider, current_prov_ver, current_prov_at
    USING p_agent_id;
    -- Dynamic EXECUTE does not update PL/pgSQL's FOUND flag, so inspect the
    -- non-null balance selected from the constrained provider row directly.
    IF current_provider IS NULL THEN
        RAISE EXCEPTION 'no % balance for agent %', p_provider_id, p_agent_id
            USING ERRCODE = 'P0002';
    END IF;

    -- The journal is the idempotency authority and retains the original
    -- post-commit values.  Checking after both locks makes concurrent retries
    -- deterministic under the READ COMMITTED transaction used by the caller.
    SELECT *
      INTO prior
      FROM shared.provider_customer_journal
     WHERE transaction_id = p_transaction_id;

    IF FOUND THEN
        IF prior.agent_id IS DISTINCT FROM p_agent_id
           OR prior.provider_id IS DISTINCT FROM p_provider_id
           OR prior.counterparty_id IS DISTINCT FROM p_counterparty_id
           OR prior.amount_bdt IS DISTINCT FROM p_amount_bdt
           OR prior.direction IS DISTINCT FROM p_direction
           OR prior.freshness IS DISTINCT FROM p_freshness
           OR prior.sim_time IS DISTINCT FROM p_sim_time THEN
            RAISE EXCEPTION
                'transaction_id % was already used with a different request',
                p_transaction_id USING ERRCODE = '22023';
        END IF;
        applied := FALSE;
        shared_balance_bdt := prior.shared_balance_after;
        shared_version_id := prior.shared_version_after;
        provider_balance_bdt := prior.provider_balance_after;
        provider_version_id := prior.provider_version_after;
        provider_updated_at := prior.provider_updated_at;
        RETURN NEXT;
        RETURN;
    END IF;

    IF current_cash + cash_delta < 0 THEN
        RAISE EXCEPTION 'insufficient shared cash for agent %', p_agent_id
            USING ERRCODE = '23514';
    END IF;
    IF current_provider + provider_delta < 0 THEN
        RAISE EXCEPTION 'insufficient % e-money for agent %',
            p_provider_id, p_agent_id USING ERRCODE = '23514';
    END IF;

    UPDATE shared.shared_cash_ledger
       SET balance_bdt = current_cash + cash_delta,
           version_id = current_cash_ver + 1,
           updated_at = now()
     WHERE agent_id = p_agent_id;

    EXECUTE format(
        'UPDATE %I.provider_balance '
        'SET balance_bdt = $1, version_id = $2, updated_at = now() '
        'WHERE agent_id = $3 RETURNING updated_at',
        p_provider_id
    )
    INTO current_prov_at
    USING current_provider + provider_delta, current_prov_ver + 1, p_agent_id;

    INSERT INTO shared.shared_cash_movement
        (transaction_id, agent_id, delta_bdt, reason, sim_time, version_after)
    VALUES
        (p_transaction_id, p_agent_id, cash_delta,
         'provider_customer_' || CASE p_direction
             WHEN 'out' THEN 'cash_out' ELSE 'cash_in' END,
         p_sim_time, current_cash_ver + 1);

    EXECUTE format(
        'INSERT INTO %I.provider_txn '
        '(transaction_id, agent_id, counterparty_msisdn, amount_bdt, '
        ' direction, freshness, sim_time) '
        'VALUES ($1, $2, $3, $4, $5, $6, $7)',
        p_provider_id
    )
    USING p_transaction_id, p_agent_id, p_counterparty_id, p_amount_bdt,
          p_direction, p_freshness, p_sim_time;

    INSERT INTO shared.provider_customer_journal
        (transaction_id, agent_id, provider_id, counterparty_id, amount_bdt,
         direction, freshness, sim_time, cash_delta_bdt, provider_delta_bdt,
         shared_balance_after, shared_version_after,
         provider_balance_after, provider_version_after, provider_updated_at)
    VALUES
        (p_transaction_id, p_agent_id, p_provider_id, p_counterparty_id,
         p_amount_bdt, p_direction, p_freshness, p_sim_time,
         cash_delta, provider_delta,
         current_cash + cash_delta, current_cash_ver + 1,
         current_provider + provider_delta, current_prov_ver + 1,
         current_prov_at);

    applied := TRUE;
    shared_balance_bdt := current_cash + cash_delta;
    shared_version_id := current_cash_ver + 1;
    provider_balance_bdt := current_provider + provider_delta;
    provider_version_id := current_prov_ver + 1;
    provider_updated_at := current_prov_at;
    RETURN NEXT;
END
$atomic_provider_transaction$;

-- Owning the SECURITY DEFINER function with this constrained NOLOGIN role
-- avoids executing application input with migration-superuser privileges.
REVOKE ALL PRIVILEGES
    ON FUNCTION shared.apply_provider_customer_transaction(
        UUID, UUID, TEXT, TEXT, NUMERIC, TEXT, TEXT, TIMESTAMPTZ
    )
    FROM PUBLIC;
GRANT EXECUTE
    ON FUNCTION shared.apply_provider_customer_transaction(
        UUID, UUID, TEXT, TEXT, NUMERIC, TEXT, TEXT, TIMESTAMPTZ
    )
    TO app_shared;
RESET ROLE;
REVOKE CREATE ON SCHEMA shared FROM ledger_executor;
REVOKE ledger_executor FROM CURRENT_USER;

-- ---------------------------------------------------------------------
-- 7. Least-privilege grants and explicit cross-provider denial.
-- ---------------------------------------------------------------------
DO $privileges$
DECLARE
    schema_name  TEXT;
    role_name    TEXT;
    other_schema TEXT;
BEGIN
    FOR schema_name, role_name IN
        SELECT *
          FROM (VALUES
                ('shared', 'app_shared'),
                ('bkash',  'app_bkash'),
                ('nagad',  'app_nagad'),
                ('rocket', 'app_rocket')
          ) AS role_schemas(schema_name, role_name)
    LOOP
        EXECUTE format(
            'GRANT USAGE ON SCHEMA %I TO %I', schema_name, role_name
        );
        EXECUTE format(
            'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %I TO %I',
            schema_name,
            role_name
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES IN SCHEMA %I '
            'GRANT USAGE, SELECT ON SEQUENCES TO %I',
            schema_name,
            role_name
        );

        -- Prevent accidental object resolution through the public schema.
        EXECUTE format(
            'ALTER ROLE %I SET search_path = %I, pg_catalog',
            role_name,
            schema_name
        );

        -- Remove any stale direct grants from earlier development versions.
        FOR other_schema IN
            SELECT unnest(ARRAY['shared', 'bkash', 'nagad', 'rocket'])
        LOOP
            IF other_schema <> schema_name THEN
                EXECUTE format(
                    'REVOKE ALL PRIVILEGES ON SCHEMA %I FROM %I',
                    other_schema,
                    role_name
                );
                EXECUTE format(
                    'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA %I FROM %I',
                    other_schema,
                    role_name
                );
                EXECUTE format(
                    'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA %I FROM %I',
                    other_schema,
                    role_name
                );
            END IF;
        END LOOP;
    END LOOP;
END
$privileges$;

-- Shared role: mutable balance, append-only movement and telemetry.
GRANT SELECT, INSERT, UPDATE
    ON shared.shared_cash_ledger TO app_shared;
GRANT SELECT, INSERT
    ON shared.shared_cash_movement, shared.simulation_events TO app_shared;

-- Provider roles can read their own immutable transaction history, but only
-- the atomic ledger boundary may append customer transactions.
GRANT SELECT, INSERT, UPDATE ON bkash.provider_balance TO app_bkash;
REVOKE INSERT, UPDATE, DELETE ON bkash.provider_txn FROM app_bkash;
GRANT SELECT ON bkash.provider_txn TO app_bkash;
GRANT SELECT, INSERT, UPDATE ON nagad.provider_balance TO app_nagad;
REVOKE INSERT, UPDATE, DELETE ON nagad.provider_txn FROM app_nagad;
GRANT SELECT ON nagad.provider_txn TO app_nagad;
GRANT SELECT, INSERT, UPDATE ON rocket.provider_balance TO app_rocket;
REVOKE INSERT, UPDATE, DELETE ON rocket.provider_txn FROM app_rocket;
GRANT SELECT ON rocket.provider_txn TO app_rocket;

-- ---------------------------------------------------------------------
-- 8. Deterministic demo seed. Reruns preserve the current live state.
-- ---------------------------------------------------------------------
INSERT INTO shared.shared_cash_ledger
    (agent_id, balance_bdt, safety_buffer, version_id)
VALUES
    ('00000000-0000-0000-0000-000000000001', 500000.00, 50000.00, 1)
ON CONFLICT (agent_id) DO NOTHING;

DO $provider_seed$
DECLARE
    provider_name  TEXT;
    opening_balance NUMERIC(14,2);
    demo_agent_id   UUID := '00000000-0000-0000-0000-000000000001';
BEGIN
    FOR provider_name, opening_balance IN
        SELECT *
          FROM (VALUES
                ('bkash',  120000.00::NUMERIC),
                ('nagad',   30000.00::NUMERIC),
                ('rocket',  90000.00::NUMERIC)
          ) AS provider_seeds(provider_name, opening_balance)
    LOOP
        EXECUTE format(
            'INSERT INTO %I.provider_balance '
            '(agent_id, balance_bdt, version_id) VALUES ($1, $2, 1) '
            'ON CONFLICT (agent_id) DO NOTHING',
            provider_name
        )
        USING demo_agent_id, opening_balance;
    END LOOP;
END
$provider_seed$;
