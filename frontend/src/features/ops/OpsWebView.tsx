"use client";

import { useMutation } from "@tanstack/react-query";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AdvisoryCard } from "../advisory/AdvisoryCard";
import { CaseTimeline } from "../coordination/CaseTimeline";
import { LiveEvidencePanel } from "../debug/LiveEvidencePanel";
import { HistoricalContextCard } from "../historical/HistoricalContextCard";
import { SafeFallbackLayout } from "../safety/SafeFallbackLayout";
import { useTelemetryStore } from "../telemetry/useTelemetryStream";
import { PROVIDER_IDS, type ProviderId } from "../telemetry/types";
import { TransactionInjector } from "./TransactionInjector";

const PROVIDER_LABEL: Record<ProviderId, string> = {
  bkash: "bKash",
  nagad: "Nagad",
  rocket: "Rocket",
};

// Desaturated palette — matches the dark "trading terminal" tokens
// declared in tailwind.config.js (bkash / nagad / rocket).
const PROVIDER_COLOR: Record<ProviderId, string> = {
  bkash: "#E0447A",
  nagad: "#E0883B",
  rocket: "#8B7FE8",
};

const PROVIDER_HAIRLINE: Record<ProviderId, string> = {
  bkash: "hairline-bkash",
  nagad: "hairline-nagad",
  rocket: "hairline-rocket",
};

const BDT = new Intl.NumberFormat("en-BD", { maximumFractionDigits: 0 });

interface TransitResponse {
  alert_token: string;
  status: "ACKNOWLEDGED" | "ESCALATED" | "RESOLVED";
  severity: string;
  provider_id: string | null;
  sim_time: string;
  transitions: unknown[];
}

