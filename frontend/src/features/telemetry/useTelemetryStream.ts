"use client";

import { useEffect } from "react";
import { create } from "zustand";
import {
  PROVIDER_IDS,
  type AlertTransition,
  type AnomalyDetection,
  type BalanceHistoryPoint,
  type BalanceReading,
  type ConnectionState,
  type CoordinationAlert,
  type HistoricalAnalyticsSummary,
  type HistoricalPositionContext,
  type LiquidityForecast,
  type ProviderId,
  type ProviderTransaction,
  type StreamEvent,
  type TelemetrySnapshot,
} from "./types";

type JsonObject = Record<string, unknown>;

interface TelemetryStore extends TelemetrySnapshot {
  ingest: (eventType: string, data: unknown, lastEventId?: string) => void;
  setConnection: (state: ConnectionState, error?: string | null) => void;
  reset: () => void;
}

const EMPTY: TelemetrySnapshot = {
  connectionState: "connecting",
  connectionError: null,
  simTime: null,
  lastEventId: 0,
  lastReceivedAt: null,
  confidenceScore: null,
  degraded: false,
  sharedCashBalance: null,
  providerBalances: {},
  balanceHistory: [],
  liquidityForecasts: {},
  historicalAnalytics: null,
  providerTxns: [],
  anomalyDetections: [],
  alerts: [],
};

export const useTelemetryStore = create<TelemetryStore>((set) => ({
  ...EMPTY,
  ingest: (eventType, data, lastEventId) =>
    set((previous) => {
      const event = normalizeStreamEvent(eventType, data, lastEventId);
      return event ? applyEvent(previous, event) : previous;
    }),
  setConnection: (connectionState, connectionError = null) =>
    set({ connectionState, connectionError }),
  reset: () => set(EMPTY),
}));

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asObject(value: unknown): JsonObject | null {
  return isObject(value) ? value : null;
}

function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function asString(value: unknown): string | null {
  if (typeof value === "string" && value.trim() !== "") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return null;
}

function firstNumber(record: JsonObject | null, keys: string[]): number | null {
  if (!record) return null;
  for (const key of keys) {
    const number = asNumber(record[key]);
    if (number !== null) return number;
  }
  return null;
}

function firstString(record: JsonObject | null, keys: string[]): string | null {
  if (!record) return null;
  for (const key of keys) {
    const string = asString(record[key]);
    if (string !== null) return string;
  }
  return null;
}

function asProvider(value: unknown): ProviderId | null {
  const provider = asString(value)?.toLowerCase();
  return PROVIDER_IDS.find((candidate) => candidate === provider) ?? null;
}

function normalizeScore(value: unknown): number | null {
  const score = asNumber(value);
  if (score === null) return null;
  const ratio = score > 1 && score <= 100 ? score / 100 : score;
  return Math.min(1, Math.max(0, ratio));
}

function readScore(record: JsonObject | null, keys: string[]): number | null {
  if (!record) return null;
  for (const key of keys) {
    const score = normalizeScore(record[key]);
    if (score !== null) return score;
  }
  return null;
}

function asStringArray(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (typeof item === "string") return item;
        const record = asObject(item);
        return firstString(record, ["reason", "message", "account", "msisdn"]);
      })
      .filter((item): item is string => Boolean(item));
  }
  const single = asString(value);
  return single ? [single] : [];
}

function readBalance(value: unknown): number | null {
  const direct = asNumber(value);
  if (direct !== null) return direct;
  return firstNumber(asObject(value), [
    "balance_bdt",
    "balance",
    "new_balance",
    "shared_cash_balance",
  ]);
}

function readConfidence(value: unknown, fallback: JsonObject | null): number | null {
  const object = asObject(value);
  return (
    readScore(object, ["confidence_score", "confidence"]) ??
    readScore(fallback, ["confidence_score", "confidence"])
  );
}

function normalizeStreamEvent(
  registeredEventType: string,
  data: unknown,
  browserLastEventId?: string,
): StreamEvent | null {
  const envelope = asObject(data);
  if (!envelope) return null;
  const payload = asObject(envelope.payload) ?? envelope;
  const eventType =
    firstString(envelope, ["event_type", "eventType"]) ?? registeredEventType;
  const simTime =
    firstString(envelope, ["sim_time", "simTime"]) ??
    firstString(payload, ["sim_time", "simTime", "updated_at"]) ??
    new Date().toISOString();
  const eventId =
    firstNumber(envelope, ["id", "event_id"]) ?? asNumber(browserLastEventId) ?? 0;
  return {
    id: Math.max(0, Math.trunc(eventId)),
    sim_time: simTime,
    event_type: eventType,
    payload,
  };
}

