"""Deterministic historical context sourced from committed PostgreSQL ledgers."""
from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.infrastructure.database import PROVIDERS, session_scope


def _number(value: Any) -> float:
    return float(value or 0.0)


def _rounded(value: float) -> float:
    return round(value, 2)


@dataclass(frozen=True, slots=True)
class PositionHistory:
    transaction_count: int
    average_outflow_bdt: float
    average_inflow_bdt: float
    drain_rate_bdt_per_min: float
    average_daily_balance_bdt: float
    consistency_score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "historical_transactions": self.transaction_count,
            "historical_avg_outflow_bdt": self.average_outflow_bdt,
            "historical_avg_inflow_bdt": self.average_inflow_bdt,
            "historical_drain_rate_bdt_per_min": self.drain_rate_bdt_per_min,
            "historical_avg_balance_bdt": self.average_daily_balance_bdt,
            "historical_consistency_score": self.consistency_score,
        }


@dataclass(frozen=True, slots=True)
class HistoricalContext:
    window_days: int
    as_of: datetime
    positions: dict[str, PositionHistory]

    def position(self, position_id: str) -> PositionHistory:
        return self.positions.get(
            position_id,
            PositionHistory(0, 0.0, 0.0, 0.0, 0.0, 0.0),
        )

    def as_dict(self) -> dict[str, Any]:
        shared = self.position("shared_cash")
        return {
            "historical_window_days": self.window_days,
            **shared.as_dict(),
            "provider_specific_averages": {
                provider: self.position(provider).as_dict()
                for provider in PROVIDERS
            },
            "as_of": self.as_of.isoformat(),
        }


