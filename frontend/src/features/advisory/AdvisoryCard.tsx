"use client";

import { useTelemetryStore } from "../telemetry/useTelemetryStream";
import type { ProviderId } from "../telemetry/types";

const PROVIDER_LABEL: Record<ProviderId, string> = {
  bkash: "bKash",
  nagad: "Nagad",
  rocket: "Rocket",
};

const BDT = new Intl.NumberFormat("en-BD", { maximumFractionDigits: 0 });

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

  return (
    <section
      className="overflow-hidden rounded-2xl border border-amber-200 bg-gradient-to-br from-amber-50 to-orange-50/70 p-4 shadow-lg shadow-amber-900/5 sm:p-5"
      aria-label="Liquidity advisory"
    >
      <header className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <span className="rounded-full bg-amber-500 px-2 py-0.5 text-xs font-bold uppercase text-white">
          Advisory
        </span>
        <span className="text-xs text-amber-800">Live evidence · human decision required</span>
      </header>

      <div className="grid gap-4 md:grid-cols-3">
        <div>
          <div className="text-[11px] font-semibold uppercase text-amber-700">
            Recommendation
          </div>
          {critical?.predictedTteMin !== null &&
          critical?.predictedTteMin !== undefined ? (
            <>
              <p className="mt-1 font-medium text-amber-950">
                {ledger} {banglaPosition} বর্তমান প্রবাহে আনুমানিক{" "}
                {critical.predictedTteMin.toFixed(1)} মিনিটে শেষ হতে পারে—রিফিল
                পরিকল্পনা পর্যালোচনা করুন।
              </p>
              <p className="mt-1 text-xs italic text-amber-800">
                {ledger} may exhaust in {critical.predictedTteMin.toFixed(1)} minutes at
                the observed rate; review a refill plan.
              </p>
            </>
          ) : (
            <p className="mt-1 font-medium text-amber-950">
              ডেটা ফিড অনিশ্চিত—লেনদেনের আগে সর্বশেষ ব্যালেন্স যাচাই করুন।
            </p>
          )}
        </div>

        <div>
          <div className="text-[11px] font-semibold uppercase text-amber-700">
            Quantitative evidence
          </div>
          {critical ? (
            <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-sm text-amber-950">
              <dt>TTE</dt>
              <dd className="font-semibold">
                {critical.predictedTteMin === null
                  ? "No declining trend"
                  : `${critical.predictedTteMin.toFixed(1)} min`}
              </dd>
              <dt>EWMA outflow</dt>
              <dd className="font-semibold">
                {critical.ewmaOutflowBdtPerMin === null
                  ? "—"
                  : `৳${BDT.format(critical.ewmaOutflowBdtPerMin)}/min`}
              </dd>
              <dt>Balance</dt>
              <dd className="font-semibold">
                {critical.balanceBdt === null
                  ? "See live ledger"
                  : `৳${BDT.format(critical.balanceBdt)}`}
              </dd>
            </dl>
          ) : (
            <p className="mt-1 text-sm text-amber-900">Forecast withheld while feed quality is low.</p>
          )}
        </div>

        <div>
          <div className="text-[11px] font-semibold uppercase text-amber-700">
            Uncertainty
          </div>
          <div className="mt-1 space-y-1 text-sm text-amber-950">
            <p>
              95% interval:{" "}
              <strong>
                {critical?.ci95
                  ? `${critical.ci95[0].toFixed(1)}–${critical.ci95[1].toFixed(1)} min`
                  : "not yet available"}
              </strong>
            </p>
            <p>
              Confidence:{" "}
              <strong>
                {confidence === null || confidence === undefined
                  ? "not reported"
                  : `${Math.round(confidence * 100)}%`}
              </strong>
            </p>
            <p className="text-xs text-amber-800">
              {critical?.sampleCount
                ? `Calculated from ${critical.sampleCount} observed deltas.`
                : "More observations will tighten the interval."}
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
