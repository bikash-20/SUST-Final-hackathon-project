"""Stateful, explainable behavioural anomaly detection.

The detector deliberately has no scenario or ground-truth input.  It consumes
the same fields an ingestion adapter would provide in production and evaluates
outgoing transactions within an event-time, provider-isolated sliding window.

The score combines five observable features:

* frequency of the dominant repeated amount;
* share of outgoing transactions at that amount;
* velocity of those repeated transactions;
* reuse of a small cluster of accounts; and
* regularity of the transaction cadence.

Scores are advisory evidence, not a fraud decision.  ``requires_human_review``
is consequently always true for a result that is surfaced to an operator.
"""
from __future__ import annotations

import math
import statistics
import threading
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Final, Iterable, Mapping


_MONEY_QUANTUM: Final = Decimal("0.01")
_VALID_DIRECTIONS: Final = frozenset({"in", "out"})


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _round_metric(value: float, places: int = 4) -> float:
    """Keep API evidence stable and readable across Python versions."""
    return round(value, places)


@dataclass(frozen=True, slots=True)
class TransactionObservation:
    """Minimal production-shaped input accepted by the detector.

    Unknown metadata, scenario names, and pre-computed labels are purposely not
    represented in this type, so they cannot influence a score.
    """

    transaction_id: str
    observed_at: datetime
    provider_id: str
    account_id: str
    amount_bdt: Decimal | int | float | str
    direction: str
    # Optional operator/demo context. None means infer from configured days;
    # True/False explicitly toggles the calendar state for a live injection.
    is_salary_window: bool | None = None


