"use client";
/** Sticky top-bar with the Multi-Role Context Switcher.
 *  The role pill is sticky and never reloads data when toggled.
 */
import type { ReactNode } from "react";
import Image from "next/image";
import { useRoleStore, type Role } from "./roleStore";
import { ThemeToggle } from "./ThemeToggle";
import { useTelemetryStore } from "../telemetry/useTelemetryStream";

const ROLES: { id: Role; label: string; sub: string }[] = [
  { id: "agent", label: "Agent Mobile",  sub: "বিকাশ/নগদ/রকেট" },
  { id: "ops",   label: "Ops Web",       sub: "TTE + Tickets" },
  { id: "risk",  label: "Risk Reviewer", sub: "Velocity Anomaly" },
];

export function Shell({ children }: { children: ReactNode }) {
  const role = useRoleStore((s) => s.role);
  const setRole = useRoleStore((s) => s.setRole);
  const connectionState = useTelemetryStore((s) => s.connectionState);
  const lastEventId = useTelemetryStore((s) => s.lastEventId);
  return (
    <div className="flex min-h-screen flex-col bg-base text-ink">
      <header className="sticky top-0 z-40 border-b border-border bg-surface/95 shadow-card backdrop-blur-xl">
        <div className="mx-auto flex max-w-screen-2xl flex-col gap-3 px-3 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5">
          <div className="flex items-center gap-3">
            <Image
              src="/icons/icon-192.png"
              alt="LiquiGuard shield"
              width={40}
              height={40}
              priority
              className="rounded-xl shadow-card"
            />
            <div>
              <div className="text-base font-bold tracking-tight text-ink">LiquiGuard</div>
              <div className="text-[11px] font-medium text-muted sm:text-xs">
                Multi-provider liquidity command center
              </div>
            </div>
            <span
              className={
                "ml-2 inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-eyebrow " +
                (connectionState === "connected"
                  ? "border-emerald-700/40 bg-emerald-900/20 text-emerald-700"
                  : "border-signal/40 bg-signal-soft text-signal")
              }
              aria-label={`Stream ${connectionState}, cursor ${lastEventId}`}
              title={`SSE stream ${connectionState} · last event #${lastEventId}`}
            >
              <span
                className={
                  "h-1.5 w-1.5 rounded-full " +
                  (connectionState === "connected" ? "bg-emerald-400" : "live-dot")
                }
                aria-hidden
              />
              Live evidence · #{lastEventId}
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <nav className="flex w-full gap-1 overflow-x-auto rounded-xl bg-surface-2 p-1 ring-1 ring-inset ring-border sm:w-auto" aria-label="Role switcher">
              {ROLES.map((r) => {
                const active = r.id === role;
                return (
                  <button
                    key={r.id}
                    type="button"
                    aria-pressed={active}
                    onClick={() => setRole(r.id)}
                    className={
                      "min-w-max flex-1 rounded-lg px-3 py-2 text-xs font-semibold transition sm:flex-none sm:text-sm " +
                      (active
                        ? "bg-base text-ink shadow-card"
                        : "text-muted hover:bg-base hover:text-ink")
                    }
                  >
                    <span>{r.label}</span>
                    <span className="ml-2 hidden text-xs opacity-70 md:inline">{r.sub}</span>
                  </button>
                );
              })}
            </nav>
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main className="flex-1 pb-8">{children}</main>
    </div>
  );
}
