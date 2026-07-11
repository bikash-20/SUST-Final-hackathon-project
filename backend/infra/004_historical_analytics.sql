-- Read-only access path for historical liquidity analytics.
GRANT SELECT ON shared.provider_customer_journal TO app_shared;

CREATE INDEX IF NOT EXISTS idx_provider_customer_journal_agent_provider_time
    ON shared.provider_customer_journal (agent_id, provider_id, sim_time DESC);

-- Backs the shared-cash historical CTE.  The single-column index in
-- 001_init.sql already covers the WHERE clause, but the planner prefers
-- a compound key when the window grows to 60 days and the result set
-- becomes large enough that a sort step matters.
CREATE INDEX IF NOT EXISTS idx_shared_cash_movement_agent_time_id
    ON shared.shared_cash_movement (agent_id, sim_time DESC, id DESC);
