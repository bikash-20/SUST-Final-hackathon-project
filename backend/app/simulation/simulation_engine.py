"""simulation_engine.py — Deterministic 60x ticker with no-lost-tick guarantee.

Phase-2 requirement (verbatim):

    Because the ticker runs at 60× speed, standard network latencies
    could cause state drift. You must ensure that your simulation_engine.py
    runs tasks in an asynchronous event queue (using FastAPI's
    BackgroundTasks or an in-memory asyncio.Queue) so that no
    transaction tick is dropped.

Design
------
* A single ``asyncio.Queue`` (``_tick_queue``) accepts tick jobs from any
  source — scenario injectors, the wall-clock pump, scenario replays.
* A pool of ``_WORKER_COUNT`` consumer tasks drains the queue forever.
  Each worker:
      - pulls a ``Tick`` off the queue,
      - executes the appropriate domain command (e.g. a cash ledger
        deduction),
      - acks by appending a row to ``shared.simulation_events`` BEFORE
        the work is considered done. This is what makes the stream
        *replayable*: a late subscriber re-reads the table from its
        last-seen ``sim_time`` and never misses a tick.
* Wall-clock pump advances a deterministic ``sim_time`` at
  ``SPEED_MULTIPLIER`` simulated seconds per wall-clock second.
* Scenario C (delayed/inconsistent feed) is implemented as a
  *telemetry-only* injector. It writes a ``{'event_type': 'inconsistency',
  'confidence_score': <0.5}`` payload to ``simulation_events`` and never
  touches the historical cash ledger rows. Frontend consumes the
  payload and flips ``confidence_score < 0.5`` which triggers the
  degraded layout per F1.

No accepted tick is silently discarded while the process is running:
    * The producer enqueues into a bounded asyncio.Queue and persists a
      correlated ``tick_id`` audit row to ``shared.simulation_events``.
    * If a worker raises, the tick is re-enqueued with a bounded retry
      counter. ``VersionConflict`` uses a non-blocking jittered backoff
      (``random.uniform(0.01, 0.05)``) to defeat lock starvation under
      the 60x multi-provider load — there is no fixed-wait hot spot.
    * After ``MAX_TICK_RETRIES`` the tick is **durably** persisted to
      ``shared.dead_letter_logs`` (NOT held in process memory). There is
      therefore no unbounded memory vector — the process RSS is flat
      regardless of failure volume.

A process crash can leave an ``enqueued`` row without a terminal row. That is
durably visible for reconciliation, but this prototype deliberately does not
auto-replay arbitrary incomplete monetary commands because only provider
customer exchanges currently have database-level exact-once semantics.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Final

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from asyncpg.exceptions import CheckViolationError

from app.infrastructure.database import session_scope, validate_provider_id
from app.infrastructure.broadcaster import broadcaster
from app.domain.liquidity import (
    EWMALiquidityForecaster,
    HistoricalAnalytics,
    HistoricalContext,
)
from app.domain.provider import (
    InsufficientProviderBalance,
    provider_ledger,
)
from app.domain.coordination.state_machine import coordination_fsm
from app.domain.metrics import runtime_metrics
from app.domain.risk import (
    AnomalyDetectorConfig,
    SlidingWindowAnomalyDetector,
    TransactionObservation,
)
from app.domain.shared.cash_ledger import (
    InsufficientCash,
    VersionConflict,
    shared_cash_ledger,
)

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Tunables
# ----------------------------------------------------------------------
SPEED_MULTIPLIER: Final = 60      # 1 wall-sec == 60 sim-sec
WORKER_COUNT: Final = 4          # consumer tasks draining the queue
QUEUE_MAX_BACKLOG: Final = 10_000 # soft cap; raises if exceeded (visible)
MAX_TICK_RETRIES: Final = 3
DEFAULT_SEED: Final = 20260111    # deterministic RNG seed


# ----------------------------------------------------------------------
# Tick model
# ----------------------------------------------------------------------
@dataclass(slots=True)
class Tick:
    """A unit of work the ticker must execute.

    ``sim_time`` is the deterministic clock — never wall-time — so a
    replayed demo is byte-identical to the live one.
    """
    tick_id: str
    sim_time: datetime
    kind: str                              # 'cash_out','cash_in','inconsistency','noop',
                                         # 'provider_drain','provider_txn',
                                         # 'advisory_tte','coordination_awaiting'
    payload: dict[str, Any]
    retries: int = 0
    on_complete: Callable[["Tick", dict[str, Any]], Awaitable[None]] | None = field(default=None)
    on_error: Callable[["Tick", dict[str, Any]], Awaitable[None]] | None = field(default=None)

    def with_retry(self) -> "Tick":
        self.retries += 1
        return self


# ----------------------------------------------------------------------
# Ticker
# ----------------------------------------------------------------------
class SimulationEngine:
    """Deterministic, lossless, queue-backed ticker.

    Lifecycle::

        engine = SimulationEngine(agent_id=..., seed=DEFAULT_SEED)
        await engine.start()
        await engine.enqueue_tick(Tick(...))
        ...
        await engine.stop()
    """

    def __init__(self, *, agent_id: uuid.UUID, seed: int = DEFAULT_SEED) -> None:
        self.agent_id = agent_id
        self._rng = random.Random(seed)
        self._queue: asyncio.Queue[Tick] = asyncio.Queue(maxsize=QUEUE_MAX_BACKLOG)
        self._sim_time: datetime = datetime(2026, 7, 11, 9, 0, 0, tzinfo=timezone.utc)
        self._wall_origin: datetime | None = None
        self._workers: list[asyncio.Task[None]] = []
        self._pump: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        # NOTE: dead-letter is no longer held in process memory; it is
        # persisted to shared.dead_letter_logs (see _persist_dead_letter).
        # This removes the unbounded memory vector.
        self._dead_letter_count: int = 0
        # Live pause/resume — the demo pauses by calling ``pause()``.
        self._paused = asyncio.Event()
        self._paused.set()  # running by default
        # Stateful online analytics are protected from concurrent worker
        # interleaving. Database writes remain asynchronous, but evidence is
        # ingested in the same order as committed domain movements.
        self._analytics_lock = asyncio.Lock()
        self._liquidity = EWMALiquidityForecaster(alpha=0.35, window_minutes=12)
        self._historical = HistoricalAnalytics()
        self._historical_warning_logged = False
        allowlisted = frozenset(
            item.strip().lower()
            for item in os.getenv("ANOMALY_ALLOWLISTED_PROVIDERS", "").split(",")
            if item.strip()
        )
        self._anomaly_detector = SlidingWindowAnomalyDetector(
            AnomalyDetectorConfig(
                window=timedelta(minutes=12),
                whitelisted_providers=allowlisted,
            )
        )
        self._coordination_lock = asyncio.Lock()
        self._active_advisories: dict[str, str] = {}
        self._observed_metric_shortages: set[str] = set()
        self._generated_transaction_count = 0

    # ---------------- clock -----------------------------------------------

    @property
    def sim_time(self) -> datetime:
        return self._sim_time

    def _advance_clock(self) -> None:
        """Advance ``sim_time`` by SPEED_MULTIPLIER seconds."""
        self._sim_time = self._sim_time + timedelta(seconds=SPEED_MULTIPLIER)

    # ---------------- lifecycle -------------------------------------------

    async def start(self) -> None:
        """Spawn workers + wall-clock pump. Idempotent."""
        if self._workers:
            return
        await self._restore_clock_watermark()
        self._stop_event.clear()
        self._paused.set()
        self._wall_origin = datetime.now(timezone.utc)
        self._workers = [
            asyncio.create_task(self._worker_loop(i), name=f"sim-worker-{i}")
            for i in range(WORKER_COUNT)
        ]
        self._pump = asyncio.create_task(self._pump_loop(), name="sim-pump")
        log.info("simulation_engine started workers=%d speed=%dx",
                 WORKER_COUNT, SPEED_MULTIPLIER)

    async def _restore_clock_watermark(self) -> None:
        """Never let a process restart move durable simulation time backward."""
        async with session_scope() as session:
            watermark = (
                await session.execute(
                    text(
                        """
                        SELECT GREATEST(
                            COALESCE(
                                (SELECT max(sim_time)
                                   FROM shared.simulation_events),
                                :floor
                            ),
                            COALESCE(
                                (SELECT max(sim_time)
                                   FROM shared.coordination_alerts),
                                :floor
                            )
                        )
                        """
                    ),
                    {"floor": self._sim_time},
                )
            ).scalar_one()
        if watermark > self._sim_time:
            self._sim_time = watermark

    async def stop(self) -> None:
        self._stop_event.set()
        self._paused.set()  # unblock workers waiting on pause
        for w in self._workers:
            w.cancel()
        if self._pump:
            self._pump.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        if self._pump:
            await asyncio.gather(self._pump, return_exceptions=True)
        self._workers = []
        self._pump = None
        log.info("simulation_engine stopped dead_letter_count=%d",
                 self._dead_letter_count)

    def pause(self) -> None:
        self._paused.clear()

    def resume(self) -> None:
        self._paused.set()

    # ---------------- public enqueue API ----------------------------------

    async def enqueue_tick(self, tick: Tick) -> None:
        """Enqueue a tick. Blocks if the soft backlog cap is hit."""
        # Establish the durable/auditable lifecycle boundary before making the
        # tick visible to workers. Queue-first ordering allowed a very fast
        # worker to broadcast ``tick.done`` before ``tick.enqueued``.
        await self._persist_event(tick, status="enqueued")
        await self._queue.put(tick)

    # ---------------- workers ---------------------------------------------

    async def _worker_loop(self, worker_id: int) -> None:
        log.debug("worker %d up", worker_id)
        while not self._stop_event.is_set():
            await self._paused.wait()
            try:
                tick = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            try:
                # Scenario injectors schedule event-time observations ahead of
                # the wall-clock pump. Keep the public simulation clock at the
                # latest processed watermark so later human audit actions
                # cannot appear to happen before their alert.
                if tick.sim_time > self._sim_time:
                    self._sim_time = tick.sim_time
                with runtime_metrics.measure_tick():
                    result = await self._dispatch(tick)
                    if tick.on_complete:
                        await tick.on_complete(tick, result)
                    await self._persist_event(tick, status="done", extra=result)
            except VersionConflict as vc:
                # Jittered backoff (10–50 ms) defeats lock starvation
                # under 60x multi-provider load by spreading retry
                # attempts across a non-deterministic window.
                backoff = random.uniform(0.01, 0.05)
                if tick.retries < MAX_TICK_RETRIES:
                    log.warning(
                        "tick %s version-conflict retry=%d backoff=%.3fs err=%s",
                        tick.tick_id, tick.retries, backoff, vc,
                    )
                    await asyncio.sleep(backoff)
                    await self._queue.put(tick.with_retry())
                else:
                    log.error(
                        "tick %s exhausted retries (%d) -> dead_letter_logs: %s",
                        tick.tick_id, tick.retries, vc,
                    )
                    await self._persist_dead_letter(tick, str(vc))
                    await self._persist_event(tick, status="dead_letter",
                                              error=str(vc))
                    await self._notify_tick_error(tick, {
                        "error": "version_conflict",
                        "message": "The transaction could not be committed after retries.",
                    })
            except InsufficientCash as ic:
                # Business-rule violation: do not retry. Park durably.
                log.error("tick %s insufficient-cash -> dead_letter_logs: %s",
                          tick.tick_id, ic)
                await self._persist_dead_letter(tick, str(ic))
                await self._persist_event(tick, status="dead_letter",
                                          error=str(ic))
                await self._notify_tick_error(
                    tick, await self._insufficient_shared_cash_payload()
                )
            except InsufficientProviderBalance as ipb:
                log.error("tick %s insufficient-provider-balance -> dead letter: %s",
                          tick.tick_id, ipb)
                await self._persist_dead_letter(tick, str(ipb))
                await self._persist_event(tick, status="dead_letter",
                                          error=str(ipb))
                await self._notify_tick_error(tick, {
                    "error": "insufficient_provider_balance",
                    "message": str(ipb),
                })
            except (IntegrityError, CheckViolationError) as exc:
                if self._is_insufficient_shared_cash_error(exc):
                    payload = await self._insufficient_shared_cash_payload()
                    log.warning(
                        "tick %s rejected: insufficient shared cash available=%s",
                        tick.tick_id,
                        payload["available_balance"],
                    )
                    await self._persist_dead_letter(tick, payload["message"])
                    await self._persist_event(
                        tick, status="dead_letter", error=payload["message"]
                    )
                    await self._notify_tick_error(tick, payload)
                else:
                    log.exception("tick %s integrity failure", tick.tick_id)
                    await self._persist_dead_letter(tick, str(exc))
                    await self._persist_event(tick, status="fatal", error=str(exc))
                    await self._notify_tick_error(tick, {
                        "error": "integrity_error",
                        "message": "The transaction could not be committed.",
                    })
            except Exception as exc:  # non-retryable
                log.exception("tick %s fatal", tick.tick_id)
                await self._persist_dead_letter(tick, str(exc))
                await self._persist_event(tick, status="fatal", error=str(exc))
                await self._notify_tick_error(tick, {
                    "error": "tick_failed",
                    "message": "The transaction tick failed to complete.",
                })
            finally:
                self._queue.task_done()

    @staticmethod
    def _is_insufficient_shared_cash_error(exc: BaseException) -> bool:
        """Recognize PostgreSQL's rejected-negative-cash error through wrappers."""
        candidates: list[BaseException | None] = [
            exc,
            getattr(exc, "orig", None),
            exc.__cause__,
        ]
        return any(
            candidate is not None
            and (
                "insufficient shared cash" in str(candidate).lower()
                or (
                    getattr(candidate, "sqlstate", None) == "23514"
                    and "shared cash" in str(candidate).lower()
                )
            )
            for candidate in candidates
        )

    async def _insufficient_shared_cash_payload(self) -> dict[str, Any]:
        balance, _ = await shared_cash_ledger.get_balance(self.agent_id)
        return {
            "error": "insufficient_shared_cash",
            "message": "Not enough shared cash to complete this transaction burst.",
            "available_balance": float(balance),
        }

    @staticmethod
    async def _notify_tick_error(tick: Tick, payload: dict[str, Any]) -> None:
        if tick.on_error is None:
            return
        try:
            await tick.on_error(tick, payload)
        except Exception:
            log.exception("tick %s error callback failed", tick.tick_id)

    async def _dispatch(self, tick: Tick) -> dict[str, Any]:
        """Translate a Tick into a domain command."""
        if tick.kind == "cash_out":
            amount = float(tick.payload["amount_bdt"])
            async with self._analytics_lock:
                res = await shared_cash_ledger.deduct(
                    self.agent_id,
                    _D(amount),
                    reason=str(tick.payload.get("reason", "cash_out")),
                    sim_time=tick.sim_time,
                )
                forecast = self._liquidity.update(
                    position_id="shared_cash",
                    balance_bdt=float(res.new_balance),
                    delta_bdt=-amount,
                    at=tick.sim_time,
                    interval_hint_seconds=float(
                        tick.payload.get("interval_seconds", 1.0)
                    ),
                )
                self._record_forecast_metric(forecast, tick.sim_time)
                forecast_payload = await self._forecast_payload(forecast, tick.sim_time)
            return {
                "shared_cash_balance": float(res.new_balance),
                "version": res.version_id,
                "liquidity_forecast": forecast_payload,
            }
        if tick.kind == "cash_in":
            amount = float(tick.payload["amount_bdt"])
            async with self._analytics_lock:
                res = await shared_cash_ledger.credit(
                    self.agent_id,
                    _D(amount),
                    reason=str(tick.payload.get("reason", "cash_in")),
                    sim_time=tick.sim_time,
                )
                forecast = self._liquidity.update(
                    position_id="shared_cash",
                    balance_bdt=float(res.new_balance),
                    delta_bdt=amount,
                    at=tick.sim_time,
                    interval_hint_seconds=float(
                        tick.payload.get("interval_seconds", 60.0)
                    ),
                )
                self._record_forecast_metric(forecast, tick.sim_time)
                forecast_payload = await self._forecast_payload(forecast, tick.sim_time)
            return {
                "shared_cash_balance": float(res.new_balance),
                "version": res.version_id,
                "liquidity_forecast": forecast_payload,
            }
        if tick.kind == "inconsistency":
            # Strict Phase-2 rule: do NOT mutate historical state.
            # Emit a telemetry-only payload; let the frontend degrade.
            confidence = float(tick.payload.get("confidence_score", 0.42))
            provider = validate_provider_id(str(tick.payload["provider_id"]))
            alert_token = await self._raise_advisory_once(
                key=f"data-quality:{provider}",
                provider_id=provider,
                severity="high",
                sim_time=tick.sim_time,
                reason=(
                    f"Data quality warning requires review: {provider} feed is late or "
                    f"conflicting (confidence {confidence:.0%}). Owner: provider operations. "
                    "Safe next step: verify the provider feed and use separate last-known "
                    "balances; do not merge balances or imply cross-provider conversion."
                ),
            )
            return {
                "event_type": "inconsistency",
                "confidence_score": confidence,    # < 0.5 by design
                "provider_id": provider,
                "stale_after_s": tick.payload.get("stale_after_s", 90),
                "coordination_alert_token": alert_token,
                "evidence": "late or conflicting provider feed",
            }
        if tick.kind == "provider_drain":
            provider = validate_provider_id(str(tick.payload["provider_id"]))
            amount = float(tick.payload["amount_bdt"])
            if amount <= 0:
                raise ValueError("provider drain amount must be positive")
            async with self._analytics_lock:
                balance = await provider_ledger.apply_delta(
                    provider_id=provider,
                    agent_id=self.agent_id,
                    delta_bdt=-_D(amount),
                )
                forecast = self._liquidity.update(
                    position_id=provider,
                    balance_bdt=float(balance.balance_bdt),
                    delta_bdt=-amount,
                    at=tick.sim_time,
                    interval_hint_seconds=float(
                        tick.payload.get("interval_seconds", 60.0)
                    ),
                )
                self._record_forecast_metric(forecast, tick.sim_time)
                forecast_payload = await self._forecast_payload(forecast, tick.sim_time)
            alert_token = None
            if forecast.status in {"critical", "exhausted"}:
                alert_token = await self._raise_advisory_once(
                    key=f"liquidity:{provider}",
                    provider_id=provider,
                    severity="high",
                    sim_time=tick.sim_time,
                    reason=(
                        f"Provider liquidity pressure requires review: {provider} "
                        f"balance BDT {forecast.balance_bdt:.2f}, EWMA drain "
                        f"BDT {forecast.ewma_drain_bdt_per_min:.2f}/min, "
                        f"estimated TTE {forecast.predicted_tte_min} min. "
                        "Owner: provider operations. Safe next step: contact the "
                        "agent and verify approved support options; no automatic transfer."
                    ),
                )
            return {
                "event_type": "provider_drain",
                "provider_id": provider,
                "amount_bdt": amount,
                "provider_balance": balance.as_dict(),
                "liquidity_forecast": forecast_payload,
                "coordination_alert_token": alert_token,
            }
        if tick.kind == "provider_txn":
            provider = validate_provider_id(str(tick.payload["provider_id"]))
            amount = float(tick.payload["amount_bdt"])
            direction = str(tick.payload["direction"]).lower()
            counterparty_id = str(tick.payload["counterparty_msisdn"])
            transaction_id = uuid.UUID(
                str(tick.payload.get("transaction_id", tick.tick_id))
            )
            if amount <= 0:
                raise ValueError("transaction amount must be positive")
            if direction not in {"in", "out"}:
                raise ValueError("transaction direction must be 'in' or 'out'")

            # One database function commits the physical cash movement, the
            # inverse provider e-money movement, and both audit records.  Its
            # transaction UUID makes retry/replay exact-once.
            async with self._analytics_lock:
                committed = await provider_ledger.apply_customer_transaction(
                    transaction_id=transaction_id,
                    provider_id=provider,
                    agent_id=self.agent_id,
                    counterparty_id=counterparty_id,
                    amount_bdt=_D(amount),
                    direction=direction,
                    sim_time=tick.sim_time,
                    freshness=str(tick.payload.get("data_quality", "fresh")),
                )
                cash_delta = -amount if direction == "out" else amount

                # A replay still produces an explicit successful tick result,
                # but it must not advance online forecasts or count the same
                # observation twice.
                if not committed.applied:
                    return {
                        "event_type": "provider_txn",
                        "transaction_id": str(transaction_id),
                        "provider_id": provider,
                        "counterparty_id": counterparty_id,
                        "amount_bdt": amount,
                        "direction": direction,
                        "synthetic": bool(tick.payload.get("synthetic", True)),
                        "idempotent_replay": True,
                        "shared_cash_balance": float(
                            committed.shared_balance_bdt
                        ),
                        "shared_cash_version": committed.shared_version_id,
                        "provider_balance": committed.provider_balance.as_dict(),
                    }

                forecast = self._liquidity.update(
                    position_id="shared_cash",
                    balance_bdt=float(committed.shared_balance_bdt),
                    delta_bdt=cash_delta,
                    at=tick.sim_time,
                    interval_hint_seconds=float(
                        tick.payload.get("interval_seconds", 1.0)
                    ),
                )
                self._record_forecast_metric(forecast, tick.sim_time)
                provider_delta = amount if direction == "out" else -amount
                provider_forecast = self._liquidity.update(
                    position_id=provider,
                    balance_bdt=float(committed.provider_balance.balance_bdt),
                    delta_bdt=provider_delta,
                    at=tick.sim_time,
                    interval_hint_seconds=float(
                        tick.payload.get("interval_seconds", 60.0)
                    ),
                )
                self._record_forecast_metric(provider_forecast, tick.sim_time)
                historical_context = await self._historical_context(tick.sim_time)
                forecast_payload = HistoricalAnalytics.enrich_forecast(
                    forecast.as_dict(),
                    historical_context.position("shared_cash"),
                    window_days=historical_context.window_days,
                )
                provider_forecast_payload = HistoricalAnalytics.enrich_forecast(
                    provider_forecast.as_dict(),
                    historical_context.position(provider),
                    window_days=historical_context.window_days,
                )
                evaluation = self._anomaly_detector.observe(
                    TransactionObservation(
                        transaction_id=str(
                            tick.payload.get("transaction_id", tick.tick_id)
                        ),
                        observed_at=tick.sim_time,
                        provider_id=provider,
                        account_id=counterparty_id,
                        amount_bdt=amount,
                        direction=direction,
                        is_salary_window=tick.payload.get("is_salary_window"),
                    )
                )
                runtime_metrics.record_anomaly_evaluation(
                    detected=evaluation.triggered,
                    explanation_present=bool(
                        evaluation.evidence.score_components
                        and evaluation.possible_benign_explanations
                    ),
                )
            anomaly_alert_token = None
            if evaluation.triggered:
                evidence = evaluation.evidence
                anomaly_alert_token = await self._raise_advisory_once(
                    key=f"anomaly:{provider}:{evaluation.category}",
                    provider_id=provider,
                    severity=evaluation.severity,
                    sim_time=tick.sim_time,
                    reason=(
                        "Unusual activity requires human review: "
                        f"{evidence.dominant_repeated_amount_frequency} transactions "
                        f"at BDT {evidence.dominant_repeated_amount_bdt} across "
                        f"{evidence.dominant_amount_distinct_account_count} synthetic "
                        f"accounts in {evidence.window_minutes:.0f} minutes; risk score "
                        f"{evaluation.risk_score:.2f}. Owner: provider risk reviewer. "
                        "Safe next step: compare with Eid demand and data quality, then "
                        "record a human decision; do not block or accuse automatically."
                    ),
                )
            return {
                "event_type": "provider_txn",
                "transaction_id": str(
                    tick.payload.get("transaction_id", tick.tick_id)
                ),
                "provider_id": provider,
                "counterparty_id": counterparty_id,
                "amount_bdt": amount,
                "direction": direction,
                "synthetic": bool(tick.payload.get("synthetic", True)),
                "idempotent_replay": False,
                "shared_cash_balance": float(committed.shared_balance_bdt),
                "shared_cash_version": committed.shared_version_id,
                "provider_balance": committed.provider_balance.as_dict(),
                "liquidity_forecast": forecast_payload,
                "provider_liquidity_forecast": provider_forecast_payload,
                "anomaly_detection": evaluation.to_dict(),
                "coordination_alert_token": anomaly_alert_token,
            }
        if tick.kind == "advisory_tte":
            raise ValueError(
                "advisory_tte injection is disabled; forecasts are computed from ledger deltas"
            )
        if tick.kind == "coordination_awaiting":
            # Mirror of FSM state; surfaced to the UI as a card.
            return {
                "event_type": "coordination_awaiting",
                "alert_token": tick.payload.get("alert_token"),
                "status": tick.payload.get("status"),
                "severity": tick.payload.get("severity"),
                "provider_id": tick.payload.get("provider_id"),
            }
        if tick.kind == "noop":
            return {"noop": True}
        raise ValueError(f"unknown tick kind: {tick.kind}")

    async def operational_snapshot(self) -> dict[str, Any]:
        """Read a provider-separated balance snapshot for initial UI hydration."""
        shared_balance, shared_version = await shared_cash_ledger.get_balance(
            self.agent_id
        )
        provider_positions = await provider_ledger.snapshot(agent_id=self.agent_id)
        historical_context = await self._historical_context(self.sim_time)
        return {
            "agent_id": str(self.agent_id),
            "sim_time": self.sim_time.isoformat(),
            "shared_cash_balance": float(shared_balance),
            "shared_cash_version": shared_version,
            "provider_balances": {
                provider: float(position["balance_bdt"])
                for provider, position in provider_positions.items()
            },
            "provider_positions": provider_positions,
            "historical_analytics": historical_context.as_dict(),
        }

    async def _historical_context(self, at: datetime) -> HistoricalContext:
        """Load context without allowing analytics failure to stop live EWMA."""
        try:
            context = await self._historical.context(
                agent_id=self.agent_id,
                as_of=at,
            )
            self._historical_warning_logged = False
            return context
        except Exception:
            if not self._historical_warning_logged:
                log.exception(
                    "historical analytics unavailable; continuing with live EWMA only"
                )
                self._historical_warning_logged = True
            return HistoricalContext(
                window_days=self._historical.window_days,
                as_of=at,
                positions={},
            )

    async def _forecast_payload(self, forecast, at: datetime) -> dict[str, Any]:
        context = await self._historical_context(at)
        return HistoricalAnalytics.enrich_forecast(
            forecast.as_dict(),
            context.position(forecast.position_id),
            window_days=context.window_days,
        )

    async def _raise_advisory_once(
        self,
        *,
        key: str,
        provider_id: str | None,
        severity: str,
        sim_time: datetime,
        reason: str,
    ) -> str:
        """Create one durable case for a continuing analytical condition.

        The in-memory map is only a fast association between an analytical
        condition and its latest durable coordination token.  The database is
        still authoritative for lifecycle state: a PENDING or ACKNOWLEDGED
        case is reused, while a RESOLVED (or deleted) case is evicted so a
        later recurrence opens a fresh PENDING case.

        The indexed status lookup happens only when an advisory-worthy
        condition recurs and already has a cached token; ordinary telemetry
        events and first occurrences perform no extra read.
        """
        async with self._coordination_lock:
            existing = self._active_advisories.get(key)
            if existing is not None:
                status = await self._advisory_status(existing)
                if status in {"PENDING", "ACKNOWLEDGED", "ESCALATED"}:
                    return existing
                # RESOLVED is terminal.  A missing row is also a stale cache
                # entry and must not suppress a new, durable case.
                self._active_advisories.pop(key, None)
            token = await coordination_fsm.raise_alert(
                agent_id=self.agent_id,
                provider_id=provider_id,
                severity=severity,
                reason=reason,
                sim_time=sim_time,
            )
            value = str(token.alert_token)
            self._active_advisories[key] = value
            return value

    @staticmethod
    async def _advisory_status(alert_token: str) -> str | None:
        """Return the durable lifecycle status for a cached advisory token."""
        async with session_scope() as s:
            result = await s.execute(
                text(
                    "SELECT status FROM shared.coordination_alerts "
                    "WHERE alert_token = :alert_token"
                ),
                {"alert_token": uuid.UUID(alert_token)},
            )
            status = result.scalar_one_or_none()
        return str(status) if status is not None else None

    def _record_forecast_metric(self, forecast, observed_at: datetime) -> None:
        shortage_expected = forecast.status == "warning" or (
            forecast.status in {"critical", "exhausted"}
            and forecast.position_id not in self._observed_metric_shortages
        )
        runtime_metrics.record_forecast(
            position_id=forecast.position_id,
            observed_at=observed_at,
            shortage_expected=shortage_expected,
        )
        if (
            forecast.status in {"critical", "exhausted"}
            and forecast.position_id not in self._observed_metric_shortages
        ):
            runtime_metrics.record_shortage_observed(
                position_id=forecast.position_id,
                observed_at=observed_at,
            )
            self._observed_metric_shortages.add(forecast.position_id)
        elif forecast.status in {"healthy", "stable_or_replenishing"}:
            self._observed_metric_shortages.discard(forecast.position_id)

    # ---------------- wall-clock pump ------------------------------------

    async def _pump_loop(self) -> None:
        """Advance time and generate one deterministic customer transaction.

        The old loop emitted only ``noop`` ticks, so the clock moved while no
        ledger command could possibly run.  This generator deliberately uses
        small, auditable movements and the same atomic transaction path as the
        scenarios, keeping provider and shared balances inverse and separate.
        """
        # One wall-clock second advances the model by SPEED_MULTIPLIER
        # simulated seconds. The previous 1/60-second interval accidentally
        # compounded both factors and ran at 3,600x.
        interval = 1.0
        while not self._stop_event.is_set():
            await self._paused.wait()
            await asyncio.sleep(interval)
            self._advance_clock()
            index = self._generated_transaction_count
            self._generated_transaction_count += 1
            provider = ("bkash", "nagad", "rocket")[index % 3]
            # Mostly cash-outs create a visible shared-cash drain; every
            # fourth event is a cash-in so provider ledgers also show drains.
            direction = "in" if index % 4 == 3 else "out"
            amount = float(100 + (index % 5) * 25)
            transaction_id = uuid.uuid5(
                uuid.UUID("fa23029d-d2bd-4f23-a8a4-d51c91d23bb1"),
                f"{self.agent_id}:{self._sim_time.isoformat()}:{index}",
            )
            await self.enqueue_tick(Tick(
                tick_id=str(transaction_id),
                sim_time=self._sim_time,
                kind="provider_txn",
                payload={
                    "transaction_id": str(transaction_id),
                    "provider_id": provider,
                    "counterparty_msisdn": f"SYN-LIVE-{index % 24:04d}",
                    "amount_bdt": amount,
                    "direction": direction,
                    "interval_seconds": SPEED_MULTIPLIER,
                    "synthetic": True,
                    "data_quality": "fresh",
                    "reason": "background_demand_generator",
                },
            ))

    # ---------------- durability (replay ledger) -------------------------

    async def _persist_event(
        self,
        tick: Tick,
        *,
        status: str,
        extra: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Append-only write to shared.simulation_events.

        Subscribers that fall behind can re-fetch via ``/telemetry/events``;
        the correlated status rows remain available if the SSE socket drops.
        """
        payload = {
            "tick_id": tick.tick_id,
            "status": status,
            "kind": tick.kind,
            "payload": tick.payload,
            "retries": tick.retries,
        }
        if extra:
            payload["result"] = extra
        if error:
            payload["error"] = error
        async with session_scope() as s:
            await s.execute(
                text(
                    """
                    INSERT INTO shared.simulation_events
                        (sim_time, event_type, agent_id, provider_id, payload)
                    VALUES (:t, :e, :a, :p, CAST(:payload AS jsonb))
                    """
                ),
                {
                    "t": tick.sim_time,
                    "e": f"tick.{status}",
                    "a": self.agent_id,
                    "p": tick.payload.get("provider_id"),
                    "payload": _json(payload),
                },
            )
        # Fan-out to live SSE subscribers (and to the front-end).
        await broadcaster.broadcast(
            sim_time=tick.sim_time,
            event_type=f"tick.{status}",
            payload={
                "tick_id": tick.tick_id,
                "kind": tick.kind,
                "status": status,
                "tick_payload": tick.payload,
                "result": extra,
                "error": error,
            },
        )

    # ---------------- inspector API --------------------------------------

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def is_running(self) -> bool:
        return bool(self._workers) and all(
            not worker.done() for worker in self._workers
        ) and self._pump is not None and not self._pump.done()

    @property
    def dead_letter_count(self) -> int:
        """In-process counter; the durable rows live in shared.dead_letter_logs."""
        return self._dead_letter_count

    async def durable_dead_letter_count(self) -> int:
        """Return the authoritative dead-letter total across process restarts."""
        async with session_scope() as session:
            count = (
                await session.execute(
                    text("SELECT count(*) FROM shared.dead_letter_logs")
                )
            ).scalar_one()
        return int(count)

    @property
    def dead_letter(self) -> list[Tick]:
        """Backwards-compat: now empty (durable state lives in Postgres)."""
        return []

    async def _persist_dead_letter(self, tick: Tick, error: str) -> None:
        """Durable parking. Replaces the old in-memory list."""
        async with session_scope() as s:
            await s.execute(
                text(
                    """
                    INSERT INTO shared.dead_letter_logs
                        (tick_id, agent_id, provider_id, sim_time,
                         kind, payload, retries, last_error)
                    VALUES (:tid, :a, :p, :t, :k,
                            CAST(:payload AS jsonb), :r, :e)
                    """
                ),
                {
                    "tid": tick.tick_id,
                    "a": self.agent_id,
                    "p": tick.payload.get("provider_id"),
                    "t": tick.sim_time,
                    "k": tick.kind,
                    "payload": _json({
                        "payload": tick.payload,
                        "retries": tick.retries,
                    }),
                    "r": tick.retries,
                    "e": error,
                },
            )
        self._dead_letter_count += 1


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _D(x: float):
    from decimal import Decimal
    return Decimal(str(x))


def _json(obj: Any) -> str:
    import json
    return json.dumps(obj, default=str, separators=(",", ":"))


# ----------------------------------------------------------------------
# Module-level default engine (one per process; bind on app startup).
# ----------------------------------------------------------------------
_default_engine: SimulationEngine | None = None


def get_engine() -> SimulationEngine:
    if _default_engine is None:
        raise RuntimeError("SimulationEngine not initialised. "
                           "Call init_default_engine(agent_id) at app startup.")
    return _default_engine


def init_default_engine(agent_id: uuid.UUID, *, seed: int = DEFAULT_SEED) -> SimulationEngine:
    global _default_engine
    _default_engine = SimulationEngine(agent_id=agent_id, seed=seed)
    return _default_engine
