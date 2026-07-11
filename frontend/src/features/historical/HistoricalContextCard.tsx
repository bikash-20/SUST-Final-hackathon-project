"use client";

import { useTelemetryStore } from "../telemetry/useTelemetryStream";

const BDT = new Intl.NumberFormat("en-BD", { maximumFractionDigits: 0 });

/**
 * One compact "trading terminal" card surface for the 60-day historical
 * context layer. Renders even when evidence is sparse so judges always
 * see *where* the layer is sourced from.
 */
export function HistoricalContextCard() {
  const historical = useTelemetryStore((state) => state.historicalAnalytics);

  if (!historical) {
    return (
      <div
        className="rounded-lg border border-border bg-surface px-4 py-3 shadow-card"
        aria-label="Historical context warming up"
      >
        <div className="eyebrow">Historical Context</div>
        <div className="num mt-1 text-sm text-muted">— warming up —</div>
      </div>
    );
  }

  const days = historical.historicalWindowDays;
  const hasEvidence = historical.historicalHasEvidence ?? historical.historicalTransactions > 0;

  return (
    <section
      className="relative overflow-hidden rounded-lg border border-border bg-surface shadow-card"
      aria-label={`${days}-day historical context`}
    >
      <div className="absolute left-0 top-0 h-full w-[3px] bg-signal" aria-hidden />
      <div className="px-4 py-3 pl-5">
        <div className="flex items-baseline justify-between gap-3">
          <span className="eyebrow">{days}-Day Context</span>
          <span
            className={`num text-[10px] uppercase tracking-wider ${
              hasEvidence ? "text-signal" : "text-muted"
            }`}
          >
            {hasEvidence ? "● Live evidence" : "○ Warming up"}
          </span>
        </div>

        <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
          <div>
            <div className="eyebrow">Transactions</div>
            <div className="num mt-0.5 text-xl text-ink">
              {hasEvidence ? historical.historicalTransactions.toLocaleString() : "—"}
            </div>
          </div>
          <div>
            <div className="eyebrow">Drain / min</div>
            <div className="num mt-0.5 text-xl text-ink">
              {hasEvidence ? `৳${BDT.format(Math.round(historical.historicalDrainRateBdtPerMin))}` : "—"}
            </div>
          </div>
          <div>
            <div className="eyebrow">Avg balance</div>
            <div className="num mt-0.5 text-xl text-ink">
              {hasEvidence ? `৳${BDT.format(Math.round(historical.historicalAvgBalanceBdt))}` : "—"}
            </div>
          </div>
          <div>
            <div className="eyebrow">Consistency</div>
            <div className="num mt-0.5 text-xl">
              <span className={hasEvidence ? "text-signal" : "text-muted"}>
                {hasEvidence ? historical.historicalConsistencyScore.toFixed(3) : "—"}
              </span>
              <span className="ml-1 text-xs text-muted">/ 1.000</span>
            </div>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted">
          <span>
            Window: <span className="num text-ink">{days} days</span>
          </span>
          <span>
            As of <span className="num text-ink">{new Date(historical.asOf).toISOString().slice(0, 16).replace("T", " ")}Z</span>
          </span>
          {!hasEvidence && (
            <span className="text-muted">
              No historical rows yet — live EWMA continues unweighted.
            </span>
          )}
        </div>
      </div>
    </section>
  );
}