export function OpsWebView() {
  const alerts = useTelemetryStore((state) => state.alerts);
  const forecastsByKey = useTelemetryStore((state) => state.liquidityForecasts);
  const historical = useTelemetryStore((state) => state.historicalAnalytics);
  const history = useTelemetryStore((state) => state.balanceHistory);
  const sharedCash = useTelemetryStore((state) => state.sharedCashBalance);
  const providerBalances = useTelemetryStore((state) => state.providerBalances);
  const connectionState = useTelemetryStore((state) => state.connectionState);
  const connectionError = useTelemetryStore((state) => state.connectionError);

  const forecasts = Object.values(forecastsByKey).sort((left, right) => {
    if (left.predictedTteMin === null) return 1;
    if (right.predictedTteMin === null) return -1;
    return left.predictedTteMin - right.predictedTteMin;
  });
  const criticalForecast = forecasts.find(
    (forecast) => forecast.predictedTteMin !== null,
  );
  const openAlerts = alerts.filter((alert) => alert.status !== "RESOLVED");

  const chartRows = new Map<string, Record<string, string | number>>();
  for (const point of history) {
    const row = chartRows.get(point.simTime) ?? {
      simTime: point.simTime,
      clock: new Date(point.simTime).toISOString().slice(11, 19),
    };
    row[point.providerId] = point.balanceBdt;
    chartRows.set(point.simTime, row);
  }
  const chartData = [...chartRows.values()].slice(-120);

  const transit = useMutation({
    mutationFn: async (args: {
      token: string;
      to: "ACKNOWLEDGED" | "ESCALATED" | "RESOLVED";
    }): Promise<TransitResponse> => {
      const response = await fetch("/v1/coordination/transit", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          alert_token: args.token,
          to: args.to,
          actor: "ops_demo",
          reason: `${args.to} via Ops Web after human review`,
        }),
      });
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(detail || `Transition failed (${response.status})`);
      }
      return response.json() as Promise<TransitResponse>;
    },
    onSuccess: (result) => {
      // Apply the authoritative REST response immediately; the SSE broadcast
      // will merge the same transition idempotently when it arrives.
      useTelemetryStore
        .getState()
        .ingest(`coordination.${result.status}`, result);
    },
  });

  return (
    <SafeFallbackLayout>
      <div className="mx-auto max-w-screen-2xl px-3 py-5 sm:px-5 sm:py-7">
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-surface px-5 py-4 shadow-card">
          <div>
            <h1 className="text-lg font-bold tracking-tight text-ink">Liquidity operations cockpit</h1>
            <p className="text-xs text-muted">
              Provider ledgers, live EWMA forecasts, and human coordination
            </p>
          </div>
          <div
            className={
              "flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-bold " +
              (connectionState === "connected"
                ? "border-emerald-700/40 bg-emerald-900/30 text-emerald-300"
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
            {connectionState === "connected" ? "Live" : (connectionError ?? "Connecting")}
          </div>
        </div>

        <HistoricalContextCard />

        <LiveEvidencePanel variant="full" />

        <AdvisoryCard />

        <TransactionInjector />

        <section className="mt-5 rounded-2xl border border-border bg-surface p-4 shadow-card sm:p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-base font-semibold text-ink">Forecast inputs</h2>
              <p className="text-xs text-muted">
                Live EWMA remains the prediction engine; committed history adds context.
              </p>
            </div>
            <span className="rounded-full bg-signal-soft px-3 py-1 text-[11px] font-semibold tracking-eyebrow text-signal">
              Deterministic · no ML
            </span>
          </div>
          <dl className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
            {[
              ["Historical window", historical ? `${historical.historicalWindowDays} days` : "Loading…"],
              ["Live window", "12 minutes"],
              ["Historical records", historical ? BDT.format(historical.historicalTransactions) : "Loading…"],
              ["Prediction model", "EWMA + historical context"],
            ].map(([label, value]) => (
              <div key={label} className="rounded-xl bg-surface-2 px-3 py-3 ring-1 ring-inset ring-border">
                <dt className="eyebrow">{label}</dt>
                <dd className="num mt-1 text-sm font-bold text-ink">{value}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div className="relative overflow-hidden rounded-2xl border border-border bg-surface p-5 shadow-card">
            <div className="absolute inset-x-0 top-0 h-[2px] bg-signal" aria-hidden />
            <div className="eyebrow">
              Shared physical cash
            </div>
            <div className="num mt-2 text-3xl font-bold text-ink">
              {sharedCash ? `৳${BDT.format(sharedCash.balanceBdt)}` : "—"}
            </div>
            <div className="mt-1 text-[11px] text-muted">
              {sharedCash ? new Date(sharedCash.simTime).toISOString() : "Awaiting snapshot"}
            </div>
          </div>
          {PROVIDER_IDS.map((providerId) => {
            const balance = providerBalances[providerId];
            const forecast = forecasts.find(
              (item) => item.providerId === providerId,
            );
            return (
              <div
                key={providerId}
                className="relative overflow-hidden rounded-2xl border border-border bg-surface p-5 shadow-card"
              >
                <div
                  className={`absolute inset-x-0 top-0 h-[2px] ${PROVIDER_HAIRLINE[providerId]}`}
                  aria-hidden
                />
                <div className="flex items-center justify-between">
                  <span className="eyebrow">
                    {PROVIDER_LABEL[providerId]} e-money
                  </span>
                  <span
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ backgroundColor: PROVIDER_COLOR[providerId] }}
                  />
                </div>
                <div className="num mt-2 text-3xl font-bold text-ink">
                  {balance ? `৳${BDT.format(balance.balanceBdt)}` : "—"}
                </div>
                <div className="num mt-1 text-xs text-muted">
                  {forecast?.predictedTteMin !== null &&
                  forecast?.predictedTteMin !== undefined
                    ? `TTE ${forecast.predictedTteMin.toFixed(1)} min`
                    : "TTE calculating"}
                </div>
              </div>
            );
          })}
        </section>

        <div className="mt-5 gap-5 lg:grid lg:grid-cols-3">
          <div className="lg:col-span-2">
            <section className="rounded-2xl border border-border bg-surface p-4 shadow-card sm:p-5">
              <header className="mb-4 flex flex-wrap items-start justify-between gap-2">
                <div>
                  <h2 className="text-base font-semibold text-ink">Live provider balance trajectory</h2>
                  <p className="text-xs text-muted">
                    Every point is a persisted balance returned by the simulation engine.
                  </p>
                </div>
                {criticalForecast?.predictedTteMin !== null &&
                  criticalForecast?.predictedTteMin !== undefined && (
                    <div className="rounded-lg border border-signal/40 bg-signal-soft px-3 py-2 text-right text-xs">
                      <div className="num font-semibold text-signal">
                        Earliest TTE: {criticalForecast.predictedTteMin.toFixed(1)} min
                      </div>
                      <div className="text-muted">
                        {criticalForecast.providerId
                          ? PROVIDER_LABEL[criticalForecast.providerId]
                          : "Aggregate forecast"}
                        {criticalForecast.ci95
                          ? ` · 95% CI ${criticalForecast.ci95[0].toFixed(1)}–${criticalForecast.ci95[1].toFixed(1)}`
                          : ""}
                      </div>
                    </div>
                  )}
              </header>

              {chartData.length === 0 ? (
                <div className="flex h-72 items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted">
                  Waiting for provider balance updates — no synthetic chart data is shown.
                </div>
              ) : (
                <div className="h-72 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={chartData} margin={{ left: 12, right: 20 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                      <XAxis
                        dataKey="clock"
                        tick={{ fontSize: 11, fill: "#8B95A1" }}
                        stroke="rgba(255,255,255,0.1)"
                      />
                      <YAxis
                        tick={{ fontSize: 11, fill: "#8B95A1" }}
                        stroke="rgba(255,255,255,0.1)"
                        tickFormatter={(value: number) => `৳${BDT.format(value)}`}
                        width={82}
                      />
                      <Tooltip
                        contentStyle={{
                          background: "#1B232D",
                          border: "1px solid #262F3B",
                          borderRadius: 8,
                          color: "#E8EAED",
                          fontFamily: "var(--font-plex-mono), monospace",
                        }}
                        labelStyle={{ color: "#8B95A1" }}
                        formatter={(value: number) => [`৳${BDT.format(value)}`, "Balance"]}
                      />
                      <Legend wrapperStyle={{ color: "#8B95A1", fontSize: 11 }} />
                      <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" />
                      {PROVIDER_IDS.map((providerId) => (
                        <Line
                          key={providerId}
                          type="monotone"
                          dataKey={providerId}
                          name={PROVIDER_LABEL[providerId]}
                          stroke={PROVIDER_COLOR[providerId]}
                          strokeWidth={2}
                          connectNulls
                          dot={{ r: 2, fill: PROVIDER_COLOR[providerId] }}
                          isAnimationActive={false}
                        />
                      ))}
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </section>

            <section className="mt-5 rounded-2xl border border-border bg-surface p-4 shadow-card sm:p-5">
              <h2 className="text-base font-semibold text-ink">EWMA forecast evidence</h2>
              {forecasts.length === 0 ? (
                <p className="num mt-3 rounded-lg bg-surface-2 p-3 text-sm text-muted">
                  Forecasts appear after enough real balance deltas have entered the EWMA window.
                </p>
              ) : (
                <div className="mt-3 overflow-x-auto">
                  <table className="num w-full min-w-[640px] text-sm">
                    <thead className="text-left text-xs uppercase text-muted">
                      <tr>
                        <th className="py-2">Ledger</th>
                        <th>TTE</th>
                        <th>95% interval</th>
                        <th>EWMA outflow/min</th>
                        <th>Samples</th>
                        <th>Confidence</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {forecasts.map((forecast) => (
                        <tr key={forecast.key}>
                          <td className="py-3 font-semibold text-ink">
                            {forecast.providerId
                              ? PROVIDER_LABEL[forecast.providerId]
                              : forecast.key}
                          </td>
                          <td className={forecast.predictedTteMin !== null ? "text-signal" : "text-muted"}>
                            {forecast.predictedTteMin === null
                              ? "Stable / insufficient trend"
                              : `${forecast.predictedTteMin.toFixed(1)} min`}
                          </td>
                          <td className="text-ink">
                            {forecast.ci95
                              ? `${forecast.ci95[0].toFixed(1)}–${forecast.ci95[1].toFixed(1)} min`
                              : "—"}
                          </td>
                          <td className="text-ink">
                            {forecast.ewmaOutflowBdtPerMin === null
                              ? "—"
                              : `৳${BDT.format(forecast.ewmaOutflowBdtPerMin)}`}
                          </td>
                          <td className="text-ink">{forecast.sampleCount ?? "—"}</td>
                          <td className="text-ink">
                            {(forecast.confidenceScoreWithHistory ?? forecast.confidenceScore) === null
                              ? "—"
                              : `${Math.round((forecast.confidenceScoreWithHistory ?? forecast.confidenceScore ?? 0) * 100)}%`}
                            {forecast.confidenceScoreWithHistory !== null &&
                              forecast.confidenceScore !== null &&
                              forecast.confidenceScoreWithHistory !== forecast.confidenceScore && (
                                <div className="text-[10px] text-muted">
                                  live-only {Math.round(forecast.confidenceScore * 100)}%
                                </div>
                              )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </div>

          <aside className="mt-4 lg:mt-0">
            <section className="rounded-2xl border border-border bg-surface p-4 shadow-card">
              <header className="mb-2 flex items-center justify-between">
                <div>
                  <h2 className="text-base font-semibold text-ink">Coordination queue</h2>
                  <p className="text-xs text-muted">Audited FSM transitions</p>
                </div>
                <span className="num rounded bg-surface-2 px-2 py-1 text-xs font-semibold text-ink">
                  {openAlerts.length} open
                </span>
              </header>

              {alerts.length === 0 && (
                <p className="num mt-3 rounded-lg bg-surface-2 p-3 text-sm text-muted">
                  No coordination alerts received.
                </p>
              )}
              <ul className="divide-y divide-border">
                {[...alerts].reverse().map((alert) => (
                  <li key={alert.alertToken} className="py-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="num truncate font-mono text-xs text-muted" title={alert.alertToken}>
                        {alert.alertToken.slice(0, 8)}…
                      </div>
                      <span
                        className={
                          "rounded px-2 py-0.5 text-[11px] font-semibold uppercase tracking-eyebrow " +
                          (alert.status === "PENDING"
                            ? "bg-signal-soft text-signal"
                            : alert.status === "ACKNOWLEDGED"
                              ? "bg-sky-900/40 text-sky-300"
                              : alert.status === "ESCALATED"
                                ? "bg-rose-900/40 text-rose-300"
                                : "bg-emerald-900/40 text-emerald-300")
                        }
                      >
                        {alert.status}
                      </span>
                    </div>
                    <div className="mt-1 text-xs text-ink">
                      {alert.severity} · {alert.providerId ?? "shared"}
                    </div>
                    {alert.reason && (
                      <div className="mt-1 text-xs text-muted">{alert.reason}</div>
                    )}
                    {alert.transitions.length > 0 && (
                      <div className="num mt-2 rounded bg-surface-2 px-2 py-1 text-[11px] text-ink">
                        {alert.transitions.length} audited transition
                        {alert.transitions.length === 1 ? "" : "s"} · last by{" "}
                        {alert.transitions.at(-1)?.by}
                      </div>
                    )}
                    <div className="mt-3 flex flex-wrap gap-2">
                      <button
                        type="button"
                        disabled={alert.status !== "PENDING" || transit.isPending}
                        onClick={() =>
                          transit.mutate({
                            token: alert.alertToken,
                            to: "ACKNOWLEDGED",
                          })
                        }
                        className="num rounded bg-sky-700 px-2 py-1 text-xs font-semibold text-white hover:bg-sky-600 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        Acknowledge
                      </button>
                      <button
                        type="button"
                        disabled={
                          !["PENDING", "ACKNOWLEDGED"].includes(alert.status) ||
                          transit.isPending
                        }
                        onClick={() =>
                          transit.mutate({ token: alert.alertToken, to: "ESCALATED" })
                        }
                        className="num rounded bg-rose-700 px-2 py-1 text-xs font-semibold text-white hover:bg-rose-600 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        Escalate
                      </button>
                      <button
                        type="button"
                        disabled={
                          !["ACKNOWLEDGED", "ESCALATED"].includes(alert.status) ||
                          transit.isPending
                        }
                        onClick={() =>
                          transit.mutate({ token: alert.alertToken, to: "RESOLVED" })
                        }
                        className="num rounded bg-emerald-700 px-2 py-1 text-xs font-semibold text-white hover:bg-emerald-600 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        Resolve
                      </button>
                    </div>
                    <CaseTimeline caseId={alert.alertToken} authorRole="Ops" />
                  </li>
                ))}
              </ul>
              {transit.error && (
                <p className="num mt-3 rounded bg-rose-900/30 p-2 text-xs text-rose-300">
                  {transit.error.message}
                </p>
              )}
            </section>
          </aside>
        </div>
      </div>
    </SafeFallbackLayout>
  );
}
