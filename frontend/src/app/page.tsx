"use client";
/**
 * Top-level router.
 * The 3-role switcher in `Shell` chooses which feature page is shown.
 * Each role keeps its own scroll position and Zustand-backed filters.
 */
import { AgentMobileView } from "@/features/agent/AgentMobileView";
import { OpsWebView } from "@/features/ops/OpsWebView";
import { RiskReviewerView } from "@/features/risk/RiskReviewerView";
import { useRole } from "@/features/shell/useRole";

export default function HomePage() {
  const role = useRole();
  if (role === "agent") return <AgentMobileView />;
  if (role === "ops") return <OpsWebView />;
  return <RiskReviewerView />;
}