function appendBalanceHistory(
  history: BalanceHistoryPoint[],
  providerId: ProviderId,
  balanceBdt: number,
  simTime: string,
): BalanceHistoryPoint[] {
  const latest = [...history]
    .reverse()
    .find((point) => point.providerId === providerId);
  if (latest?.simTime === simTime && latest.balanceBdt === balanceBdt) return history;
  return [...history, { providerId, balanceBdt, simTime }].slice(-360);
}

function toCi95(value: unknown, record: JsonObject): [number, number] | null {
  if (Array.isArray(value) && value.length >= 2) {
    const low = asNumber(value[0]);
    const high = asNumber(value[1]);
    if (low !== null && high !== null) return [low, high];
  }
  const object = asObject(value);
  const low =
    firstNumber(object, ["low", "lower", "min"]) ??
    firstNumber(record, ["ci95_low", "tte_ci_low_min"]);
  const high =
    firstNumber(object, ["high", "upper", "max"]) ??
    firstNumber(record, ["ci95_high", "tte_ci_high_min"]);
  return low !== null && high !== null ? [low, high] : null;
}

function parseForecastRecord(
  record: JsonObject,
  simTime: string,
  fallbackKey?: string,
  fallbackProvider?: ProviderId | null,
): LiquidityForecast | null {
  const providerId =
    asProvider(record.provider_id ?? record.providerId ?? record.position_id) ??
    asProvider(fallbackKey) ??
    fallbackProvider ??
    null;
  const providers = Array.isArray(record.providers)
    ? record.providers.map(asProvider).filter((p): p is ProviderId => p !== null)
    : [];
  const resolvedProvider = providerId ?? (providers.length === 1 ? providers[0] : null);
  const predictedTteMin = firstNumber(record, [
    "predicted_tte_min",
    "predictedTteMin",
    "tte_minutes",
    "tte_min",
    "time_to_exhaustion_min",
    "tte",
  ]);
  const outflow = firstNumber(record, [
    "ewma_outflow_bdt_per_min",
    "ewma_drain_bdt_per_min",
    "ewma_slope_bdt_per_min",
    "drain_rate_bdt_per_min",
  ]);
  const balance = firstNumber(record, ["balance_bdt", "current_balance_bdt"]);
  const hasForecastSignal =
    predictedTteMin !== null || outflow !== null || record.ci95 !== undefined;
  if (!hasForecastSignal) return null;

  const key =
    firstString(record, ["key", "forecast_id", "position_id"]) ??
    resolvedProvider ??
    fallbackKey ??
    "aggregate";
  return {
    key,
    providerId: resolvedProvider,
    predictedTteMin,
    ci95: toCi95(record.ci95 ?? record.confidence_interval, record),
    ewmaOutflowBdtPerMin: outflow === null ? null : Math.abs(outflow),
    confidenceScore: readScore(record, ["confidence_score", "confidence"]),
    confidenceScoreWithHistory: readScore(record, [
      "confidence_score_with_history",
    ]),
    historicalContext: parseHistoricalPosition(record.historical_context),
    sampleCount: firstNumber(record, ["sample_count", "samples", "observation_count"]),
    balanceBdt: balance,
    advisory: firstString(record, ["advisory", "recommendation"]),
    simTime: firstString(record, ["sim_time", "calculated_at", "updated_at"]) ?? simTime,
  };
}

function parseHistoricalPosition(value: unknown): HistoricalPositionContext | null {
  const record = asObject(value);
  if (!record) return null;
  const historicalWindowDays = firstNumber(record, ["historical_window_days"]);
  const historicalTransactions = firstNumber(record, ["historical_transactions"]);
  if (historicalWindowDays === null || historicalTransactions === null) return null;
  return {
    historicalWindowDays,
    historicalTransactions,
    historicalAvgOutflowBdt:
      firstNumber(record, ["historical_avg_outflow_bdt"]) ?? 0,
    historicalAvgInflowBdt:
      firstNumber(record, ["historical_avg_inflow_bdt"]) ?? 0,
    historicalDrainRateBdtPerMin:
      firstNumber(record, ["historical_drain_rate_bdt_per_min"]) ?? 0,
    historicalAvgBalanceBdt:
      firstNumber(record, ["historical_avg_balance_bdt"]) ?? 0,
    historicalConsistencyScore:
      normalizeScore(record.historical_consistency_score) ?? 0,
    liveHistoricalTrendSimilarity:
      normalizeScore(record.live_historical_trend_similarity),
  };
}

