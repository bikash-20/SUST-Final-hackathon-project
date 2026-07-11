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
import { SafeFallbackLayout } from "../safety/SafeFallbackLayout";
import { useTelemetryStore } from "../telemetry/useTelemetryStream";
import { PROVIDER_IDS, type ProviderId } from "../telemetry/types";
import { TransactionInjector } from "./TransactionInjector";

const PROVIDER_LABEL: Record<ProviderId, string> = {
  bkash: "bKash",
  nagad: "Nagad",
  rocket: "Rocket",
};

const PROVIDER_COLOR: Record<ProviderId, string> = {
  bkash: "#e2136e",
  nagad: "#f97316",
  rocket: "#6d28d9",
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
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200/80 bg-white/90 px-5 py-4 shadow-lg shadow-slate-900/5 backdrop-blur">
          <div>
            <h1 className="text-lg font-bold tracking-tight text-slate-950">Liquidity operations cockpit</h1>
            <p className="text-xs text-slate-500">
              Provider ledgers, live EWMA forecasts, and human coordination
            </p>
          </div>
          <div
            className={
              "rounded-full border px-3 py-1.5 text-xs font-bold " +
              (connectionState === "connected"
                ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                : "border-amber-200 bg-amber-50 text-amber-900")
            }
          >
            {connectionState === "connected" ? "● Live" : `○ ${connectionError ?? "Connecting"}`}
          </div>
        </div>

        <AdvisoryCard />

        <TransactionInjector />

        <section className="mt-5 rounded-2xl border border-slate-200/80 bg-white p-4 shadow-lg shadow-slate-900/5 sm:p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-base font-semibold text-slate-950">Forecast inputs</h2>
              <p className="text-xs text-slate-500">
                Live EWMA remains the prediction engine; committed history adds context.
              </p>
            </div>
            <span className="rounded-full bg-indigo-50 px-3 py-1 text-[11px] font-semibold text-indigo-700">
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
              <div key={label} className="rounded-xl bg-slate-50 px-3 py-3 ring-1 ring-inset ring-slate-200/70">
                <dt className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</dt>
                <dd className="mt-1 text-sm font-bold text-slate-900">{value}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-2xl bg-slate-950 p-5 text-white shadow-xl shadow-slate-900/15">
            <div className="text-xs uppercase tracking-wide text-slate-300">
              Shared physical cash
            </div>
            <div className="mt-2 text-2xl font-bold">
              {sharedCash ? `৳${BDT.format(sharedCash.balanceBdt)}` : "—"}
            </div>
            <div className="mt-1 text-[11px] text-slate-400">
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
                className="rounded-2xl border border-slate-200 bg-white p-5 shadow-lg shadow-slate-900/5"
                style={{ borderTopColor: PROVIDER_COLOR[providerId], borderTopWidth: 3 }}
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase text-slate-500">
                    {PROVIDER_LABEL[providerId]} e-money
                  </span>
                  <span
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ backgroundColor: PROVIDER_COLOR[providerId] }}
                  />
                </div>
                <div className="mt-2 text-2xl font-bold">
                  {balance ? `৳${BDT.format(balance.balanceBdt)}` : "—"}
                </div>
                <div className="mt-1 text-xs text-slate-500">
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
            <section className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-lg shadow-slate-900/5 sm:p-5">
              <header className="mb-4 flex flex-wrap items-start justify-between gap-2">
                <div>
                  <h2 className="text-base font-semibold">Live provider balance trajectory</h2>
                  <p className="text-xs text-slate-500">
                    Every point is a persisted balance returned by the simulation engine.
                  </p>
                </div>
                {criticalForecast?.predictedTteMin !== null &&
                  criticalForecast?.predictedTteMin !== undefined && (
                    <div className="rounded-lg bg-amber-100 px-3 py-2 text-right text-xs text-amber-950">
                      <div className="font-semibold">
                        Earliest TTE: {criticalForecast.predictedTteMin.toFixed(1)} min
                      </div>
                      <div>
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
                <div className="flex h-72 items-center justify-center rounded-lg border border-dashed border-slate-300 text-sm text-slate-500">
                  Waiting for provider balance updates — no synthetic chart data is shown.
                </div>
              ) : (
                <div className="h-72 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={chartData} margin={{ left: 12, right: 20 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                      <XAxis dataKey="clock" tick={{ fontSize: 11 }} />
                      <YAxis
                        tick={{ fontSize: 11 }}
                        tickFormatter={(value: number) => `৳${BDT.format(value)}`}
                        width={82}
                      />
                      <Tooltip
                        formatter={(value: number) => [`৳${BDT.format(value)}`, "Balance"]}
                      />
                      <Legend />
                      <ReferenceLine y={0} stroke="#64748b" />
                      {PROVIDER_IDS.map((providerId) => (
                        <Line
                          key={providerId}
                          type="monotone"
                          dataKey={providerId}
                          name={PROVIDER_LABEL[providerId]}
                          stroke={PROVIDER_COLOR[providerId]}
                          strokeWidth={2}
                          connectNulls
                          dot={{ r: 2 }}
                          isAnimationActive={false}
                        />
                      ))}
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </section>

            <section className="mt-5 rounded-2xl border border-slate-200/80 bg-white p-4 shadow-lg shadow-slate-900/5 sm:p-5">
              <h2 className="text-base font-semibold">EWMA forecast evidence</h2>
              {forecasts.length === 0 ? (
                <p className="mt-3 rounded-lg bg-slate-50 p-3 text-sm text-slate-500">
                  Forecasts appear after enough real balance deltas have entered the EWMA window.
                </p>
              ) : (
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full min-w-[640px] text-sm">
                    <thead className="text-left text-xs uppercase text-slate-500">
                      <tr>
                        <th className="py-2">Ledger</th>
                        <th>TTE</th>
                        <th>95% interval</th>
                        <th>EWMA outflow/min</th>
                        <th>Samples</th>
                        <th>Confidence</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {forecasts.map((forecast) => (
                        <tr key={forecast.key}>
                          <td className="py-3 font-semibold">
                            {forecast.providerId
                              ? PROVIDER_LABEL[forecast.providerId]
                              : forecast.key}
                          </td>
                          <td>
                            {forecast.predictedTteMin === null
                              ? "Stable / insufficient trend"
                              : `${forecast.predictedTteMin.toFixed(1)} min`}
                          </td>
                          <td>
                            {forecast.ci95
                              ? `${forecast.ci95[0].toFixed(1)}–${forecast.ci95[1].toFixed(1)} min`
                              : "—"}
                          </td>
                          <td>
                            {forecast.ewmaOutflowBdtPerMin === null
                              ? "—"
                              : `৳${BDT.format(forecast.ewmaOutflowBdtPerMin)}`}
                          </td>
                          <td>{forecast.sampleCount ?? "—"}</td>
                          <td>
                            {(forecast.confidenceScoreWithHistory ?? forecast.confidenceScore) === null
                              ? "—"
                              : `${Math.round((forecast.confidenceScoreWithHistory ?? forecast.confidenceScore ?? 0) * 100)}%`}
                            {forecast.confidenceScoreWithHistory !== null &&
                              forecast.confidenceScore !== null &&
                              forecast.confidenceScoreWithHistory !== forecast.confidenceScore && (
                                <div className="text-[10px] text-slate-500">
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
            <section className="rounded-2xl border border-slate-200/80 bg-white p-4 shadow-lg shadow-slate-900/5">
              <header className="mb-2 flex items-center justify-between">
                <div>
                  <h2 className="text-base font-semibold">Coordination queue</h2>
                  <p className="text-xs text-slate-500">Audited FSM transitions</p>
                </div>
                <span className="rounded bg-slate-100 px-2 py-1 text-xs font-semibold">
                  {openAlerts.length} open
                </span>
              </header>

              {alerts.length === 0 && (
                <p className="mt-3 rounded-lg bg-slate-50 p-3 text-sm text-slate-500">
                  No coordination alerts received.
                </p>
              )}
              <ul className="divide-y divide-slate-100">
                {[...alerts].reverse().map((alert) => (
                  <li key={alert.alertToken} className="py-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="truncate font-mono text-xs" title={alert.alertToken}>
                        {alert.alertToken.slice(0, 8)}…
                      </div>
                      <span
                        className={
                          "rounded px-2 py-0.5 text-[11px] font-semibold uppercase " +
                          (alert.status === "PENDING"
                            ? "bg-amber-200 text-amber-950"
                            : alert.status === "ACKNOWLEDGED"
                              ? "bg-sky-200 text-sky-950"
                              : alert.status === "ESCALATED"
                                ? "bg-rose-200 text-rose-950"
                              : "bg-emerald-200 text-emerald-950")
                        }
                      >
                        {alert.status}
                      </span>
                    </div>
                    <div className="mt-1 text-xs text-slate-600">
                      {alert.severity} · {alert.providerId ?? "shared"}
                    </div>
                    {alert.reason && (
                      <div className="mt-1 text-xs text-slate-500">{alert.reason}</div>
                    )}
                    {alert.transitions.length > 0 && (
                      <div className="mt-2 rounded bg-slate-50 px-2 py-1 text-[11px] text-slate-600">
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
                        className="rounded bg-sky-600 px-2 py-1 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
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
                        className="rounded bg-rose-600 px-2 py-1 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
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
                        className="rounded bg-emerald-600 px-2 py-1 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        Resolve
                      </button>
                    </div>
                    <CaseTimeline caseId={alert.alertToken} authorRole="Ops" />
                  </li>
                ))}
              </ul>
              {transit.error && (
                <p className="mt-3 rounded bg-rose-50 p-2 text-xs text-rose-800">
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
