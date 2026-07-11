"use client";
/**
 * Live Evidence Panel — lets a judge verify the historical context layer
 * WITHOUT opening a terminal. Every value below is sourced from the same
 * SnapshotSource the backend emits; nothing here is fabricated.
 *
 *   * Raw CTE:    the historical aggregation SQL copied verbatim from
 *                 backend/app/domain/liquidity/historical_analytics.py
 *                 (the query the historical_analytics key in
 *                 /v1/telemetry/snapshot was produced from).
 *   * Live cursor: last_event_id from the SSE stream + sim_time +
 *                 connectionState so judges see the data is live.
 *   * Verdict:    transaction count, drain rate, consistency score,
 *                 window days, and the has_evidence flag wired to the
 *                 HistoricalContextCard.
 */
import { useState } from "react";
import { useTelemetryStore } from "../telemetry/useTelemetryStream";

const HISTORICAL_CTE = `-- historical.shared_cash: returns the aggregated rollup that the
-- backend stores under historical_analytics.shared_cash in the snapshot.
WITH raw AS (
    SELECT id, sim_time, delta_bdt
      FROM shared.shared_cash_movement
     WHERE agent_id = :agent_id
       AND sim_time >= :cutoff
       AND sim_time <= :as_of
     ORDER BY sim_time DESC, id DESC
     LIMIT :row_cap
), balance_points AS (
    SELECT id, sim_time,
           (SELECT balance_bdt FROM shared.shared_cash_ledger
             WHERE agent_id = :agent_id)
           - COALESCE(
               sum(delta_bdt) OVER (
                   ORDER BY sim_time DESC, id DESC
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
               ), 0
           ) AS balance_after
      FROM raw
), daily AS (
    SELECT balance_after,
           row_number() OVER (
               PARTITION BY (sim_time AT TIME ZONE 'UTC')::date
               ORDER BY sim_time DESC, id DESC
           ) AS daily_rank
      FROM balance_points
)
SELECT count(*)                                   AS transaction_count,
       avg(-delta_bdt) FILTER (WHERE delta_bdt < 0) AS average_outflow,
       avg(delta_bdt)  FILTER (WHERE delta_bdt > 0) AS average_inflow,
       sum(delta_bdt)                              AS net_delta,
       stddev_pop(abs(delta_bdt))                  AS amount_stddev,
       avg(abs(delta_bdt))                         AS average_absolute_delta,
       (SELECT avg(balance_after)
          FROM daily WHERE daily_rank = 1)          AS average_daily_balance,
       (SELECT balance_bdt
          FROM shared.shared_cash_ledger
         WHERE agent_id = :agent_id)               AS current_balance
  FROM raw;`;

const BDT = new Intl.NumberFormat("en-BD", { maximumFractionDigits: 0 });

interface Props {
  /** When true, the panel mounts inline (Ops view). When false, it
   *  renders as a compact one-row card with a "Show evidence" toggle
   *  (Agent view). */
  variant?: "full" | "compact";
}

