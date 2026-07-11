/** Canonical client-side model for the replayable telemetry stream. */
export const PROVIDER_IDS = ["bkash", "nagad", "rocket"] as const;

export type ProviderId = (typeof PROVIDER_IDS)[number];
export type ConnectionState = "connecting" | "connected" | "disconnected";

export interface StreamEvent {
  id: number;
  sim_time: string;
  event_type: string;
  payload: Record<string, unknown>;
}

export interface BalanceReading {
  balanceBdt: number;
  simTime: string;
  receivedAt: string;
  confidenceScore: number | null;
}

export interface BalanceHistoryPoint {
  providerId: ProviderId;
  balanceBdt: number;
  simTime: string;
}

export interface LiquidityForecast {
  key: string;
  providerId: ProviderId | null;
  predictedTteMin: number | null;
  ci95: [number, number] | null;
  ewmaOutflowBdtPerMin: number | null;
  confidenceScore: number | null;
  confidenceScoreWithHistory: number | null;
  historicalContext: HistoricalPositionContext | null;
  sampleCount: number | null;
  balanceBdt: number | null;
  advisory: string | null;
  simTime: string;
}

export interface HistoricalPositionContext {
  historicalWindowDays: number;
  historicalTransactions: number;
  historicalAvgOutflowBdt: number;
  historicalAvgInflowBdt: number;
  historicalDrainRateBdtPerMin: number;
  historicalAvgBalanceBdt: number;
  historicalConsistencyScore: number;
  liveHistoricalTrendSimilarity: number | null;
}

export interface HistoricalAnalyticsSummary extends HistoricalPositionContext {
  asOf: string;
  providerSpecificAverages: Partial<
    Record<ProviderId, Omit<HistoricalPositionContext, "historicalWindowDays" | "liveHistoricalTrendSimilarity">>
  >;
}

export interface ProviderTransaction {
  transactionId: string;
  providerId: ProviderId;
  counterpartyMsisdn: string;
  amountBdt: number;
  direction: "in" | "out";
  simTime: string;
}

export interface AnomalyDetection {
  detectionId: string;
  providerId: ProviderId | null;
  riskScore: number;
  severity: string;
  windowMinutes: number;
  transactionCount: number;
  accountCount: number;
  repeatedAmountBdt: number | null;
  repeatedAmountFrequency: number;
  frequencyScore: number | null;
  velocityScore: number | null;
  identicalAmountScore: number | null;
  accountClusterScore: number | null;
  cadenceScore: number | null;
  confidenceScore: number | null;
  accounts: string[];
  rationale: string[];
  possibleBenignExplanations: string[];
  simTime: string;
}

export interface AlertTransition {
  from?: string;
  to: string;
  at: string;
  by: string;
  reason: string;
}

export interface CoordinationAlert {
  alertToken: string;
  status: "PENDING" | "ACKNOWLEDGED" | "ESCALATED" | "RESOLVED" | string;
  severity: string;
  providerId: ProviderId | null;
  actor: string | null;
  reason: string | null;
  transitions: AlertTransition[];
  simTime: string;
}

export interface TelemetrySnapshot {
  connectionState: ConnectionState;
  connectionError: string | null;
  simTime: string | null;
  lastEventId: number;
  lastReceivedAt: string | null;
  confidenceScore: number | null;
  degraded: boolean;
  sharedCashBalance: BalanceReading | null;
  providerBalances: Partial<Record<ProviderId, BalanceReading>>;
  balanceHistory: BalanceHistoryPoint[];
  liquidityForecasts: Record<string, LiquidityForecast>;
  historicalAnalytics: HistoricalAnalyticsSummary | null;
  providerTxns: ProviderTransaction[];
  anomalyDetections: AnomalyDetection[];
  alerts: CoordinationAlert[];
}
