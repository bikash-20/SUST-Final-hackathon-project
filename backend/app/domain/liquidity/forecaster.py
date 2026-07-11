"""Online, explainable time-to-exhaustion forecasting.

The forecaster consumes actual balance deltas.  It estimates the current
drain rate with an exponentially weighted moving average (EWMA) and keeps a
bounded twelve-minute rate window to quantify uncertainty.  It deliberately
uses deterministic arithmetic rather than an opaque model: every forecast can
be reconstructed from the evidence returned to the UI.
"""
from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Deque


@dataclass(frozen=True, slots=True)
class RateSample:
    at: datetime
    drain_bdt_per_min: float


@dataclass(frozen=True, slots=True)
class LiquidityForecast:
    position_id: str
    balance_bdt: float
    ewma_drain_bdt_per_min: float
    predicted_tte_min: float | None
    ci95: tuple[float | None, float | None]
    confidence_score: float
    sample_count: int
    window_minutes: int
    status: str
    as_of: str

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["ci95"] = list(self.ci95)
        return data


@dataclass(slots=True)
class _PositionState:
    ewma_rate: float = 0.0
    last_at: datetime | None = None
    samples: Deque[RateSample] | None = None


class EWMALiquidityForecaster:
    """Maintain independent forecasts for shared cash and each provider."""

    def __init__(
        self,
        *,
        alpha: float = 0.35,
        window_minutes: int = 12,
        minimum_drain_bdt_per_min: float = 0.01,
    ) -> None:
        if not 0 < alpha <= 1:
            raise ValueError("alpha must be in (0, 1]")
        if window_minutes <= 0:
            raise ValueError("window_minutes must be positive")
        self.alpha = alpha
        self.window_minutes = window_minutes
        self.minimum_drain = minimum_drain_bdt_per_min
        self._states: dict[str, _PositionState] = {}

    def update(
        self,
        *,
        position_id: str,
        balance_bdt: float,
        delta_bdt: float,
        at: datetime,
        interval_hint_seconds: float = 60.0,
    ) -> LiquidityForecast:
        """Ingest one committed movement and return the new forecast.

        ``delta_bdt`` follows ledger convention: negative means depletion and
        positive means replenishment.  Credits therefore reduce the estimated
        net drain instead of being silently ignored.
        """
        if not position_id:
            raise ValueError("position_id is required")
        if balance_bdt < 0:
            raise ValueError("balance_bdt cannot be negative")
        if interval_hint_seconds <= 0:
            raise ValueError("interval_hint_seconds must be positive")

        state = self._states.setdefault(
            position_id,
            _PositionState(samples=deque()),
        )
        assert state.samples is not None

        if state.last_at is None:
            elapsed_min = interval_hint_seconds / 60.0
        else:
            elapsed_s = (at - state.last_at).total_seconds()
            # Replayed or concurrent events can share a timestamp.  The
            # explicit hint keeps the rate finite and reproducible.
            elapsed_min = max(elapsed_s, interval_hint_seconds, 1.0) / 60.0

        instantaneous_rate = -float(delta_bdt) / elapsed_min
        if not state.samples:
            state.ewma_rate = max(0.0, instantaneous_rate)
        else:
            state.ewma_rate = max(
                0.0,
                self.alpha * instantaneous_rate
                + (1.0 - self.alpha) * state.ewma_rate,
            )
        state.last_at = at
        state.samples.append(RateSample(at=at, drain_bdt_per_min=instantaneous_rate))

        cutoff = at - timedelta(minutes=self.window_minutes)
        while state.samples and state.samples[0].at < cutoff:
            state.samples.popleft()

        rates = [sample.drain_bdt_per_min for sample in state.samples]
        sample_count = len(rates)
        std = statistics.pstdev(rates) if sample_count > 1 else 0.0
        standard_error = std / math.sqrt(sample_count) if sample_count else 0.0
        margin = 1.96 * standard_error

        ewma = state.ewma_rate
        if balance_bdt == 0:
            estimate: float | None = 0.0
            ci: tuple[float | None, float | None] = (0.0, 0.0)
            status = "exhausted"
        elif ewma <= self.minimum_drain:
            estimate = None
            ci = (None, None)
            status = "stable_or_replenishing"
        else:
            estimate = balance_bdt / ewma
            high_rate = max(self.minimum_drain, ewma + margin)
            low_rate = ewma - margin
            lower_tte = balance_bdt / high_rate
            # If the lower confidence bound crosses zero drain, exhaustion is
            # not bounded above.  Return a conservative finite display bound
            # and lower the confidence score accordingly.
            upper_tte = (
                balance_bdt / low_rate
                if low_rate > self.minimum_drain
                else estimate * 2.0
            )
            ci = (lower_tte, upper_tte)
            status = "critical" if estimate <= 10 else "warning" if estimate <= 30 else "healthy"

        variability = std / max(abs(ewma), self.minimum_drain)
        evidence_factor = min(1.0, sample_count / 6.0)
        stability_factor = max(0.2, 1.0 - min(1.0, variability / 2.0))
        confidence = min(0.98, max(0.15, (0.25 + 0.73 * evidence_factor) * stability_factor))
        if estimate is None:
            confidence = min(confidence, 0.6)

        def rounded(value: float | None) -> float | None:
            return None if value is None else round(value, 2)

        return LiquidityForecast(
            position_id=position_id,
            balance_bdt=round(float(balance_bdt), 2),
            ewma_drain_bdt_per_min=round(ewma, 2),
            predicted_tte_min=rounded(estimate),
            ci95=(rounded(ci[0]), rounded(ci[1])),
            confidence_score=round(confidence, 3),
            sample_count=sample_count,
            window_minutes=self.window_minutes,
            status=status,
            as_of=at.isoformat(),
        )