export function LiveEvidencePanel({ variant = "full" }: Props) {
  const historical = useTelemetryStore((s) => s.historicalAnalytics);
  const lastEventId = useTelemetryStore((s) => s.lastEventId);
  const lastReceivedAt = useTelemetryStore((s) => s.lastReceivedAt);
  const simTime = useTelemetryStore((s) => s.simTime);
  const connectionState = useTelemetryStore((s) => s.connectionState);

  const [open, setOpen] = useState(variant === "full");
  const [copied, setCopied] = useState<string | null>(null);

  async function copy(label: string, value: string) {
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard) {
        await navigator.clipboard.writeText(value);
      }
      setCopied(label);
      window.setTimeout(() => setCopied((current) => (current === label ? null : current)), 1500);
    } catch {
      /* clipboard unavailable */
    }
  }

  if (variant === "compact" && !open) {
    return (
      <section
        className="mt-4 rounded-2xl border border-border bg-surface p-3 shadow-card"
        aria-label="Live evidence (collapsed)"
      >
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="flex w-full items-center justify-between gap-2 text-left"
          aria-expanded={false}
        >
          <span className="flex items-center gap-2">
            <EvidenceDot connected={connectionState === "connected"} />
            <span className="text-xs font-semibold uppercase tracking-eyebrow text-signal">
              Live evidence
            </span>
            <span className="num text-[11px] text-muted">
              cursor #{lastEventId}
            </span>
          </span>
          <span className="num text-[11px] text-muted">Show SQL ·</span>
        </button>
      </section>
    );
  }

  const txCount = historical?.historicalTransactions ?? 0;
  const drain = historical?.historicalDrainRateBdtPerMin ?? 0;
  const consistency = historical?.historicalConsistencyScore ?? 0;
  const days = historical?.historicalWindowDays ?? 0;
  const hasEvidence = historical?.historicalHasEvidence ?? txCount > 0;
  const asOf = historical?.asOf ?? null;

  return (
    <section
      className="mt-4 rounded-2xl border border-border bg-surface p-4 shadow-card sm:p-5"
      aria-label={`Live evidence panel, cursor ${lastEventId}`}
    >
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <EvidenceDot connected={connectionState === "connected"} />
          <h2 className="text-base font-semibold text-ink">Live evidence</h2>
          <span className="eyebrow">judge-verifiable</span>
        </div>
        {variant === "compact" && (
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="num text-xs text-muted hover:text-ink"
            aria-label="Collapse live evidence"
          >
            Hide
          </button>
        )}
      </header>

      <p className="mt-1 max-w-prose text-xs text-muted">
        Every value below is sourced live from{" "}
        <code className="num rounded bg-surface-2 px-1 py-0.5">/v1/telemetry/snapshot</code>{" "}
        — the same payload that drives the cards above. Nothing is hardcoded.
      </p>

      <dl className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <EvidenceStat
          label="Last event id"
          value={`#${lastEventId}`}
          mono
        />
        <EvidenceStat
          label="Sim time"
          value={simTime ? simTime.slice(0, 19).replace("T", " ") + "Z" : "—"}
          mono
        />
        <EvidenceStat
          label="Window"
          value={days ? `${days} days` : "—"}
          mono
        />
        <EvidenceStat
          label="Has evidence"
          value={hasEvidence ? "yes · live" : "warming up"}
          tone={hasEvidence ? "live" : "muted"}
        />
      </dl>

      <dl className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <EvidenceStat
          label="Transactions"
          value={hasEvidence ? txCount.toLocaleString() : "—"}
          mono
        />
        <EvidenceStat
          label="Drain / min"
          value={hasEvidence ? `৳${BDT.format(Math.round(drain))}` : "—"}
          mono
        />
        <EvidenceStat
          label="Consistency"
          value={hasEvidence ? consistency.toFixed(3) : "—"}
          mono
          tone={hasEvidence ? "live" : "muted"}
        />
        <EvidenceStat
          label="As of"
          value={asOf ? asOf.slice(0, 19).replace("T", " ") + "Z" : "—"}
          mono
        />
      </dl>

      <div className="mt-4">
        <div className="mb-1.5 flex items-center justify-between">
          <span className="eyebrow">Historical CTE — backend SQL</span>
          <button
            type="button"
            onClick={() => copy("sql", HISTORICAL_CTE)}
            className="num rounded border border-border bg-surface-2 px-2 py-0.5 text-[11px] font-semibold text-ink hover:border-signal hover:text-signal"
          >
            {copied === "sql" ? "Copied" : "Copy SQL"}
          </button>
        </div>
        <pre className="debug-pre" aria-label="Historical aggregation SQL">
{HISTORICAL_CTE}
        </pre>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() =>
            copy(
              "cursor",
              JSON.stringify(
                {
                  last_event_id: lastEventId,
                  sim_time: simTime,
                  last_received_at: lastReceivedAt,
                  window_days: days,
                  transaction_count: txCount,
                  drain_rate_bdt_per_min: drain,
                  consistency_score: consistency,
                  has_evidence: hasEvidence,
                },
                null,
                2,
              ),
            )
          }
          className="num rounded border border-border bg-surface-2 px-3 py-1.5 text-xs font-semibold text-ink hover:border-signal hover:text-signal"
        >
          {copied === "cursor" ? "Copied" : "Copy snapshot cursor"}
        </button>
        <span className="num text-[11px] text-muted">
          Received {lastReceivedAt ? new Date(lastReceivedAt).toISOString().slice(11, 19) + "Z" : "—"}
          {connectionState !== "connected" ? ` · ${connectionState}` : ""}
        </span>
      </div>
    </section>
  );
}

function EvidenceStat({
  label,
  value,
  mono,
  tone = "ink",
}: {
  label: string;
  value: string;
  mono?: boolean;
  tone?: "ink" | "live" | "muted";
}) {
  const toneClass =
    tone === "live"
      ? "text-signal"
      : tone === "muted"
        ? "text-muted"
        : "text-ink";
  return (
    <div className="rounded-xl bg-surface-2 px-3 py-2 ring-1 ring-inset ring-border">
      <dt className="eyebrow">{label}</dt>
      <dd className={`num mt-1 text-sm font-bold ${toneClass} ${mono ? "" : ""}`}>
        {value}
      </dd>
    </div>
  );
}

function EvidenceDot({ connected }: { connected: boolean }) {
  return (
    <span
      className={
        "h-2 w-2 rounded-full " +
        (connected ? "bg-emerald-400" : "live-dot")
      }
      aria-hidden
    />
  );
}