function parseHistoricalAnalytics(value: unknown): HistoricalAnalyticsSummary | null {
  const record = asObject(value);
  const shared = parseHistoricalPosition(record);
  if (!record || !shared) return null;
  const providers = asObject(record.provider_specific_averages);
  const providerSpecificAverages: HistoricalAnalyticsSummary["providerSpecificAverages"] = {};
  for (const providerId of PROVIDER_IDS) {
    const position = parseHistoricalPosition({
      ...asObject(providers?.[providerId]),
      historical_window_days: shared.historicalWindowDays,
    });
    if (!position) continue;
    const { historicalWindowDays: _window, liveHistoricalTrendSimilarity: _similarity, ...averages } = position;
    providerSpecificAverages[providerId] = averages;
  }
  return {
    ...shared,
    asOf: firstString(record, ["as_of"]) ?? "",
    historicalHasEvidence:
      typeof record.historical_has_evidence === "boolean"
        ? record.historical_has_evidence
        : shared.historicalTransactions > 0,
    providerSpecificAverages,
  };
}

function collectForecasts(
  value: unknown,
  simTime: string,
  fallbackProvider?: ProviderId | null,
): LiquidityForecast[] {
  if (Array.isArray(value)) {
    return value.flatMap((item) => collectForecasts(item, simTime, fallbackProvider));
  }
  const object = asObject(value);
  if (!object) return [];
  const direct = parseForecastRecord(object, simTime, undefined, fallbackProvider);
  if (direct) return [direct];

  return Object.entries(object).flatMap(([key, nested]) => {
    const nestedObject = asObject(nested);
    if (!nestedObject) return [];
    const parsed = parseForecastRecord(nestedObject, simTime, key, asProvider(key));
    return parsed ? [parsed] : collectForecasts(nestedObject, simTime, asProvider(key));
  });
}

function parseAnomaly(
  value: unknown,
  simTime: string,
  fallbackId: string,
): AnomalyDetection[] {
  if (Array.isArray(value)) {
    return value.flatMap((item, index) =>
      parseAnomaly(item, simTime, `${fallbackId}-${index}`),
    );
  }
  const record = asObject(value);
  if (!record) return [];
  if (
    record.triggered === false ||
    record.flagged === false ||
    record.is_anomaly === false
  ) {
    return [];
  }

  const evidence = asObject(record.evidence);
  const components = asObject(evidence?.score_components ?? record.score_components);

  const riskScore = readScore(record, ["risk_score", "score", "anomaly_score"]);
  const hasDetectionSignal =
    record.triggered === true || record.flagged === true || record.is_anomaly === true;
  if (!hasDetectionSignal) return [];

  const providerId = asProvider(record.provider_id ?? record.providerId);
  const accounts = asStringArray(
    record.accounts ?? record.account_ids ?? record.counterparties ?? record.msisdns,
  );
  const normalizedRisk = riskScore ?? 0;
  const detectionId =
    firstString(record, ["detection_id", "alert_id", "id"]) ?? fallbackId;
  return [
    {
      detectionId,
      providerId,
      riskScore: normalizedRisk,
      severity:
        firstString(record, ["severity", "risk_level"]) ??
        (normalizedRisk >= 0.8 ? "high" : normalizedRisk >= 0.6 ? "medium" : "review"),
      windowMinutes:
        firstNumber(evidence, ["window_minutes", "window_min", "lookback_minutes"]) ??
        firstNumber(record, ["window_minutes", "window_min", "lookback_minutes"]) ??
        0,
      transactionCount:
        firstNumber(evidence, [
          "outgoing_transaction_count",
          "window_transaction_count",
          "transaction_count",
        ]) ??
        firstNumber(record, ["transaction_count", "frequency", "event_count"]) ??
        0,
      accountCount:
        firstNumber(evidence, [
          "dominant_amount_distinct_account_count",
          "distinct_account_count",
        ]) ??
        firstNumber(record, ["account_count", "cluster_size", "unique_accounts"]) ??
        accounts.length,
      repeatedAmountBdt:
        firstNumber(evidence, ["dominant_repeated_amount_bdt"]) ??
        firstNumber(record, [
          "repeated_amount_bdt",
          "identical_amount_bdt",
          "dominant_amount_bdt",
          "amount_bdt",
        ]),
      repeatedAmountFrequency:
        firstNumber(evidence, ["dominant_repeated_amount_frequency"]) ?? 0,
      frequencyScore:
        readScore(components, ["repeated_frequency", "frequency"]) ??
        readScore(record, ["frequency_score", "velocity_score"]),
      velocityScore: readScore(components, ["repeated_velocity", "velocity"]),
      identicalAmountScore:
        readScore(components, ["repeated_share", "concentration"]) ??
        readScore(record, [
          "identical_amount_score",
          "repeated_amount_score",
          "amount_score",
        ]),
      accountClusterScore:
        readScore(components, ["account_clustering"]) ??
        readScore(record, [
          "account_cluster_score",
          "clustering_score",
          "cluster_score",
        ]),
      cadenceScore: readScore(components, ["cadence_regularity", "cadence"]),
      confidenceScore: readScore(record, ["confidence", "confidence_score"]),
      accounts,
      rationale: asStringArray(record.rationale ?? record.reasons),
      possibleBenignExplanations: asStringArray(
        record.possible_benign_explanations ?? record.benign_explanations,
      ),
      simTime: firstString(record, ["sim_time", "detected_at", "window_end"]) ?? simTime,
    },
  ];
}

