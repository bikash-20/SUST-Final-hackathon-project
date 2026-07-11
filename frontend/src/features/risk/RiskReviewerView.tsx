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
        <header className="mb-5 flex flex-wrap items-end justify-between gap-3 rounded-2xl border border-border bg-surface p-5 shadow-card">
          <div>
            <h1 className="text-xl font-bold tracking-tight text-ink">Risk review workbench</h1>
            <p className="text-xs text-muted">
              Algorithmic 12-minute-window evidence for human review; no source labels are used.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={
                "flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold " +
                (connectionState === "connected"
                  ? "border-emerald-700/40 bg-emerald-900/20 text-emerald-300"
                  : "border-signal/40 bg-signal-soft text-signal")
              }
            >
              <span
                className={
                  "h-1.5 w-1.5 rounded-full " +
                  (connectionState === "connected" ? "bg-emerald-400" : "live-dot")
                }
                aria-hidden
              />
              {connectionState === "connected" ? "Live detector" : "Reconnecting"}
            </span>
            <span className="num rounded bg-surface-2 px-2 py-1 text-xs font-semibold text-ink">
              {ordered.length} signal{ordered.length === 1 ? "" : "s"}
            </span>
          </div>
        </header>

        <section className="rounded-2xl border border-border bg-surface p-4 shadow-card sm:p-5">
          <div className="overflow-x-auto">
            <table className="num w-full min-w-[920px] text-sm">
              <thead className="text-left text-xs uppercase text-muted">
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
              <tbody className="divide-y divide-border">
                {ordered.length === 0 && (
                  <tr>
                    <td colSpan={7} className="py-10 text-center text-muted">
                      No detector output received. Transactions remain unlabelled until the
                      sliding-window model produces a review signal.
                    </td>
                  </tr>
                )}
                {ordered.map((detection) => (
                  <tr key={detection.detectionId} className="align-top">
                    <td className="py-3 font-mono text-xs text-muted">
                      {new Date(detection.simTime).toISOString().slice(11, 19)}
                    </td>
                    <td className="py-3 font-semibold uppercase text-ink">
                      {detection.providerId ?? "cross-provider"}
                    </td>
                    <td className="py-3">
                      <span
                        className={
                          "rounded px-2 py-1 text-xs font-bold " +
                          (detection.riskScore >= 0.8
                            ? "bg-rose-900/40 text-rose-300"
                            : detection.riskScore >= 0.6
                              ? "bg-signal-soft text-signal"
                              : "bg-sky-900/40 text-sky-300")
                        }
                      >
                        {percent(detection.riskScore)} · {detection.severity}
                      </span>
                    </td>
                    <td className="py-3">
                      <div className="font-semibold text-ink">{percent(detection.frequencyScore)}</div>
                      <div className="text-xs text-muted">
                        {detection.transactionCount || "—"} outgoing transactions
                      </div>
                    </td>
                    <td className="py-3">
                      <div className="font-semibold text-ink">
                        {percent(detection.identicalAmountScore)}
                      </div>
                      <div className="text-xs text-muted">
                        {detection.repeatedAmountBdt === null
                          ? "Amount not dominant"
                          : `৳${BDT.format(detection.repeatedAmountBdt)} × ${detection.repeatedAmountFrequency || "—"}`}
                      </div>
                    </td>
                    <td className="py-3">
                      <div className="font-semibold text-ink">
                        {percent(detection.accountClusterScore)}
                      </div>
                      <div className="text-xs text-muted">
                        {detection.accountCount || "—"} linked accounts
                      </div>
                      {detection.accounts.length > 0 && (
                        <div
                          className="num mt-1 max-w-[180px] truncate font-mono text-[11px] text-muted"
                          title={detection.accounts.join(", ")}
                        >
                          {detection.accounts.join(", ")}
                        </div>
                      )}
                    </td>
                    <td className="py-3">
                      <div className="text-xs font-semibold text-ink">
                        {detection.windowMinutes
                          ? `${detection.windowMinutes}-minute lookback`
                          : "Window supplied by detector"}
                      </div>
                      <div className="num mt-1 text-xs text-muted">
                        Velocity {percent(detection.velocityScore)} · cadence{" "}
                        {percent(detection.cadenceScore)} · model confidence{" "}
                        {percent(detection.confidenceScore)}
                      </div>
                      {detection.rationale.length > 0 && (
                        <ul className="mt-1 max-w-xs list-disc pl-4 text-xs text-muted">
                          {detection.rationale.slice(0, 3).map((reason) => (
                            <li key={reason}>{reason}</li>
                          ))}
                        </ul>
                      )}
                      {detection.possibleBenignExplanations.length > 0 && (
                        <details className="mt-2 text-xs text-ink">
                          <summary className="cursor-pointer font-medium text-muted">
                            Possible benign context
                          </summary>
                          <ul className="num mt-1 list-disc pl-4 text-muted">
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

          <p className="num mt-4 rounded-lg border border-signal/30 bg-signal-soft px-3 py-2 text-xs text-ink">
            ⚖ These scores combine observed frequency, repeated-amount concentration, and
            account clustering. They are decision-support signals—not fraud declarations.
            No account is frozen and no money is moved without an authorized human decision.
          </p>
        </section>

        <section className="mt-5 rounded-2xl border border-border bg-surface p-4 shadow-card sm:p-5">
          <h2 className="text-base font-semibold text-ink">Alert cases and notes</h2>
          <p className="text-xs text-muted">
            Notes share one chronological audit timeline with coordination transitions.
          </p>
          {alerts.length === 0 ? (
            <p className="num mt-3 text-sm text-muted">No alert cases available.</p>
          ) : (
            <ul className="mt-2 divide-y divide-border">
              {[...alerts].reverse().map((alert) => (
                <li key={alert.alertToken} className="py-3">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                    <span className="num font-mono text-muted">{alert.alertToken.slice(0, 8)}…</span>
                    <span className="font-semibold uppercase text-ink">
                      {alert.providerId ?? "shared"} · {alert.status}
                    </span>
                  </div>
                  {alert.reason && <p className="num mt-1 text-xs text-muted">{alert.reason}</p>}
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
