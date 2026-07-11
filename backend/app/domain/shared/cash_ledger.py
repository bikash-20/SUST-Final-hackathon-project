"""Aggregate root for the *shared* physical cash drawer.

This is the only place in the system allowed to mutate the
``shared.shared_cash_ledger`` row. It enforces OPTIMISTIC LOCKING via the
``version_id`` column to defeat the race condition the user flagged:

    Two parallel simulated streams attempt to deduct/credit physical
    cash simultaneously; the database must enforce isolation levels to
    prevent dirty or phantom writes.

Strategy
--------
1. The whole operation runs inside a ``REPEATABLE READ`` transaction
   (see ``database.py``).
2. We read the current ``balance_bdt`` and ``version_id``.
3. We perform the business mutation in memory.
4. We issue a guarded UPDATE::

       UPDATE shared.shared_cash_ledger
          SET balance_bdt = :new_balance,
              version_id   = :expected_version + 1,
              updated_at   = now()
        WHERE agent_id    = :agent_id
          AND version_id  = :expected_version

   If two writers race, exactly one wins. The loser's UPDATE affects
   zero rows, which we treat as ``VersionConflict``.
5. We retry up to ``MAX_RETRIES`` with exponential backoff. If we still
   lose we surface the conflict so the simulation ticker can re-queue
   the tick (it is *not* dropped — see ``simulation_engine.py``).
"""
from __future__ import annotations

import asyncio
import logging
import random
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import session_scope

log = logging.getLogger(__name__)

MAX_RETRIES: Final = 5
BACKOFF_MIN_S: Final = 0.01   # 10 ms
BACKOFF_MAX_S: Final = 0.05   # 50 ms
# Non-blocking jittered backoff: random uniform in [BACKOFF_MIN_S, BACKOFF_MAX_S]
# defeats lock starvation under the 60x multi-provider load by spreading
# concurrent retry attempts across a non-deterministic window.


class VersionConflict(RuntimeError):
    """Raised when the optimistic-lock UPDATE finds zero matching rows."""


class InsufficientCash(RuntimeError):
    """Raised when a deduction would drive ``balance_bdt`` below zero."""


@dataclass(frozen=True, slots=True)
class CashMovementResult:
    agent_id: uuid.UUID
    new_balance: Decimal
    version_id: int
    sim_time: str  # ISO-8601 string the caller (ticker) supplies


class SharedCashLedger:
    """The single aggregate root for the physical cash drawer.

    All public methods are coroutines; they manage their own session so
    callers cannot accidentally share one across the 60x ticker streams.
    """

    # ---------------- queries (read-only) -------------------------------

    async def get_balance(self, agent_id: uuid.UUID) -> tuple[Decimal, int]:
        async with session_scope() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT balance_bdt, version_id "
                        "FROM shared.shared_cash_ledger WHERE agent_id = :a"
                    ),
                    {"a": agent_id},
                )
            ).first()
            if row is None:
                raise LookupError(f"No cash ledger row for agent {agent_id}")
            return Decimal(row.balance_bdt), int(row.version_id)

    # ---------------- commands (mutations with optimistic lock) ----------

    async def deduct(
        self,
        agent_id: uuid.UUID,
        amount_bdt: Decimal,
        *,
        reason: str,
        sim_time,
    ) -> CashMovementResult:
        """Atomically subtract ``amount_bdt`` from the shared drawer."""
        if amount_bdt <= 0:
            raise ValueError("deduct amount must be > 0")
        return await self._mutate(
            agent_id,
            delta=-amount_bdt,
            reason=reason,
            sim_time=sim_time,
        )

    async def credit(
        self,
        agent_id: uuid.UUID,
        amount_bdt: Decimal,
        *,
        reason: str,
        sim_time,
    ) -> CashMovementResult:
        if amount_bdt <= 0:
            raise ValueError("credit amount must be > 0")
        return await self._mutate(
            agent_id,
            delta=amount_bdt,
            reason=reason,
            sim_time=sim_time,
        )

    # ---------------- core ------------------------------------------------

    async def _mutate(
        self,
        agent_id: uuid.UUID,
        *,
        delta: Decimal,
        reason: str,
        sim_time,
    ) -> CashMovementResult:
        """Optimistic-locked mutation. Retries on VersionConflict."""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return await self._attempt(agent_id, delta, reason, sim_time)
            except VersionConflict:
                # Non-blocking jittered backoff: random uniform in
                # [0.01, 0.05] seconds. Eliminates the fixed-wait hot
                # spot that causes lock starvation when the 60x ticker
                # fires multi-provider bursts in parallel.
                sleep_s = random.uniform(BACKOFF_MIN_S, BACKOFF_MAX_S)
                log.warning(
                    "cash_ledger version conflict agent=%s attempt=%d sleeping=%.3fs",
                    agent_id, attempt, sleep_s,
                )
                await asyncio.sleep(sleep_s)
        raise VersionConflict(
            f"cash_ledger could not converge after {MAX_RETRIES} attempts for {agent_id}"
        )

    async def _attempt(
        self,
        agent_id: uuid.UUID,
        delta: Decimal,
        reason: str,
        sim_time,
    ) -> CashMovementResult:
        async with session_scope() as s:  # REPEATABLE READ tx
            row = (
                await s.execute(
                    text(
                        "SELECT balance_bdt, version_id "
                        "FROM shared.shared_cash_ledger WHERE agent_id = :a"
                    ),
                    {"a": agent_id},
                )
            ).first()
            if row is None:
                raise LookupError(f"No cash ledger row for agent {agent_id}")

            current = Decimal(row.balance_bdt)
            expected_version = int(row.version_id)
            new_balance = current + delta

            if new_balance < 0:
                raise InsufficientCash(
                    f"agent={agent_id} balance={current} delta={delta} would go negative"
                )

            res = await s.execute(
                text(
                    """
                    UPDATE shared.shared_cash_ledger
                       SET balance_bdt = :new_balance,
                           version_id   = :new_version,
                           updated_at   = now()
                     WHERE agent_id    = :a
                       AND version_id  = :expected_version
                    """
                ),
                {
                    "a": agent_id,
                    "new_balance": new_balance,
                    "expected_version": expected_version,
                    "new_version": expected_version + 1,
                },
            )
            # rowcount == 0 → another transaction won the race.
            if res.rowcount != 1:
                raise VersionConflict(
                    f"agent={agent_id} expected version {expected_version} lost the race"
                )

            await s.execute(
                text(
                    """
                    INSERT INTO shared.shared_cash_movement
                        (agent_id, delta_bdt, reason, sim_time, version_after)
                    VALUES (:a, :d, :r, :t, :v)
                    """
                ),
                {
                    "a": agent_id,
                    "d": delta,
                    "r": reason,
                    "t": sim_time,
                    "v": expected_version + 1,
                },
            )

            return CashMovementResult(
                agent_id=agent_id,
                new_balance=new_balance,
                version_id=expected_version + 1,
                sim_time=sim_time.isoformat() if hasattr(sim_time, "isoformat") else str(sim_time),
            )


# Module-level singleton — the aggregate root is stateless beyond config.
shared_cash_ledger = SharedCashLedger()