function parseTransitions(value: unknown): AlertTransition[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const transition = asObject(item);
    if (!transition) return [];
    const to = firstString(transition, ["to", "status"]);
    if (!to) return [];
    return [
      {
        from: firstString(transition, ["from"]) ?? undefined,
        to: to.toUpperCase(),
        at: firstString(transition, ["at", "sim_time", "timestamp"]) ?? "",
        by: firstString(transition, ["by", "actor"]) ?? "system",
        reason: firstString(transition, ["reason"]) ?? "",
      },
    ];
  });
}

function parseAlert(
  record: JsonObject,
  eventType: string,
  simTime: string,
): CoordinationAlert | null {
  const alertToken = firstString(record, ["alert_token", "alertToken"]);
  if (!alertToken) return null;
  const eventStatus = eventType.startsWith("coordination.")
    ? eventType.split(".").at(-1)?.toUpperCase()
    : null;
  const status =
    firstString(record, ["status"])?.toUpperCase() ?? eventStatus ?? "PENDING";
  return {
    alertToken,
    status,
    severity: firstString(record, ["severity"]) ?? "",
    providerId: asProvider(record.provider_id ?? record.providerId),
    actor: firstString(record, ["actor", "by"]),
    reason: firstString(record, ["reason"]),
    transitions: parseTransitions(record.transitions),
    simTime: firstString(record, ["sim_time", "updated_at"]) ?? simTime,
  };
}

function mergeAlert(
  alerts: CoordinationAlert[],
  incoming: CoordinationAlert,
): CoordinationAlert[] {
  const existing = alerts.find((alert) => alert.alertToken === incoming.alertToken);
  if (!existing) {
    return [...alerts, { ...incoming, severity: incoming.severity || "medium" }].slice(-100);
  }
  return alerts.map((alert) =>
    alert.alertToken === incoming.alertToken
      ? {
          ...alert,
          ...incoming,
          severity: incoming.severity || alert.severity,
          providerId: incoming.providerId ?? alert.providerId,
          actor: incoming.actor ?? alert.actor,
          reason: incoming.reason ?? alert.reason,
          transitions:
            incoming.transitions.length > 0 ? incoming.transitions : alert.transitions,
        }
      : alert,
  );
}