@dataclass(frozen=True, slots=True)
class AnomalyDetectorConfig:
    """Tuning values for the explainable detector."""

    window: timedelta = timedelta(minutes=12)
    review_threshold: float = 0.65
    minimum_repeated_transactions: int = 5
    whitelisted_providers: frozenset[str] = field(default_factory=frozenset)
    salary_period_days: frozenset[int] = field(
        default_factory=lambda: frozenset(range(1, 6))
    )
    broad_account_threshold: int = 10
    broad_volume_threshold: int = 12
    varied_dominant_ratio_max: float = 0.20
    salary_window_score_multiplier: float = 0.55
    salary_window_confidence_multiplier: float = 0.65

    def __post_init__(self) -> None:
        if self.window <= timedelta(0):
            raise ValueError("window must be positive")
        if not 0.0 < self.review_threshold < 1.0:
            raise ValueError("review_threshold must be between zero and one")
        if self.minimum_repeated_transactions < 3:
            raise ValueError("minimum_repeated_transactions must be at least 3")
        normalized = frozenset(p.strip().lower() for p in self.whitelisted_providers)
        if "" in normalized:
            raise ValueError("whitelisted provider IDs must not be blank")
        object.__setattr__(self, "whitelisted_providers", normalized)
        if any(day < 1 or day > 31 for day in self.salary_period_days):
            raise ValueError("salary_period_days must contain calendar days 1..31")
        if self.broad_account_threshold < 2 or self.broad_volume_threshold < 3:
            raise ValueError("calendar broad-activity thresholds are too small")
        for value in (
            self.varied_dominant_ratio_max,
            self.salary_window_score_multiplier,
            self.salary_window_confidence_multiplier,
        ):
            if not 0.0 < value <= 1.0:
                raise ValueError("calendar adjustment values must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class DetectionEvidence:
    """Quantitative evidence behind one detector evaluation."""

    window_minutes: float
    window_start: datetime
    window_end: datetime
    window_transaction_count: int
    outgoing_transaction_count: int
    dominant_repeated_amount_bdt: str | None
    dominant_repeated_amount_frequency: int
    dominant_repeated_amount_ratio: float
    distinct_account_count: int
    dominant_amount_distinct_account_count: int
    dominant_amount_max_transactions_per_account: int
    overall_velocity_per_minute: float
    dominant_amount_velocity_per_minute: float
    dominant_amount_span_seconds: float
    median_cadence_seconds: float | None
    cadence_mad_seconds: float | None
    cadence_regularity: float
    score_components: Mapping[str, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "window_minutes": self.window_minutes,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "window_transaction_count": self.window_transaction_count,
            "outgoing_transaction_count": self.outgoing_transaction_count,
            "dominant_repeated_amount_bdt": self.dominant_repeated_amount_bdt,
            "dominant_repeated_amount_frequency": self.dominant_repeated_amount_frequency,
            "dominant_repeated_amount_ratio": self.dominant_repeated_amount_ratio,
            "distinct_account_count": self.distinct_account_count,
            "dominant_amount_distinct_account_count": (
                self.dominant_amount_distinct_account_count
            ),
            "dominant_amount_max_transactions_per_account": (
                self.dominant_amount_max_transactions_per_account
            ),
            "overall_velocity_per_minute": self.overall_velocity_per_minute,
            "dominant_amount_velocity_per_minute": (
                self.dominant_amount_velocity_per_minute
            ),
            "dominant_amount_span_seconds": self.dominant_amount_span_seconds,
            "median_cadence_seconds": self.median_cadence_seconds,
            "cadence_mad_seconds": self.cadence_mad_seconds,
            "cadence_regularity": self.cadence_regularity,
            "score_components": dict(self.score_components),
        }


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """An explainable advisory evaluation for one provider window."""

    provider_id: str
    category: str
    detected_at: datetime
    triggered: bool
    severity: str
    risk_score: float
    confidence: float
    uncertainty: float
    score_interval: tuple[float, float]
    raw_risk_score: float
    calendar_adjustment_applied: bool
    calendar_context: str
    evidence: DetectionEvidence
    possible_benign_explanations: tuple[str, ...]
    requires_human_review: bool = True

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable API/SSE payload."""
        return {
            "provider_id": self.provider_id,
            "category": self.category,
            "detected_at": self.detected_at.isoformat(),
            "triggered": self.triggered,
            "severity": self.severity,
            "risk_score": self.risk_score,
            "confidence": self.confidence,
            "uncertainty": self.uncertainty,
            "score_interval": list(self.score_interval),
            "raw_risk_score": self.raw_risk_score,
            "calendar_adjustment_applied": self.calendar_adjustment_applied,
            "calendar_context": self.calendar_context,
            "evidence": self.evidence.to_dict(),
            "possible_benign_explanations": list(
                self.possible_benign_explanations
            ),
            "requires_human_review": self.requires_human_review,
        }


@dataclass(frozen=True, slots=True)
class _StoredObservation:
    transaction_id: str
    observed_at: datetime
    provider_id: str
    account_id: str
    amount_bdt: Decimal
    direction: str
    is_salary_window: bool | None

    @property
    def sort_key(self) -> tuple[datetime, str]:
        return self.observed_at, self.transaction_id


@dataclass(slots=True)
class _ProviderWindow:
    observations: list[_StoredObservation] = field(default_factory=list)
    transaction_ids: set[str] = field(default_factory=set)
    watermark: datetime | None = None


class SlidingWindowAnomalyDetector:
    """Provider-isolated 12-minute behavioural detector.

    ``observe`` is synchronous and thread-safe.  A transaction ID is counted at
    most once, which makes retry/replay ingestion idempotent.  Event-time
    watermarks permit mildly out-of-order input without extending the window.
    """

    CATEGORY: Final = "velocity_repeated_amount_account_cluster"

    def __init__(self, config: AnomalyDetectorConfig | None = None) -> None:
        self.config = config or AnomalyDetectorConfig()
        self._providers: dict[str, _ProviderWindow] = {}
        self._lock = threading.RLock()

    def observe(self, observation: TransactionObservation) -> DetectionResult:
        """Add an observation and evaluate its provider's current window."""
        item = self._validate(observation)
        with self._lock:
            state = self._providers.setdefault(item.provider_id, _ProviderWindow())
            state.watermark = max(state.watermark or item.observed_at, item.observed_at)
            self._evict_expired(state)

            cutoff = state.watermark - self.config.window
            if (
                item.observed_at >= cutoff
                and item.transaction_id not in state.transaction_ids
            ):
                keys = [event.sort_key for event in state.observations]
                insert_at = bisect_right(keys, item.sort_key)
                state.observations.insert(insert_at, item)
                state.transaction_ids.add(item.transaction_id)

            return self._evaluate(item.provider_id, state)

    def snapshot(
        self,
        provider_id: str,
        *,
        at: datetime | None = None,
    ) -> DetectionResult:
        """Evaluate a provider without inserting a transaction.

        Passing ``at`` advances event time and expires old observations.  This
        is useful for timer-driven SSE snapshots when a provider is quiet.
        """
        provider = provider_id.strip().lower()
        if not provider:
            raise ValueError("provider_id must not be blank")
        with self._lock:
            state = self._providers.setdefault(provider, _ProviderWindow())
            if at is not None:
                normalized_at = self._normalise_time(at)
                state.watermark = max(state.watermark or normalized_at, normalized_at)
                self._evict_expired(state)
            elif state.watermark is None:
                raise ValueError(
                    "at is required when snapshotting a provider with no observations"
                )
            return self._evaluate(provider, state)

    def reset(self, provider_id: str | None = None) -> None:
        """Clear all state or a single provider's state."""
        with self._lock:
            if provider_id is None:
                self._providers.clear()
            else:
                self._providers.pop(provider_id.strip().lower(), None)

    def _evict_expired(self, state: _ProviderWindow) -> None:
        assert state.watermark is not None
        cutoff = state.watermark - self.config.window
        first_live = 0
        while (
            first_live < len(state.observations)
            and state.observations[first_live].observed_at < cutoff
        ):
            first_live += 1
        if first_live:
            expired = state.observations[:first_live]
            del state.observations[:first_live]
            state.transaction_ids.difference_update(
                event.transaction_id for event in expired
            )

    def _evaluate(
        self,
        provider_id: str,
        state: _ProviderWindow,
    ) -> DetectionResult:
        assert state.watermark is not None
        observations = state.observations
        outgoing = [event for event in observations if event.direction == "out"]
        window_minutes = self.config.window.total_seconds() / 60.0

        amount_counts = Counter(event.amount_bdt for event in outgoing)
        dominant_amount: Decimal | None = None
        dominant_frequency = 0
        if amount_counts:
            # Stable tie-breaking: higher frequency first, then lower amount.
            dominant_amount, dominant_frequency = min(
                amount_counts.items(), key=lambda pair: (-pair[1], pair[0])
            )

        dominant_events = (
            [event for event in outgoing if event.amount_bdt == dominant_amount]
            if dominant_amount is not None
            else []
        )
        dominant_accounts = Counter(event.account_id for event in dominant_events)
        distinct_accounts = len({event.account_id for event in outgoing})
        dominant_distinct_accounts = len(dominant_accounts)
        max_per_account = max(dominant_accounts.values(), default=0)
        dominant_ratio = dominant_frequency / len(outgoing) if outgoing else 0.0

        observation_span_seconds = self._span_seconds(outgoing)
        dominant_span_seconds = self._span_seconds(dominant_events)
        overall_velocity = len(outgoing) / max(observation_span_seconds / 60.0, 1.0)
        dominant_velocity = dominant_frequency / max(
            dominant_span_seconds / 60.0, 1.0
        )

        intervals = self._intervals_seconds(dominant_events)
        median_cadence: float | None = None
        cadence_mad: float | None = None
        cadence_regularity = 0.0
        if intervals:
            median_cadence = float(statistics.median(intervals))
            deviations = [abs(value - median_cadence) for value in intervals]
            cadence_mad = float(statistics.median(deviations))
            if len(intervals) >= 3 and median_cadence > 0:
                cadence_regularity = _clamp(1.0 - cadence_mad / median_cadence)

        components = self._score_components(
            outgoing_count=len(outgoing),
            dominant_frequency=dominant_frequency,
            dominant_ratio=dominant_ratio,
            dominant_velocity=dominant_velocity,
            dominant_distinct_accounts=dominant_distinct_accounts,
            cadence_regularity=cadence_regularity,
        )
        raw_score = sum(
            components[name] * weight
            for name, weight in {
                "repeated_frequency": 0.30,
                "repeated_share": 0.15,
                "repeated_velocity": 0.20,
                "account_clustering": 0.20,
                "cadence_regularity": 0.15,
            }.items()
        )
        raw_score = _clamp(raw_score)

        explicit_calendar_values = [
            event.is_salary_window
            for event in outgoing
            if event.is_salary_window is not None
        ]
        salary_window_active = (
            explicit_calendar_values[-1]
            if explicit_calendar_values
            else state.watermark.day in self.config.salary_period_days
        )
        broad_varied_activity = (
            len(outgoing) >= self.config.broad_volume_threshold
            and distinct_accounts >= self.config.broad_account_threshold
            and dominant_ratio <= self.config.varied_dominant_ratio_max
        )
        # Lightweight calendar heuristic only: broad, varied activity during
        # a configured/explicit salary window gets a conservative reduction.
        # Narrow repeated-amount clusters never receive this adjustment and
        # still require human review when they cross the ordinary threshold.
        calendar_adjustment_applied = salary_window_active and broad_varied_activity
        score = (
            raw_score * self.config.salary_window_score_multiplier
            if calendar_adjustment_applied
            else raw_score
        )
        score = _clamp(score)

        enough_repeats = (
            dominant_frequency >= self.config.minimum_repeated_transactions
        )
        triggered = enough_repeats and score >= self.config.review_threshold
        confidence = self._confidence(
            provider_id=provider_id,
            dominant_frequency=dominant_frequency,
            dominant_span_seconds=dominant_span_seconds,
            interval_count=len(intervals),
        )
        if calendar_adjustment_applied:
            confidence *= self.config.salary_window_confidence_multiplier
        uncertainty = 1.0 - confidence
        interval_radius = 0.05 + uncertainty * 0.25
        score_interval = (
            _round_metric(_clamp(score - interval_radius)),
            _round_metric(_clamp(score + interval_radius)),
        )

        evidence = DetectionEvidence(
            window_minutes=_round_metric(window_minutes, 2),
            window_start=state.watermark - self.config.window,
            window_end=state.watermark,
            window_transaction_count=len(observations),
            outgoing_transaction_count=len(outgoing),
            dominant_repeated_amount_bdt=(
                format(dominant_amount, ".2f") if dominant_amount is not None else None
            ),
            dominant_repeated_amount_frequency=dominant_frequency,
            dominant_repeated_amount_ratio=_round_metric(dominant_ratio),
            distinct_account_count=distinct_accounts,
            dominant_amount_distinct_account_count=dominant_distinct_accounts,
            dominant_amount_max_transactions_per_account=max_per_account,
            overall_velocity_per_minute=_round_metric(overall_velocity),
            dominant_amount_velocity_per_minute=_round_metric(dominant_velocity),
            dominant_amount_span_seconds=_round_metric(dominant_span_seconds, 2),
            median_cadence_seconds=(
                _round_metric(median_cadence, 2)
                if median_cadence is not None
                else None
            ),
            cadence_mad_seconds=(
                _round_metric(cadence_mad, 2) if cadence_mad is not None else None
            ),
            cadence_regularity=_round_metric(cadence_regularity),
            score_components={
                key: _round_metric(value) for key, value in components.items()
            },
        )
        rounded_score = _round_metric(score)
        rounded_confidence = _round_metric(confidence)
        benign_explanations = self._benign_explanations(
            provider_id=provider_id,
            cadence_regularity=cadence_regularity,
            dominant_distinct_accounts=dominant_distinct_accounts,
        )
        if calendar_adjustment_applied:
            benign_explanations += (
                "A simple calendar-aware salary-period heuristic reduced this score; "
                "false positives and false negatives still require human review.",
            )
        return DetectionResult(
            provider_id=provider_id,
            category=self.CATEGORY,
            detected_at=state.watermark,
            triggered=triggered,
            severity=self._severity(rounded_score, triggered),
            risk_score=rounded_score,
            confidence=rounded_confidence,
            uncertainty=_round_metric(1.0 - rounded_confidence),
            score_interval=score_interval,
            raw_risk_score=_round_metric(raw_score),
            calendar_adjustment_applied=calendar_adjustment_applied,
            calendar_context=(
                "simple salary-window adjustment: broad accounts and varied amounts"
                if calendar_adjustment_applied
                else (
                    "salary window active; narrow/repeated pattern not adjusted"
                    if salary_window_active
                    else "outside configured salary window"
                )
            ),
            evidence=evidence,
            possible_benign_explanations=benign_explanations,
        )

    def _score_components(
        self,
        *,
        outgoing_count: int,
        dominant_frequency: int,
        dominant_ratio: float,
        dominant_velocity: float,
        dominant_distinct_accounts: int,
        cadence_regularity: float,
    ) -> dict[str, float]:
        repeated_frequency = _clamp((dominant_frequency - 2.0) / 8.0)
        repeated_share = _clamp((dominant_ratio - 0.08) / 0.30)
        repeated_velocity = _clamp((dominant_velocity - 0.25) / 0.75)

        average_per_cluster_account = (
            dominant_frequency / dominant_distinct_accounts
            if dominant_distinct_accounts
            else 0.0
        )
        multi_account_spread = _clamp((dominant_distinct_accounts - 1.0) / 3.0)
        account_reuse = _clamp((average_per_cluster_account - 1.0) / 2.0)
        account_clustering = 0.55 * multi_account_spread + 0.45 * account_reuse

        # Cadence is not considered reliable until four repeated events have
        # produced at least three intervals.
        reliable_cadence = cadence_regularity if dominant_frequency >= 4 else 0.0
        if outgoing_count == 0:
            repeated_share = 0.0

        return {
            "repeated_frequency": repeated_frequency,
            "repeated_share": repeated_share,
            "repeated_velocity": repeated_velocity,
            "account_clustering": _clamp(account_clustering),
            "cadence_regularity": reliable_cadence,
        }

    def _confidence(
        self,
        *,
        provider_id: str,
        dominant_frequency: int,
        dominant_span_seconds: float,
        interval_count: int,
    ) -> float:
        sample_adequacy = _clamp((dominant_frequency - 2.0) / 8.0)
        window_coverage = _clamp(
            dominant_span_seconds / self.config.window.total_seconds()
        )
        cadence_adequacy = _clamp(interval_count / 8.0)
        confidence = (
            0.30
            + 0.40 * sample_adequacy
            + 0.20 * window_coverage
            + 0.10 * cadence_adequacy
        )
        if provider_id in self.config.whitelisted_providers:
            # An allowlist is contextual evidence, never a silent suppression.
            confidence -= 0.15
        return _clamp(confidence, 0.20, 0.95)

    def _benign_explanations(
        self,
        *,
        provider_id: str,
        cadence_regularity: float,
        dominant_distinct_accounts: int,
    ) -> tuple[str, ...]:
        explanations = [
            "A scheduled merchant, payroll, or aid-disbursement batch may repeat one amount.",
            "A festival campaign or common price point may create legitimate amount concentration.",
            "Provider retries or delayed replay may temporarily increase observed velocity.",
        ]
        if cadence_regularity >= 0.8:
            explanations.append(
                "A legitimate automated schedule may explain the regular cadence."
            )
        if dominant_distinct_accounts >= 2:
            explanations.append(
                "One merchant serving several customers may explain the account cluster."
            )
        if provider_id in self.config.whitelisted_providers:
            explanations.append(
                "The provider is allowlisted; verify the allowlist scope and expiry before action."
            )
        return tuple(explanations)

    @staticmethod
    def _severity(score: float, triggered: bool) -> str:
        if not triggered:
            return "info"
        return "high" if score >= 0.82 else "warn"

    @staticmethod
    def _span_seconds(events: Iterable[_StoredObservation]) -> float:
        materialized = list(events)
        if len(materialized) < 2:
            return 0.0
        return max(
            0.0,
            (materialized[-1].observed_at - materialized[0].observed_at).total_seconds(),
        )

    @staticmethod
    def _intervals_seconds(events: Iterable[_StoredObservation]) -> list[float]:
        materialized = list(events)
        return [
            max(
                0.0,
                (
                    materialized[index].observed_at
                    - materialized[index - 1].observed_at
                ).total_seconds(),
            )
            for index in range(1, len(materialized))
        ]

    def _validate(self, observation: TransactionObservation) -> _StoredObservation:
        transaction_id = observation.transaction_id.strip()
        provider_id = observation.provider_id.strip().lower()
        account_id = observation.account_id.strip()
        direction = observation.direction.strip().lower()
        if not transaction_id:
            raise ValueError("transaction_id must not be blank")
        if not provider_id:
            raise ValueError("provider_id must not be blank")
        if not account_id:
            raise ValueError("account_id must not be blank")
        if direction not in _VALID_DIRECTIONS:
            raise ValueError("direction must be 'in' or 'out'")
        observed_at = self._normalise_time(observation.observed_at)
        try:
            amount = Decimal(str(observation.amount_bdt)).quantize(
                _MONEY_QUANTUM, rounding=ROUND_HALF_UP
            )
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("amount_bdt must be a finite number") from exc
        if not amount.is_finite() or amount <= 0:
            raise ValueError("amount_bdt must be a positive finite number")
        return _StoredObservation(
            transaction_id=transaction_id,
            observed_at=observed_at,
            provider_id=provider_id,
            account_id=account_id,
            amount_bdt=amount,
            direction=direction,
            is_salary_window=observation.is_salary_window,
        )

    @staticmethod
    def _normalise_time(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        timestamp = value.astimezone(timezone.utc)
        if not math.isfinite(timestamp.timestamp()):
            raise ValueError("observed_at must be finite")
        return timestamp