class HistoricalAnalytics:
    """Aggregate committed movements and enrich, but never replace, live EWMA."""

    def __init__(self, window_days: int | None = None) -> None:
        configured = window_days or int(os.getenv("HISTORICAL_WINDOW_DAYS", "30"))
        if not 1 <= configured <= 365:
            raise ValueError("historical window must be between 1 and 365 days")
        self.window_days = configured
        self._cache_key: tuple[uuid.UUID, int, int] | None = None
        self._cache_value: HistoricalContext | None = None
        self._lock = asyncio.Lock()

    async def context(
        self,
        *,
        agent_id: uuid.UUID,
        as_of: datetime,
        window_days: int | None = None,
    ) -> HistoricalContext:
        days = window_days or self.window_days
        if not 1 <= days <= 365:
            raise ValueError("historical window must be between 1 and 365 days")
        key = (agent_id, days, int(as_of.timestamp() // 60))
        if key == self._cache_key and self._cache_value is not None:
            return self._cache_value
        async with self._lock:
            if key == self._cache_key and self._cache_value is not None:
                return self._cache_value
            value = await self._query(agent_id=agent_id, as_of=as_of, days=days)
            self._cache_key = key
            self._cache_value = value
            return value

    async def _query(
        self,
        *,
        agent_id: uuid.UUID,
        as_of: datetime,
        days: int,
    ) -> HistoricalContext:
        cutoff = as_of - timedelta(days=days)
        params = {"agent_id": agent_id, "cutoff": cutoff, "as_of": as_of}
        async with session_scope() as session:
            shared = (
                await session.execute(
                    text(
                        """
                        WITH raw AS (
                            SELECT id, sim_time, delta_bdt
                              FROM shared.shared_cash_movement
                             WHERE agent_id = :agent_id
                               AND sim_time >= :cutoff
                               AND sim_time <= :as_of
                        ), balance_points AS (
                            SELECT id, sim_time,
                                   (SELECT balance_bdt
                                      FROM shared.shared_cash_ledger
                                     WHERE agent_id = :agent_id)
                                   - COALESCE(
                                       sum(delta_bdt) OVER (
                                           ORDER BY sim_time DESC, id DESC
                                           ROWS BETWEEN UNBOUNDED PRECEDING
                                                    AND 1 PRECEDING
                                       ), 0
                                   ) AS balance_after
                              FROM raw
                        ), daily AS (
                            SELECT balance_after,
                                   row_number() OVER (
                                       PARTITION BY (sim_time AT TIME ZONE 'UTC')::date
                                       ORDER BY sim_time DESC, id DESC
                                   ) AS daily_rank
                              FROM balance_points
                        )
                        SELECT count(*) AS transaction_count,
                               avg(-delta_bdt) FILTER (WHERE delta_bdt < 0)
                                   AS average_outflow,
                               avg(delta_bdt) FILTER (WHERE delta_bdt > 0)
                                   AS average_inflow,
                               sum(delta_bdt) AS net_delta,
                               extract(epoch FROM (max(sim_time) - min(sim_time))) / 60.0
                                   AS span_minutes,
                               stddev_pop(abs(delta_bdt)) AS amount_stddev,
                               avg(abs(delta_bdt)) AS average_absolute_delta,
                               (SELECT avg(balance_after)
                                  FROM daily WHERE daily_rank = 1)
                                   AS average_daily_balance,
                               (SELECT balance_bdt
                                  FROM shared.shared_cash_ledger
                                 WHERE agent_id = :agent_id) AS current_balance
                          FROM raw
                        """
                    ),
                    params,
                )
            ).mappings().one()

            provider_rows = (
                await session.execute(
                    text(
                        """
                        WITH raw AS (
                            SELECT transaction_id, provider_id, sim_time,
                                   provider_delta_bdt AS delta_bdt,
                                   provider_balance_after AS balance_after
                              FROM shared.provider_customer_journal
                             WHERE agent_id = :agent_id
                               AND sim_time >= :cutoff
                               AND sim_time <= :as_of
                        ), ranked AS (
                            SELECT *,
                                   row_number() OVER (
                                       PARTITION BY provider_id,
                                           (sim_time AT TIME ZONE 'UTC')::date
                                       ORDER BY sim_time DESC, transaction_id DESC
                                   ) AS daily_rank
                              FROM raw
                        ), stats AS (
                            SELECT provider_id,
                                   count(*) AS transaction_count,
                                   avg(-delta_bdt) FILTER (WHERE delta_bdt < 0)
                                       AS average_outflow,
                                   avg(delta_bdt) FILTER (WHERE delta_bdt > 0)
                                       AS average_inflow,
                                   sum(delta_bdt) AS net_delta,
                                   extract(epoch FROM (max(sim_time) - min(sim_time))) / 60.0
                                       AS span_minutes,
                                   stddev_pop(abs(delta_bdt)) AS amount_stddev,
                                   avg(abs(delta_bdt)) AS average_absolute_delta
                              FROM raw GROUP BY provider_id
                        ), daily AS (
                            SELECT provider_id, avg(balance_after) AS average_daily_balance
                              FROM ranked WHERE daily_rank = 1 GROUP BY provider_id
                        )
                        SELECT stats.*, daily.average_daily_balance
                          FROM stats LEFT JOIN daily USING (provider_id)
                        """
                    ),
                    params,
                )
            ).mappings().all()

        positions = {"shared_cash": self._position_from_row(shared)}
        for row in provider_rows:
            positions[str(row["provider_id"])] = self._position_from_row(row)
        for provider in PROVIDERS:
            positions.setdefault(
                provider,
                PositionHistory(0, 0.0, 0.0, 0.0, 0.0, 0.0),
            )
        return HistoricalContext(window_days=days, as_of=as_of, positions=positions)

    @staticmethod
    def _position_from_row(row: Any) -> PositionHistory:
        count = int(row["transaction_count"] or 0)
        span_minutes = max(_number(row["span_minutes"]), 1.0)
        net_delta = _number(row["net_delta"])
        average_absolute = _number(row["average_absolute_delta"])
        # Direction changes should not make otherwise repeatable transaction
        # sizes look inconsistent, so variability is measured on magnitudes.
        variability = _number(row["amount_stddev"])
        # A bounded inverse coefficient of variation is robust to the large
        # synthetic scenario outliers in this dataset: 1 means uniform sizes,
        # while increasingly variable history approaches (but never fakes) 0.
        consistency = (
            average_absolute / (average_absolute + variability)
            if average_absolute > 0 else 0.0
        )
        average_balance = _number(row["average_daily_balance"])
        if average_balance == 0.0 and "current_balance" in row:
            average_balance = _number(row["current_balance"])
        return PositionHistory(
            transaction_count=count,
            average_outflow_bdt=_rounded(_number(row["average_outflow"])),
            average_inflow_bdt=_rounded(_number(row["average_inflow"])),
            drain_rate_bdt_per_min=_rounded(max(0.0, -net_delta / span_minutes)),
            average_daily_balance_bdt=_rounded(average_balance),
            consistency_score=round(consistency, 3),
        )

    @staticmethod
    def enrich_forecast(
        forecast: dict[str, Any],
        history: PositionHistory,
        *,
        window_days: int,
    ) -> dict[str, Any]:
        live_rate = float(forecast.get("ewma_drain_bdt_per_min") or 0.0)
        historical_rate = history.drain_rate_bdt_per_min
        if live_rate <= 0.01 and historical_rate <= 0.01:
            similarity = 1.0
        elif live_rate <= 0.01 or historical_rate <= 0.01:
            similarity = 0.0
        else:
            similarity = max(
                0.0,
                1.0 - abs(live_rate - historical_rate) / max(live_rate, historical_rate),
            )
        evidence = min(1.0, history.transaction_count / 100.0)
        live_confidence = float(forecast.get("confidence_score") or 0.0)
        boost = 0.20 * evidence * history.consistency_score * similarity
        contextual_confidence = min(
            0.98, live_confidence + (1.0 - live_confidence) * boost
        )
        return {
            **forecast,
            "confidence_score_with_history": round(contextual_confidence, 3),
            "historical_context": {
                "historical_window_days": window_days,
                **history.as_dict(),
                "live_historical_trend_similarity": round(similarity, 3),
            },
        }
