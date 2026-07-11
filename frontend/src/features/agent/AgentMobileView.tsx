"use client";

import { useEffect, useState } from "react";
import { AdvisoryCard } from "../advisory/AdvisoryCard";
import { SafeFallbackLayout } from "../safety/SafeFallbackLayout";
import { useRoleStore, type ProviderTab } from "../shell/roleStore";
import { useTelemetryStore } from "../telemetry/useTelemetryStream";
import type { BalanceReading } from "../telemetry/types";

const TABS: ProviderTab[] = ["bkash", "nagad", "rocket"];
const TAB_LABEL: Record<ProviderTab, string> = {
  bkash: "bKash",
  nagad: "Nagad",
  rocket: "Rocket",
};
const TAB_STYLE: Record<ProviderTab, { active: string; idle: string; dot: string }> = {
  bkash: {
    active: "border-bkash bg-bkash text-white shadow-lg shadow-pink-900/15",
    idle: "border-pink-200 bg-white text-slate-900 hover:border-bkash",
    dot: "bg-bkash",
  },
  nagad: {
    active: "border-nagad bg-nagad text-slate-950 shadow-lg shadow-orange-900/15",
    idle: "border-orange-200 bg-white text-slate-900 hover:border-nagad",
    dot: "bg-nagad",
  },
  rocket: {
    active: "border-rocket bg-rocket text-white shadow-lg shadow-purple-900/15",
    idle: "border-purple-200 bg-white text-slate-900 hover:border-rocket",
    dot: "bg-rocket",
  },
};

