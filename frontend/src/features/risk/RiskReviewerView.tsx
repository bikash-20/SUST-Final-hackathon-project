"use client";

import { SafeFallbackLayout } from "../safety/SafeFallbackLayout";
import { CaseTimeline } from "../coordination/CaseTimeline";
import { useTelemetryStore } from "../telemetry/useTelemetryStream";

const BDT = new Intl.NumberFormat("en-BD", { maximumFractionDigits: 2 });

function percent(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

export function RiskReviewerView() {
  const detections = useTelemetryStore((state) => state.anomalyDetections);
  const connectionState = useTelemetryStore((state) => state.connectionState);
  const alerts = useTelemetryStore((state) => state.alerts);

  const ordered = [...detections].sort((left, right) => {
    if (right.riskScore !== left.riskScore) return right.riskScore - left.riskScore;
    return Date.parse(right.simTime) - Date.parse(left.simTime);
  });

  return (
    <SafeFallbackLayout>
      <div className="mx-auto max-w-screen-xl px-3 py-5 sm:px-5 sm:py-7">
        <header className="mb-5 flex flex-wrap items-end justify-between gap-3 rounded-2xl border border-slate-200/80 bg-white/90 p-5 shadow-lg shadow-slate-900/5">
          <div>
            <h1 className="text-xl font-bold tracking-tight text-slate-950">Risk review workbench</h1>
            <p className="text-xs text-slate-500">
              Algorithmic 12-minute-window evidence for human review; no source labels are used.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={
                "rounded-full px-2 py-1 text-xs font-semibold " +
                (connectionState === "connected"
                  ? "bg-emerald-100 text-emerald-800"
                  : "bg-amber-100 text-amber-900")
              }
            >
              {connectionState === "connected" ? "● Live detector" : "○ Reconnecting"}
            </span>
            <span className="rounded bg-slate-100 px-2 py-1 text-xs font-semibold">
              {ordered.length} signal{ordered.length === 1 ? "" : "s"}
            </span>
          </div>
        </header>

        <section className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-lg shadow-slate-900/5 sm:p-5">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[920px] text-sm">
              <thead className="text-left text-xs uppercase text-slate-500">
                <tr>
                  <th className="py-2">Detected</th>
                  <th>Provider</th>
                  <th>Risk score</th>
                  <th>Velocity</th>
                  <th>Repeated amount</th>
                  <th>Account cluster</th>
                  <th>Window evidence</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {ordered.length === 0 && (
                  <tr>
                    <td colSpan={7} className="py-10 text-center text-slate-500">
                      No detector output received. Transactions remain unlabelled until the
                      sliding-window model produces a review signal.
                    </td>
                  </tr>
                )}
                {ordered.map((detection) => (
                  <tr key={detection.detectionId} className="align-top">
                    <td className="py-3 font-mono text-xs">
                      {new Date(detection.simTime).toISOString().slice(11, 19)}
                    </td>
                    <td className="py-3 font-semibold uppercase">
                      {detection.providerId ?? "cross-provider"}
                    </td>
                    <td className="py-3">
                      <span
                        className={
                          "rounded px-2 py-1 text-xs font-bold " +
                          (detection.riskScore >= 0.8
                            ? "bg-rose-100 text-rose-800"
                            : detection.riskScore >= 0.6
                              ? "bg-amber-100 text-amber-900"
                              : "bg-sky-100 text-sky-800")
                        }
                      >
                        {percent(detection.riskScore)} · {detection.severity}
                      </span>
                    </td>
                    <td className="py-3">
                      <div className="font-semibold">{percent(detection.frequencyScore)}</div>
                      <div className="text-xs text-slate-500">
                        {detection.transactionCount || "—"} outgoing transactions
                      </div>
                    </td>
                    <td className="py-3">
                      <div className="font-semibold">
                        {percent(detection.identicalAmountScore)}
                      </div>
                      <div className="text-xs text-slate-500">
                        {detection.repeatedAmountBdt === null
                          ? "Amount not dominant"
                          : `৳${BDT.format(detection.repeatedAmountBdt)} × ${detection.repeatedAmountFrequency || "—"}`}
                      </div>
                    </td>
                    <td className="py-3">
                      <div className="font-semibold">
                        {percent(detection.accountClusterScore)}
                      </div>
                      <div className="text-xs text-slate-500">
                        {detection.accountCount || "—"} linked accounts
                      </div>
                      {detection.accounts.length > 0 && (
                        <div
                          className="mt-1 max-w-[180px] truncate font-mono text-[11px] text-slate-500"
                          title={detection.accounts.join(", ")}
                        >
                          {detection.accounts.join(", ")}
                        </div>
                      )}
                    </td>
                    <td className="py-3">
                      <div className="text-xs font-semibold">
                        {detection.windowMinutes
                          ? `${detection.windowMinutes}-minute lookback`
                          : "Window supplied by detector"}
                      </div>
                      <div className="mt-1 text-xs text-slate-500">
                        Velocity {percent(detection.velocityScore)} · cadence{" "}
                        {percent(detection.cadenceScore)} · model confidence{" "}
                        {percent(detection.confidenceScore)}
                      </div>
                      {detection.rationale.length > 0 && (
                        <ul className="mt-1 max-w-xs list-disc pl-4 text-xs text-slate-500">
                          {detection.rationale.slice(0, 3).map((reason) => (
                            <li key={reason}>{reason}</li>
                          ))}
                        </ul>
                      )}
                      {detection.possibleBenignExplanations.length > 0 && (
                        <details className="mt-2 text-xs text-slate-600">
                          <summary className="cursor-pointer font-medium">
                            Possible benign context
                          </summary>
                          <ul className="mt-1 list-disc pl-4">
                            {detection.possibleBenignExplanations.map((explanation) => (
                              <li key={explanation}>{explanation}</li>
                            ))}
                          </ul>
                        </details>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700">
            ⚖ These scores combine observed frequency, repeated-amount concentration, and
            account clustering. They are decision-support signals—not fraud declarations.
            No account is frozen and no money is moved without an authorized human decision.
          </p>
        </section>

        <section className="mt-5 rounded-2xl border border-slate-200/80 bg-white p-4 shadow-lg shadow-slate-900/5 sm:p-5">
          <h2 className="text-base font-semibold">Alert cases and notes</h2>
          <p className="text-xs text-slate-500">
            Notes share one chronological audit timeline with coordination transitions.
          </p>
          {alerts.length === 0 ? (
            <p className="mt-3 text-sm text-slate-500">No alert cases available.</p>
          ) : (
            <ul className="mt-2 divide-y divide-slate-100">
              {[...alerts].reverse().map((alert) => (
                <li key={alert.alertToken} className="py-3">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                    <span className="font-mono">{alert.alertToken.slice(0, 8)}…</span>
                    <span className="font-semibold uppercase">
                      {alert.providerId ?? "shared"} · {alert.status}
                    </span>
                  </div>
                  {alert.reason && <p className="mt-1 text-xs text-slate-600">{alert.reason}</p>}
                  <CaseTimeline caseId={alert.alertToken} authorRole="Risk Reviewer" />
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </SafeFallbackLayout>
  );
}