function applyEvent(previous: TelemetryStore, event: StreamEvent): TelemetryStore {
  const payload = event.payload;
  const result = asObject(payload.result);
  const tickPayload = asObject(payload.tick_payload ?? payload.tickPayload ?? payload.payload);
  const snapshot = asObject(payload.snapshot) ??
    (event.event_type === "snapshot" ? payload : null);
  const kind = firstString(payload, ["kind"]) ?? firstString(result, ["event_type", "kind"]);
  const receivedAt = new Date().toISOString();

  let confidenceScore = previous.confidenceScore;
  let degraded = previous.degraded;
  let sharedCashBalance = previous.sharedCashBalance;
  let providerBalances = { ...previous.providerBalances };
  let balanceHistory = previous.balanceHistory;
  let liquidityForecasts = { ...previous.liquidityForecasts };
  let historicalAnalytics = previous.historicalAnalytics;
  let providerTxns = previous.providerTxns;
  let anomalyDetections = previous.anomalyDetections;
  let alerts = previous.alerts;

  const historicalInput =
    snapshot?.historical_analytics ?? snapshot?.historicalAnalytics;
  if (historicalInput !== undefined) {
    historicalAnalytics = parseHistoricalAnalytics(historicalInput);
  }

  const explicitConfidence =
    readScore(snapshot, ["confidence_score", "confidence"]) ??
    (kind === "inconsistency"
      ? readScore(result, ["confidence_score", "confidence"]) ??
        readScore(payload, ["confidence_score", "confidence"])
      : null);
  if (explicitConfidence !== null) {
    confidenceScore = explicitConfidence;
    degraded = explicitConfidence < 0.5;
  }

  const sharedValue =
    snapshot?.shared_cash_balance ??
    snapshot?.sharedCashBalance ??
    result?.shared_cash_balance ??
    result?.sharedCashBalance ??
    ((kind === "cash_in" || kind === "cash_out")
      ? result?.balance ?? result?.new_balance
      : undefined);
  const sharedBalance = readBalance(sharedValue);
  if (sharedBalance !== null) {
    sharedCashBalance = {
      balanceBdt: sharedBalance,
      simTime: event.sim_time,
      receivedAt,
      confidenceScore: readConfidence(sharedValue, snapshot ?? result),
    };
  }

  const balanceMap =
    asObject(snapshot?.provider_balances ?? snapshot?.providerBalances) ??
    asObject(result?.provider_balances ?? result?.providerBalances);
  const positionMap =
    asObject(snapshot?.provider_positions ?? snapshot?.providerPositions) ??
    asObject(result?.provider_positions ?? result?.providerPositions);
  if (balanceMap || positionMap) {
    for (const providerId of PROVIDER_IDS) {
      const numericValue = balanceMap?.[providerId];
      const positionValue = positionMap?.[providerId];
      const value = positionValue ?? numericValue;
      const balanceBdt = readBalance(value) ?? readBalance(numericValue);
      if (balanceBdt === null) continue;
      providerBalances[providerId] = {
        balanceBdt,
        simTime:
          firstString(asObject(value), ["sim_time", "updated_at"]) ?? event.sim_time,
        receivedAt,
        confidenceScore: readConfidence(value, snapshot ?? result),
      };
      balanceHistory = appendBalanceHistory(
        balanceHistory,
        providerId,
        balanceBdt,
        event.sim_time,
      );
    }
  }

  const singularBalance =
    result?.provider_balance ?? payload.provider_balance ?? snapshot?.provider_balance;
  const singularObject = asObject(singularBalance);
  const singularProvider =
    asProvider(singularObject?.provider_id ?? singularObject?.providerId) ??
    asProvider(result?.provider_id ?? result?.providerId) ??
    asProvider(tickPayload?.provider_id ?? tickPayload?.providerId);
  const singularAmount =
    readBalance(singularBalance) ??
    (singularProvider ? firstNumber(result, ["balance_bdt", "new_balance"]) : null);
  if (singularProvider && singularAmount !== null) {
    providerBalances[singularProvider] = {
      balanceBdt: singularAmount,
      simTime: event.sim_time,
      receivedAt,
      confidenceScore: readConfidence(singularBalance, result),
    };
    balanceHistory = appendBalanceHistory(
      balanceHistory,
      singularProvider,
      singularAmount,
      event.sim_time,
    );
  }

  const forecastInputs: unknown[] = [
    snapshot?.liquidity_forecasts,
    snapshot?.liquidityForecasts,
    payload.liquidity_forecast,
    result?.liquidity_forecast,
    result?.provider_liquidity_forecast,
    result?.forecast,
  ];
  if (kind === "liquidity_forecast" || kind === "advisory_tte") {
    forecastInputs.push(result);
  }
  for (const input of forecastInputs) {
    for (const forecast of collectForecasts(input, event.sim_time, singularProvider)) {
      liquidityForecasts[forecast.key] = forecast;
    }
  }

  if (event.event_type === "tick.done" && kind === "provider_txn" && result) {
    const providerId = asProvider(result.provider_id ?? result.providerId);
    const amountBdt = firstNumber(result, ["amount_bdt", "amount"]);
    const counterpartyMsisdn = firstString(result, [
      "counterparty_msisdn",
      "counterparty_id",
      "counterparty",
      "account_id",
    ]);
    const directionValue = firstString(result, ["direction"])?.toLowerCase();
    if (
      providerId &&
      amountBdt !== null &&
      counterpartyMsisdn &&
      (directionValue === "in" || directionValue === "out")
    ) {
      const transactionId =
        firstString(result, ["transaction_id", "txn_id"]) ??
        firstString(payload, ["tick_id"]) ??
        `${event.id}-${event.sim_time}`;
      const transaction: ProviderTransaction = {
        transactionId,
        providerId,
        counterpartyMsisdn,
        amountBdt,
        direction: directionValue,
        simTime: event.sim_time,
      };
      providerTxns = [
        ...providerTxns.filter((item) => item.transactionId !== transactionId),
        transaction,
      ].slice(-300);
    }
  }

  const anomalyInputs: unknown[] = [
    snapshot?.anomaly_detections,
    snapshot?.anomalyDetections,
    payload.anomaly_detection,
    result?.anomaly_detection,
    result?.detection,
  ];
  if (kind === "anomaly_detection") anomalyInputs.push(result);
  for (const input of anomalyInputs) {
    const fallbackId =
      firstString(payload, ["tick_id"]) ?? `${event.id}-${event.sim_time}`;
    for (const detection of parseAnomaly(input, event.sim_time, fallbackId)) {
      anomalyDetections = [
        ...anomalyDetections.filter(
          (item) => item.detectionId !== detection.detectionId,
        ),
        detection,
      ].slice(-200);
    }
  }

  const snapshotAlerts = snapshot?.alerts;
  if (Array.isArray(snapshotAlerts)) {
    for (const item of snapshotAlerts) {
      const alertRecord = asObject(item);
      if (!alertRecord) continue;
      const alert = parseAlert(alertRecord, "snapshot", event.sim_time);
      if (alert) alerts = mergeAlert(alerts, alert);
    }
  }
  if (event.event_type.startsWith("coordination.")) {
    const alert = parseAlert(payload, event.event_type, event.sim_time);
    if (alert) alerts = mergeAlert(alerts, alert);
  } else if (kind === "coordination_awaiting" && result) {
    const alert = parseAlert(result, "coordination.PENDING", event.sim_time);
    if (alert) alerts = mergeAlert(alerts, alert);
  }

  return {
    ...previous,
    connectionState: "connected",
    connectionError: null,
    simTime:
      event.event_type === "ready" && previous.simTime
        ? previous.simTime
        : event.sim_time,
    lastEventId: Math.max(previous.lastEventId, event.id),
    lastReceivedAt: receivedAt,
    confidenceScore,
    degraded,
    sharedCashBalance,
    providerBalances,
    balanceHistory,
    liquidityForecasts,
    historicalAnalytics,
    providerTxns,
    anomalyDetections,
    alerts,
  };
}