const BDT = new Intl.NumberFormat("en-BD", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function balanceText(reading: BalanceReading | undefined | null): string {
  return reading ? `৳ ${BDT.format(reading.balanceBdt)}` : "Waiting for live data";
}

function freshnessText(reading: BalanceReading | undefined | null, now: number): string {
  if (!reading) return "No reading received";
  const received = Date.parse(reading.receivedAt);
  if (!Number.isFinite(received)) return "Freshness unavailable";
  const seconds = Math.max(0, Math.floor((now - received) / 1_000));
  if (seconds < 2) return "Updated just now";
  if (seconds < 60) return `Updated ${seconds}s ago`;
  return `Updated ${Math.floor(seconds / 60)}m ago`;
}

function confidenceText(
  reading: BalanceReading | undefined | null,
  fallback: number | null,
): string {
  const confidence = reading?.confidenceScore ?? fallback;
  return confidence === null ? "Confidence not reported" : `${Math.round(confidence * 100)}% confidence`;
}

export function AgentMobileView() {
  const [now, setNow] = useState(() => Date.now());
  const provider = useRoleStore((state) => state.provider);
  const setProvider = useRoleStore((state) => state.setProvider);
  const simTime = useTelemetryStore((state) => state.simTime);
  const degraded = useTelemetryStore((state) => state.degraded);
  const confidence = useTelemetryStore((state) => state.confidenceScore);
  const connectionState = useTelemetryStore((state) => state.connectionState);
  const sharedCash = useTelemetryStore((state) => state.sharedCashBalance);
  const providerBalances = useTelemetryStore((state) => state.providerBalances);
  const txns = useTelemetryStore((state) => state.providerTxns);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  const my = txns
    .filter((transaction) => transaction.providerId === provider)
    .slice(-10)
    .reverse();

  return (
    <SafeFallbackLayout>
      <div className="mx-auto max-w-lg px-3 py-5 sm:px-5 sm:py-7">
        <div
          className={
            "mb-4 flex items-center justify-between rounded-xl border px-3 py-2 text-xs font-semibold shadow-sm " +
            (connectionState === "connected"
              ? "border-emerald-200 bg-emerald-50/90 text-emerald-800"
              : "border-amber-200 bg-amber-50/90 text-amber-900")
          }
        >
          <span>
            <span className="mr-2" aria-hidden>
              {connectionState === "connected" ? "●" : "○"}
            </span>
            {connectionState === "connected" ? "Live telemetry" : "Telemetry reconnecting"}
          </span>
          <code>{simTime ? new Date(simTime).toISOString().slice(11, 19) : "—"}</code>
        </div>

        <AdvisoryCard />

        <section className="relative mt-4 overflow-hidden rounded-2xl bg-slate-950 p-5 text-white shadow-xl shadow-slate-900/15">
          <div className="absolute -right-8 -top-12 h-32 w-32 rounded-full bg-emerald-400/15 blur-2xl" />
          <div className="relative text-[11px] font-bold uppercase tracking-[0.14em] text-emerald-300">
            Shared physical cash drawer
          </div>
          <div className="relative mt-2 text-3xl font-bold tracking-tight sm:text-4xl">
            {balanceText(sharedCash)}
          </div>
          <div className="relative mt-3 flex flex-wrap justify-between gap-2 text-[11px] text-slate-300">
            <span>{freshnessText(sharedCash, now)}</span>
            <span>{confidenceText(sharedCash, confidence)}</span>
          </div>
          {degraded && (
            <div className="mt-3 rounded-lg bg-amber-100 p-2 text-xs font-medium text-amber-950">
              ⚠ ডেটা অনিশ্চিত — নতুন ক্যাশ-আউটের আগে অপারেশনস টিমের সাথে যাচাই করুন
            </div>
          )}
        </section>

        <section className="mt-5" aria-label="Provider e-money positions">
          <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.12em] text-slate-500">
            Provider e-money — separate ledgers
          </div>
          <div className="grid grid-cols-3 gap-2">
            {TABS.map((providerId) => {
              const reading = providerBalances[providerId];
              const active = providerId === provider;
              return (
                <button
                  key={providerId}
                  type="button"
                  onClick={() => setProvider(providerId)}
                  aria-pressed={active}
                  className={
                    "min-w-0 rounded-2xl border px-2.5 py-3 text-left shadow-sm transition duration-200 " +
                    (active ? TAB_STYLE[providerId].active : TAB_STYLE[providerId].idle)
                  }
                >
                  <span className="flex items-center gap-1.5 text-xs font-bold">
                    <span className={`h-2 w-2 rounded-full ${active ? "bg-white/80" : TAB_STYLE[providerId].dot}`} />
                    {TAB_LABEL[providerId]}
                  </span>
                  <span className="mt-1 block truncate text-sm font-bold">
                    {reading ? `৳${BDT.format(reading.balanceBdt)}` : "—"}
                  </span>
                  <span
                    className={
                      "mt-1 block truncate text-[10px] " +
                      (active ? "text-slate-300" : "text-slate-500")
                    }
                  >
                    {freshnessText(reading, now)}
                  </span>
                </button>
              );
            })}
          </div>
        </section>

        <section className="mt-5 rounded-2xl border border-slate-200/80 bg-white p-4 shadow-lg shadow-slate-900/5">
          <div className="mb-2 flex items-center justify-between">
            <div className="text-[11px] font-bold uppercase tracking-[0.1em] text-slate-500">
              Recent activity — {TAB_LABEL[provider]}
            </div>
            <div className="text-[11px] text-slate-500">
              {confidenceText(providerBalances[provider], confidence)}
            </div>
          </div>
          <ul className="divide-y divide-slate-100 text-sm">
            {my.length === 0 && (
              <li className="py-4 text-center text-slate-500">
                No live transactions received yet.
              </li>
            )}
            {my.map((transaction) => (
              <li
                key={transaction.transactionId}
                className="flex items-center justify-between gap-3 py-3"
              >
                <div>
                  <div className="font-medium">{transaction.counterpartyMsisdn}</div>
                  <div className="text-[11px] text-slate-500">
                    {new Date(transaction.simTime).toISOString().slice(11, 19)}
                  </div>
                </div>
                <div
                  className={
                    transaction.direction === "out"
                      ? "font-semibold text-rose-600"
                      : "font-semibold text-emerald-700"
                  }
                >
                  {transaction.direction === "out" ? "−" : "+"}৳
                  {BDT.format(transaction.amountBdt)}
                </div>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </SafeFallbackLayout>
  );
}
