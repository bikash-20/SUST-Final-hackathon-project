"use client";
/**
 * ThemeToggle — three-state cycle button (light → dark → system → light).
 * Sits in the Shell header so judges can flip the entire UI between the
 * previous white look and the current dark look without leaving the page.
 */
import { useSyncExternalStore } from "react";
import { useThemeStore, type ThemeMode } from "./themeStore";

const ORDER: ThemeMode[] = ["light", "dark", "system"];

const LABEL: Record<ThemeMode, string> = {
  light: "Light",
  dark: "Dark",
  system: "Auto",
};

function next(mode: ThemeMode): ThemeMode {
  const i = ORDER.indexOf(mode);
  return ORDER[(i + 1) % ORDER.length];
}

export function ThemeToggle() {
  const mode = useThemeStore((s) => s.mode);
  const setMode = useThemeStore((s) => s.setMode);

  // Snapshot the persisted theme to guard against SSR hydration
  // mismatches. The inline boot script in layout.tsx already applies the
  // class to <html> before paint, so the post-hydration value is always
  // authoritative.
  const persistedMode: ThemeMode = useSyncExternalStore(
    () => () => undefined,
    () => {
      if (typeof window === "undefined") return "light" as ThemeMode;
      try {
        const raw = window.localStorage.getItem("liquiguard.theme");
        if (!raw) return "light" as ThemeMode;
        const parsed = JSON.parse(raw) as { state?: { mode?: string } };
        const candidate = parsed.state?.mode;
        if (candidate === "dark" || candidate === "system" || candidate === "light") {
          return candidate;
        }
        return "light" as ThemeMode;
      } catch {
        return "light" as ThemeMode;
      }
    },
    () => "light" as ThemeMode,
  );

  return (
    <button
      type="button"
      onClick={() => setMode(next(mode))}
      aria-label={`Theme: ${LABEL[persistedMode]} — click to switch`}
      title={`Theme: ${LABEL[persistedMode]} — click to switch`}
      className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface-2 px-2.5 py-1.5 text-xs font-semibold text-ink shadow-card transition hover:border-signal hover:text-signal"
    >
      {persistedMode === "dark" && <MoonIcon />}
      {persistedMode === "system" && <SystemIcon />}
      {persistedMode === "light" && <SunIcon />}
      <span className="hidden sm:inline">{LABEL[persistedMode]}</span>
    </button>
  );
}

function SunIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

function SystemIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M8 20h8M12 16v4" />
    </svg>
  );
}
