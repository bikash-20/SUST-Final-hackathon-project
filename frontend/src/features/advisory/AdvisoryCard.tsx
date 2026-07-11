"use client";

import { useTelemetryStore } from "../telemetry/useTelemetryStream";
import type { ProviderId } from "../telemetry/types";

const PROVIDER_LABEL: Record<ProviderId, string> = {
  bkash: "bKash",
  nagad: "Nagad",
  rocket: "Rocket",
};

const BDT = new Intl.NumberFormat("en-BD", { maximumFractionDigits: 0 });

/**
 * Trading-terminal advisory surface:
 *   * dark card body, amber left-border, amber headline.
 *   * mono numbers for every BDT figure and the hero TTE.
 *   * pulsing live dot beside the TTE — the product's signature element.
 */
export function AdvisoryCard() {
  const forecastsByKey = useTelemetryStore((state) => state.liquidityForecasts);
  const degraded = useTelemetryStore((state) => state.degraded);
  const feedConfidence = useTelemetryStore((state) => state.confidenceScore);

  const critical = Object.values(forecastsByKey)
    .filter(
      (forecast) =>
        forecast.predictedTteMin !== null && forecast.predictedTteMin >= 0,
    )
    .sort(
      (left, right) =>
        (left.predictedTteMin as number) - (right.predictedTteMin as number),
    )[0];

  if (!critical && !degraded) return null;

  const ledger = critical?.providerId
    ? PROVIDER_LABEL[critical.providerId]
    : critical?.key === "shared_cash"
      ? "Shared cash"
      : "Provider";
  const banglaPosition = critical?.key === "shared_cash" ? "নগদ" : "ই-মানি";
  const confidence = critical?.confidenceScore ?? feedConfidence;
  const hasTte =
    critical?.predictedTteMin !== null && critical?.predictedTteMin !== undefined;

  return (
    <section
      className="relative overflow-hidden rounded-lg border border-border bg-surface shadow-card"
      aria-label="Liquidity advisory"
    >
      {/* Amber left-border — replaces the old full-bleed cream background. */}
      <div className="absolute left-0 top-0 h-full w-[3px] bg-signal" aria-hidden />

      <div className="px-5 py-4 pl-6">
        <header className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="rounded-sm bg-signal-soft px-2 py-0.5 text-[10px] font-semibold uppercase tracking-eyebrow text-signal">
              Advisory
            </span>
            <span className="text-[11px] text-muted">
              Live evidence · human decision required
            </span>
          </div>
          {hasTte && (
            <span className="flex items-center gap-2">
              <span className="live-dot" aria-hidden />
              <span className="text-[11px] font-medium uppercase tracking-eyebrow text-signal">
                Live
              </span>
            </span>
          )}
        </header>

        {/* Hero TTE — the signature element. */}
        {hasTte ? (
          <div className="mt-3 flex items-baseline gap-3">
            <span className="hero-ttc num">
              {critical!.predictedTteMin!.toFixed(1)}
            </span>
            <span className="num text-base text-muted">min to exhaust</span>
          </div>
        ) : (
          <div className="mt-3 num text-2xl text-muted">—</div>
        )}

        <div className="mt-5 grid gap-6 md:grid-cols-3">
          <div>
            <div className="eyebrow">Recommendation</div>
            {hasTte ? (
              <>
                <p className="num mt-1 text-sm text-ink">
                  {ledger} {banglaPosition} প্রবাহে আনুমানিক{" "}
                  <span className="text-signal">
                    {critical!.predictedTteMin!.toFixed(1)}
                  </span>{" "}
                  মিনিটে শেষ হতে পারে—রিফিল পরিকল্পনা পর্যালোচনা করুন।
                </p>
                <p className="num mt-1 text-xs italic text-muted">
                  {ledger} may exhaust in {critical!.predictedTteMin!.toFixed(1)} min at
                  the observed rate; review a refill plan.
                </p>
              </>
            ) : (
              <p className="num mt-1 text-sm text-ink">
                ডেটা ফিড অনিশ্চিত — লেনদেনের আগে সর্বশেষ ব্যালেন্স যাচাই করুন।
              </p>
            )}
          </div>

          <div>
            <div className="eyebrow">Quantitative evidence</div>
            {critical ? (
              <dl className="num mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm text-ink">
                <dt className="text-muted">TTE</dt>
                <dd className="font-semibold text-signal">
                  {critical.predictedTteMin === null
                    ? "No declining trend"
                    : `${critical.predictedTteMin.toFixed(1)} min`}
                </dd>
                <dt className="text-muted">EWMA outflow</dt>
                <dd className="font-semibold">
                  {critical.ewmaOutflowBdtPerMin === null
                    ? "—"
                    : `৳${BDT.format(critical.ewmaOutflowBdtPerMin)}/min`}
                </dd>
                <dt className="text-muted">Balance</dt>
                <dd className="font-semibold">
                  {critical.balanceBdt === null
                    ? "See live ledger"
                    : `৳${BDT.format(critical.balanceBdt)}`}
                </dd>
              </dl>
            ) : (
              <p className="num mt-1 text-sm text-muted">
                Forecast withheld while feed quality is low.
              </p>
            )}
          </div>

          <div>
            <div className="eyebrow">Uncertainty</div>
            <div className="num mt-1 space-y-1 text-sm text-ink">
              <p>
                <span className="text-muted">95% interval:</span>{" "}
                <strong className="text-ink">
                  {critical?.ci95
                    ? `${critical.ci95[0].toFixed(1)}–${critical.ci95[1].toFixed(1)} min`
                    : "not yet available"}
                </strong>
              </p>
              <p>
                <span className="text-muted">Confidence:</span>{" "}
                <strong className="text-ink">
                  {confidence === null || confidence === undefined
                    ? "not reported"
                    : `${Math.round(confidence * 100)}%`}
                </strong>
              </p>
              <p className="num text-xs text-muted">
                {critical?.sampleCount
                  ? `Calculated from ${critical.sampleCount} observed deltas.`
                  : "More observations will tighten the interval."}
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
