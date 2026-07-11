"use client";
/**
 * Scenario C Degraded Mode Guard.
 *
 *  The instant a tick arrives with kind='inconsistency' and
 *  confidence_score < 0.5, this layout wraps its children in a
 *  pulsing-amber outline + a sticky Bangla/Banglish banner. No
 *  historical data is mutated on the backend — the change is purely
 *  visual and stops at the consumer.
 */
import { type ReactNode } from "react";
import { useTelemetryStore } from "../telemetry/useTelemetryStream";

const BANNERS = {
  bn: "⚠ ডেটা ফিডে অনিশ্চয়তা শনাক্ত হয়েছে — নতুন ক্যাশ-আউটের আগে সর্বশেষ ব্যালেন্স যাচাই করুন",
  en: "⚠ Data feed uncertainty detected — verify the latest balance before a new cash-out",
  banglish: "⚠ Data feed uncertain — notun cash-out er age latest balance verify korun",
};

export function SafeFallbackLayout({ children }: { children: ReactNode }) {
  const degraded = useTelemetryStore((s) => s.degraded);
  const confidence = useTelemetryStore((s) => s.confidenceScore);
  if (!degraded) return <>{children}</>;
  return (
    <div className="safe-fallback animate-pulse-warn mx-auto my-4 max-w-screen-2xl p-4">
      <div className="safe-fallback-banner">
        <div>Scenario C — Inconsistent Feed Active</div>
        <div className="text-xs font-normal opacity-80">
          {BANNERS.bn} · {BANNERS.banglish}
        </div>
        <div className="text-[11px] font-normal opacity-70">
          confidence_score = {confidence?.toFixed(2)} (threshold 0.50)
        </div>
      </div>
      <div className="opacity-60">
        {children}
      </div>
    </div>
  );
}
