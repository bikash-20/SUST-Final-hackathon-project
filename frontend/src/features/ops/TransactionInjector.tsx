"use client";

import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import type { ProviderId } from "../telemetry/types";

type AmountPattern = "varied" | "near_identical";

interface InjectionPayload {
  provider: ProviderId;
  number_of_transactions: number;
  min_amount_bdt: number;
  max_amount_bdt: number;
  amount_pattern: AmountPattern;
  distinct_accounts: number;
  window_seconds: number;
  is_salary_window: boolean;
}

interface InjectionResponse {
  injected: number;
  provider: ProviderId;
  anomaly_outcome: {
    triggered: boolean;
    severity: string;
    risk_score: number;
    confidence: number;
    calendar_adjustment_applied: boolean;
    calendar_context: string;
  };
}

const SALARY_PRESET: InjectionPayload = {
  provider: "bkash",
  number_of_transactions: 24,
  min_amount_bdt: 500,
  max_amount_bdt: 4500,
  amount_pattern: "varied",
  distinct_accounts: 24,
  window_seconds: 300,
  is_salary_window: true,
};

const SUSPICIOUS_PRESET: InjectionPayload = {
  provider: "bkash",
  number_of_transactions: 10,
  min_amount_bdt: 4999,
  max_amount_bdt: 4999,
  amount_pattern: "near_identical",
  distinct_accounts: 3,
  window_seconds: 20,
  is_salary_window: true,
};

async function submitInjection(payload: InjectionPayload): Promise<InjectionResponse> {
  const response = await fetch("/v1/simulation/inject", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null) as {
      error?: string;
      message?: string;
      available_balance?: number;
      detail?: string | { message?: string };
    } | null;
    const detailMessage = typeof error?.detail === "string"
      ? error.detail
      : error?.detail?.message;
    const message = error?.message ?? detailMessage ?? `Injection failed (${response.status})`;
    const available = typeof error?.available_balance === "number"
      ? ` Available shared cash: ৳${error.available_balance.toLocaleString("en-BD")}.`
      : "";
    throw new Error(`${message}${available}`);
  }
  return response.json() as Promise<InjectionResponse>;
}

export function TransactionInjector() {
  const [form, setForm] = useState<InjectionPayload>(SUSPICIOUS_PRESET);
  const injection = useMutation({ mutationFn: submitInjection });

  function update<K extends keyof InjectionPayload>(key: K, value: InjectionPayload[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    injection.mutate(form);
  }

  function runPreset(preset: InjectionPayload) {
    setForm(preset);
    injection.mutate(preset);
  }

  const outcome = injection.data?.anomaly_outcome;
  return (
    <section className="mt-5 rounded-2xl border border-indigo-200 bg-gradient-to-br from-white to-indigo-50/70 p-4 shadow-lg shadow-indigo-900/5 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-indigo-600">
            Live test scenario
          </div>
          <h2 className="mt-1 text-base font-bold text-slate-950">Inject synthetic transactions</h2>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-600">
            Uses the normal ledger, EWMA, anomaly, alert, and SSE pipeline. The salary-period
            option is a simple calendar-aware heuristic—not solved seasonality—and every result
            still requires human review.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={injection.isPending}
            onClick={() => runPreset(SALARY_PRESET)}
            className="rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs font-bold text-emerald-800 transition hover:bg-emerald-100 disabled:opacity-50"
          >
            Simulate salary-day demand
          </button>
          <button
            type="button"
            disabled={injection.isPending}
            onClick={() => runPreset(SUSPICIOUS_PRESET)}
            className="rounded-lg border border-rose-300 bg-rose-50 px-3 py-2 text-xs font-bold text-rose-800 transition hover:bg-rose-100 disabled:opacity-50"
          >
            Simulate suspicious burst
          </button>
        </div>
      </div>

      <form onSubmit={submit} className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8">
        <label className="text-xs font-semibold text-slate-600">
          Provider
          <select
            value={form.provider}
            onChange={(event) => update("provider", event.target.value as ProviderId)}
            className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2 py-2 text-sm"
          >
            <option value="bkash">bKash</option>
            <option value="nagad">Nagad</option>
            <option value="rocket">Rocket</option>
          </select>
        </label>
        <NumberField label="Transactions" value={form.number_of_transactions} min={1} max={200} onChange={(value) => update("number_of_transactions", value)} />
        <NumberField label="Min BDT" value={form.min_amount_bdt} min={0.01} step={0.01} onChange={(value) => update("min_amount_bdt", value)} />
        <NumberField label="Max BDT" value={form.max_amount_bdt} min={0.01} step={0.01} onChange={(value) => update("max_amount_bdt", value)} />
        <label className="text-xs font-semibold text-slate-600">
          Amount pattern
          <select
            value={form.amount_pattern}
            onChange={(event) => update("amount_pattern", event.target.value as AmountPattern)}
            className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2 py-2 text-sm"
          >
            <option value="varied">Varied</option>
            <option value="near_identical">Near-identical</option>
          </select>
        </label>
        <NumberField label="Accounts" value={form.distinct_accounts} min={1} max={200} onChange={(value) => update("distinct_accounts", value)} />
        <NumberField label="Window seconds" value={form.window_seconds} min={1} max={3600} onChange={(value) => update("window_seconds", value)} />
        <div className="flex flex-col justify-end gap-2">
          <label className="flex items-center gap-2 text-xs font-semibold text-slate-700">
            <input
              type="checkbox"
              checked={form.is_salary_window}
              onChange={(event) => update("is_salary_window", event.target.checked)}
              className="h-4 w-4 rounded border-slate-300 text-emerald-600"
            />
            Salary window
          </label>
          <button
            type="submit"
            disabled={injection.isPending}
            className="rounded-lg bg-slate-950 px-3 py-2 text-xs font-bold text-white transition hover:bg-indigo-700 disabled:opacity-50"
          >
            {injection.isPending ? "Injecting…" : "Inject custom"}
          </button>
        </div>
      </form>

      {outcome && (
        <p className={`mt-3 rounded-lg px-3 py-2 text-xs font-semibold ${outcome.triggered ? "bg-rose-100 text-rose-900" : "bg-emerald-100 text-emerald-900"}`}>
          Injected {injection.data?.injected} {injection.data?.provider} transactions · anomaly {outcome.triggered ? `flagged (${outcome.severity})` : "not flagged"} · score {outcome.risk_score.toFixed(2)} · confidence {Math.round(outcome.confidence * 100)}% · {outcome.calendar_context}
        </p>
      )}
      {injection.error && (
        <p className="mt-3 rounded-lg bg-rose-100 px-3 py-2 text-xs text-rose-900">
          {injection.error.message}
        </p>
      )}
    </section>
  );
}

function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min: number;
  max?: number;
  step?: number;
}) {
  return (
    <label className="text-xs font-semibold text-slate-600">
      {label}
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        required
        onChange={(event) => onChange(Number(event.target.value))}
        className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2 py-2 text-sm"
      />
    </label>
  );
}
