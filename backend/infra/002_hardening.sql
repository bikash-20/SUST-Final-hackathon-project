-- =====================================================================
-- 002_hardening.sql -- durable failure handling and coordination FSM
--
-- This migration is independently rerunnable. It creates its schema and
-- application role when absent, but never resets data or existing role
-- credentials.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $shared_role$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_shared'
    ) THEN
        CREATE ROLE app_shared LOGIN PASSWORD 'change_me_shared';
    END IF;
END
$shared_role$;

CREATE SCHEMA IF NOT EXISTS shared;
REVOKE ALL PRIVILEGES ON SCHEMA shared FROM PUBLIC;
GRANT USAGE ON SCHEMA shared TO app_shared;
ALTER ROLE app_shared SET search_path = shared, pg_catalog;

-- Durable sink for ticks that exhaust the bounded retry policy.
CREATE TABLE IF NOT EXISTS shared.dead_letter_logs (
    id              BIGSERIAL       PRIMARY KEY,
    tick_id         TEXT            NOT NULL,
    agent_id        UUID,
    provider_id     TEXT
                                      CHECK (provider_id IS NULL OR provider_id IN
                                             ('bkash', 'nagad', 'rocket')),
    sim_time        TIMESTAMPTZ     NOT NULL,
    kind            TEXT            NOT NULL,
    payload         JSONB           NOT NULL,
    retries         INTEGER         NOT NULL CHECK (retries >= 0),
    last_error      TEXT            NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dead_letter_time
    ON shared.dead_letter_logs (sim_time DESC);
CREATE INDEX IF NOT EXISTS idx_dead_letter_kind
    ON shared.dead_letter_logs (kind);
CREATE INDEX IF NOT EXISTS idx_dead_letter_agent_time
    ON shared.dead_letter_logs (agent_id, sim_time DESC);

-- One canonical row per alert. `transitions` remains an append-only JSON
-- history even though the current token state is updated in place.
CREATE TABLE IF NOT EXISTS shared.coordination_alerts (
    id              BIGSERIAL       PRIMARY KEY,
    alert_token     UUID            NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    agent_id        UUID,
    provider_id     TEXT
                                      CHECK (provider_id IS NULL OR provider_id IN
                                             ('bkash', 'nagad', 'rocket')),
    severity        TEXT            NOT NULL DEFAULT 'medium'
                                      CHECK (btrim(severity) <> ''),
    status          TEXT            NOT NULL DEFAULT 'PENDING'
                                      CHECK (status IN
                                             ('PENDING', 'ACKNOWLEDGED', 'ESCALATED', 'RESOLVED')),
    transitions     JSONB           NOT NULL DEFAULT '[]'::jsonb
                                      CHECK (jsonb_typeof(transitions) = 'array'),
    sim_time        TIMESTAMPTZ     NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);
-- Upgrade existing databases whose original check predates ESCALATED.
ALTER TABLE shared.coordination_alerts
    DROP CONSTRAINT IF EXISTS coordination_alerts_status_check;
ALTER TABLE shared.coordination_alerts
    ADD CONSTRAINT coordination_alerts_status_check
    CHECK (status IN ('PENDING', 'ACKNOWLEDGED', 'ESCALATED', 'RESOLVED'));
CREATE INDEX IF NOT EXISTS idx_coord_status
    ON shared.coordination_alerts (status);
CREATE INDEX IF NOT EXISTS idx_coord_sim_time
    ON shared.coordination_alerts (sim_time DESC);
CREATE INDEX IF NOT EXISTS idx_coord_agent_status_time
    ON shared.coordination_alerts (agent_id, status, sim_time DESC);

-- Clean up the broad DML grant from pre-hardening development versions,
-- then grant only what the runtime paths require.
REVOKE ALL PRIVILEGES
    ON shared.dead_letter_logs, shared.coordination_alerts FROM app_shared;
GRANT SELECT, INSERT ON shared.dead_letter_logs TO app_shared;
GRANT SELECT, INSERT, UPDATE ON shared.coordination_alerts TO app_shared;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA shared TO app_shared;
ALTER DEFAULT PRIVILEGES IN SCHEMA shared
    REVOKE ALL PRIVILEGES ON TABLES FROM app_shared;
ALTER DEFAULT PRIVILEGES IN SCHEMA shared
    GRANT USAGE, SELECT ON SEQUENCES TO app_shared;
