-- Read-only access path for historical liquidity analytics.
GRANT SELECT ON shared.provider_customer_journal TO app_shared;

CREATE INDEX IF NOT EXISTS idx_provider_customer_journal_agent_provider_time
    ON shared.provider_customer_journal (agent_id, provider_id, sim_time DESC);