const NAMED_SSE_EVENTS = [
  "ready",
  "snapshot",
  "tick.enqueued",
  "tick.done",
  "tick.dead_letter",
  "tick.fatal",
  "coordination.PENDING",
  "coordination.ACKNOWLEDGED",
  "coordination.ESCALATED",
  "coordination.RESOLVED",
  "coordination.pending",
  "coordination.acknowledged",
  "coordination.escalated",
  "coordination.resolved",
] as const;

/** Mount once at the app provider boundary so role changes never reset the stream. */
export function useTelemetryStream() {
  const ingest = useTelemetryStore((state) => state.ingest);
  const setConnection = useTelemetryStore((state) => state.setConnection);

  useEffect(() => {
    if (typeof window === "undefined") return;
    setConnection("connecting");
    const source = new EventSource("/v1/telemetry/stream");

    const consume = (registeredType: string) => (event: Event) => {
      const message = event as MessageEvent<string>;
      try {
        ingest(registeredType, JSON.parse(message.data) as unknown, message.lastEventId);
      } catch {
        setConnection("disconnected", `Malformed ${registeredType} telemetry event`);
      }
    };

    const listeners = NAMED_SSE_EVENTS.map((eventType) => {
      const listener = consume(eventType);
      source.addEventListener(eventType, listener);
      return { eventType, listener };
    });

    // Compatibility with servers that intentionally use the default SSE channel.
    source.onmessage = consume("message");
    source.onopen = () => setConnection("connected");
    source.onerror = () => {
      setConnection("disconnected", "Telemetry stream reconnecting");
    };

    return () => {
      for (const { eventType, listener } of listeners) {
        source.removeEventListener(eventType, listener);
      }
      source.close();
    };
  }, [ingest, setConnection]);
}
