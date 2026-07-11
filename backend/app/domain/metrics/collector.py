"""Bounded, thread-safe runtime evidence metrics.

The collector intentionally records only observations supplied by the live
runtime.  It does not seed defaults or synthesize demo values: an empty
collector reports zero counts and ``None`` for undefined rates and
percentiles.

Only rolling sample collections consume memory.  Cumulative counters are
integers and the pending shortage-warning map is capped, so the collector's
memory use is bounded independently of process uptime.
"""
from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Final, Iterator


DEFAULT_SAMPLE_CAPACITY: Final = 2_048
DEFAULT_PENDING_FORECAST_CAPACITY: Final = 1_024


def _finite_non_negative(value: float, *, field: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be a finite, non-negative number")
    return number


def _aware_utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _percentile(values: tuple[float, ...], fraction: float) -> float | None:
    """Return a linearly interpolated percentile for an immutable sample."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def _rounded(value: float | None, places: int = 3) -> float | None:
    return None if value is None else round(value, places)


class RuntimeMetricsCollector:
    """Collect measured operational evidence without external dependencies.

    All mutation and snapshot operations are protected by one re-entrant
    lock.  ``snapshot`` first copies the bounded samples under the lock and
    computes percentiles from those immutable copies, yielding a coherent
    point-in-time view even while worker threads continue recording.
    """

    def __init__(
        self,
        *,
        sample_capacity: int = DEFAULT_SAMPLE_CAPACITY,
        pending_forecast_capacity: int = DEFAULT_PENDING_FORECAST_CAPACITY,
    ) -> None:
        if isinstance(sample_capacity, bool) or sample_capacity <= 0:
            raise ValueError("sample_capacity must be a positive integer")
        if (
            isinstance(pending_forecast_capacity, bool)
            or pending_forecast_capacity <= 0
        ):
            raise ValueError(
                "pending_forecast_capacity must be a positive integer"
            )
        self._sample_capacity = int(sample_capacity)
        self._pending_forecast_capacity = int(pending_forecast_capacity)
        self._lock = threading.RLock()
        self._processing_latency_ms: deque[float] = deque(
            maxlen=self._sample_capacity
        )
        self._shortage_lead_time_seconds: deque[float] = deque(
            maxlen=self._sample_capacity
        )
        self._pending_shortage_forecasts: OrderedDict[str, datetime] = (
            OrderedDict()
        )
        self._tick_success_count = 0
        self._tick_failure_count = 0
        self._anomaly_evaluation_count = 0
        self._anomaly_detection_count = 0
        self._explained_detection_count = 0
        self._forecast_count = 0
        self._observed_shortage_count = 0
        self._matched_shortage_count = 0

    def record_tick(self, *, success: bool, latency_ms: float) -> None:
        """Record one completed processing attempt and its measured latency."""
        if not isinstance(success, bool):
            raise TypeError("success must be a bool")
        measured_latency = _finite_non_negative(
            latency_ms, field="latency_ms"
        )
        with self._lock:
            self._processing_latency_ms.append(measured_latency)
            if success:
                self._tick_success_count += 1
            else:
                self._tick_failure_count += 1

    @contextmanager
    def measure_tick(self) -> Iterator[None]:
        """Measure a synchronous or async-call-site block with a monotonic clock.

        The context manager itself is synchronous, but it can wrap ``await``
        expressions inside an async function.  Escaping exceptions are
        recorded as failed attempts and are never swallowed.
        """
        started_ns = time.perf_counter_ns()
        try:
            yield
        except BaseException:
            elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
            self.record_tick(success=False, latency_ms=elapsed_ms)
            raise
        else:
            elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
            self.record_tick(success=True, latency_ms=elapsed_ms)

    def record_anomaly_evaluation(
        self,
        *,
        detected: bool,
        explanation_present: bool,
    ) -> None:
        """Record one detector evaluation and explanation coverage.

        Coverage uses detected advisories as its denominator.  Normal
        evaluations do not require an operator explanation and therefore do
        not dilute the metric.
        """
        if not isinstance(detected, bool):
            raise TypeError("detected must be a bool")
        if not isinstance(explanation_present, bool):
            raise TypeError("explanation_present must be a bool")
        with self._lock:
            self._anomaly_evaluation_count += 1
            if detected:
                self._anomaly_detection_count += 1
                if explanation_present:
                    self._explained_detection_count += 1

    def record_forecast(
        self,
        *,
        position_id: str,
        observed_at: datetime,
        shortage_expected: bool,
    ) -> None:
        """Record one real forecast and optionally open a lead-time clock.

        The first shortage warning for a position starts its clock.  Repeated
        warning forecasts do not move that clock forward.  A non-warning
        forecast closes any outstanding clock, reflecting a recovered
        position rather than carrying stale warning evidence into a later
        incident.
        """
        position = position_id.strip()
        if not position:
            raise ValueError("position_id must not be blank")
        if not isinstance(shortage_expected, bool):
            raise TypeError("shortage_expected must be a bool")
        at = _aware_utc(observed_at, field="observed_at")

        with self._lock:
            self._forecast_count += 1
            if not shortage_expected:
                self._pending_shortage_forecasts.pop(position, None)
                return
            if position in self._pending_shortage_forecasts:
                return
            self._pending_shortage_forecasts[position] = at
            while (
                len(self._pending_shortage_forecasts)
                > self._pending_forecast_capacity
            ):
                self._pending_shortage_forecasts.popitem(last=False)

    def record_shortage_observed(
        self,
        *,
        position_id: str,
        observed_at: datetime,
    ) -> float | None:
        """Pair an observed shortage with its prior warning, if available.

        Returns the measured lead time in seconds.  An unmatched shortage is
        still counted, but contributes no percentile sample.
        """
        position = position_id.strip()
        if not position:
            raise ValueError("position_id must not be blank")
        at = _aware_utc(observed_at, field="observed_at")

        with self._lock:
            self._observed_shortage_count += 1
            forecast_at = self._pending_shortage_forecasts.pop(position, None)
            if forecast_at is None:
                return None
            lead_seconds = (at - forecast_at).total_seconds()
            if lead_seconds < 0:
                # Out-of-order evidence is not a valid lead-time sample.
                return None
            self._shortage_lead_time_seconds.append(lead_seconds)
            self._matched_shortage_count += 1
            return lead_seconds

    def reset(self) -> None:
        """Clear all runtime evidence (primarily for isolated test/app runs)."""
        with self._lock:
            self._processing_latency_ms.clear()
            self._shortage_lead_time_seconds.clear()
            self._pending_shortage_forecasts.clear()
            self._tick_success_count = 0
            self._tick_failure_count = 0
            self._anomaly_evaluation_count = 0
            self._anomaly_detection_count = 0
            self._explained_detection_count = 0
            self._forecast_count = 0
            self._observed_shortage_count = 0
            self._matched_shortage_count = 0

    def snapshot(self) -> dict[str, Any]:
        """Return a coherent, JSON-serialisable point-in-time snapshot."""
        with self._lock:
            latency_samples = tuple(self._processing_latency_ms)
            lead_samples = tuple(self._shortage_lead_time_seconds)
            successes = self._tick_success_count
            failures = self._tick_failure_count
            evaluations = self._anomaly_evaluation_count
            detections = self._anomaly_detection_count
            explained = self._explained_detection_count
            forecast_count = self._forecast_count
            observed_shortages = self._observed_shortage_count
            matched_shortages = self._matched_shortage_count
            pending_forecasts = len(self._pending_shortage_forecasts)

        tick_total = successes + failures
        lead_mean = (
            sum(lead_samples) / len(lead_samples) if lead_samples else None
        )
        return {
            "processing_latency_ms": {
                "sample_count": len(latency_samples),
                "p50": _rounded(_percentile(latency_samples, 0.50)),
                "p95": _rounded(_percentile(latency_samples, 0.95)),
            },
            "ticks": {
                "total_count": tick_total,
                "success_count": successes,
                "failure_count": failures,
                "success_rate": (
                    None if tick_total == 0 else round(successes / tick_total, 6)
                ),
            },
            "anomalies": {
                "evaluation_count": evaluations,
                "detection_count": detections,
                "detection_rate": (
                    None if evaluations == 0 else round(detections / evaluations, 6)
                ),
                "explained_detection_count": explained,
                "explanation_coverage_pct": (
                    None if detections == 0 else round(100.0 * explained / detections, 3)
                ),
            },
            "forecasts": {
                "count": forecast_count,
                "pending_shortage_warning_count": pending_forecasts,
                "observed_shortage_count": observed_shortages,
                "matched_shortage_count": matched_shortages,
                "shortage_lead_time_seconds": {
                    "sample_count": len(lead_samples),
                    "min": _rounded(min(lead_samples) if lead_samples else None),
                    "mean": _rounded(lead_mean),
                    "p50": _rounded(_percentile(lead_samples, 0.50)),
                    "p90": _rounded(_percentile(lead_samples, 0.90)),
                    "max": _rounded(max(lead_samples) if lead_samples else None),
                },
            },
        }


# One process-local collector shared by the simulation workers and API route.
runtime_metrics = RuntimeMetricsCollector()
