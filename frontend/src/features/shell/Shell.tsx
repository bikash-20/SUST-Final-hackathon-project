"use client";
/** Sticky top-bar with the Multi-Role Context Switcher.
 *  The role pill is sticky and never reloads data when toggled.
 */
import type { ReactNode } from "react";
import Image from "next/image";
import { useRoleStore, type Role } from "./roleStore";

const ROLES: { id: Role; label: string; sub: string }[] = [
  { id: "agent", label: "Agent Mobile",  sub: "বিকাশ/নগদ/রকেট" },
  { id: "ops",   label: "Ops Web",       sub: "TTE + Tickets" },
  { id: "risk",  label: "Risk Reviewer", sub: "Velocity Anomaly" },
];

export function Shell({ children }: { children: ReactNode }) {
  const role = useRoleStore((s) => s.role);
  const setRole = useRoleStore((s) => s.setRole);
  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-40 border-b border-slate-200/80 bg-white/90 shadow-sm shadow-slate-900/5 backdrop-blur-xl">
        <div className="mx-auto flex max-w-screen-2xl flex-col gap-3 px-3 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5">
          <div className="flex items-center gap-3">
            <Image
              src="/icons/icon-192.png"
              alt="LiquiGuard shield"
              width={40}
              height={40}
              priority
              className="rounded-xl shadow-sm"
            />
            <div>
              <div className="text-base font-bold tracking-tight text-slate-950">LiquiGuard</div>
              <div className="text-[11px] font-medium text-slate-500 sm:text-xs">
                Multi-provider liquidity command center
              </div>
            </div>
          </div>
          <nav className="flex w-full gap-1 overflow-x-auto rounded-xl bg-slate-100 p-1 sm:w-auto" aria-label="Role switcher">
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
                      ? "bg-slate-950 text-white shadow-sm"
                      : "text-slate-600 hover:bg-white hover:text-slate-950")
                  }
                >
                  <span>{r.label}</span>
                  <span className="ml-2 hidden text-xs opacity-70 md:inline">{r.sub}</span>
                </button>
              );
            })}
          </nav>
        </div>
      </header>
      <main className="flex-1 pb-8">{children}</main>
    </div>
  );
}
