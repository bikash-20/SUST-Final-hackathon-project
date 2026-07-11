"""Scenario B — festival traffic with an embedded behavioural pattern.

All generated transactions have the same schema and carry no stream name,
anomaly flag, or expected detector outcome.  A repeated-amount, multi-account
pattern is mixed into ordinary festival traffic, then the combined sequence is
sorted by event time before enqueueing.  Detection therefore has to come from
transaction behaviour rather than scenario metadata.
"""
from __future__ import annotations

import logging
import random
import uuid
from datetime import timedelta
from typing import Any, Final

from app.simulation.simulation_engine import Tick

log = logging.getLogger(__name__)

WINDOW_MINUTES: Final = 12
WINDOW_SECONDS: Final = WINDOW_MINUTES * 60
PROVIDERS: Final = ("bkash", "nagad", "rocket")
REPEATED_AMOUNT_BDT: Final = 4999.0
REPEATED_CADENCE_SECONDS: Final = 60
_NAMESPACE: Final = uuid.UUID("10f26ca0-005b-4d70-84cd-a0e0357db18c")


def _stable_uuid(seed: int, purpose: str, index: int) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{seed}:{purpose}:{index}"))


def _account_pool(size: int = 120) -> tuple[str, ...]:
    # Deliberately not phone-number shaped: these IDs cannot be mistaken for PII.
    return tuple(f"SYN-ACC-{index:04d}" for index in range(1, size + 1))


def _ordinary_amount(rng: random.Random) -> float:
    """Generate realistic festival traffic with some benign price repetition."""
    if rng.random() < 0.22:
        return float(rng.choice((500, 750, 1000, 1500, 2000, 3000, 5000)))
    # Retail amounts generally land on 10-BDT boundaries but retain variance.
    return float(rng.randrange(20, 951) * 10)


async def run(engine, params: dict[str, Any]) -> dict[str, Any]:
    seed = int(params.get("seed", int(engine.sim_time.timestamp()) ^ 0xB))
    rng = random.Random(seed)
    ordinary_count = max(0, int(params.get("ordinary_transactions", 90)))
    repeated_count = max(0, int(params.get("repeated_amount_transactions", 12)))
    pattern_provider = str(params.get("pattern_provider", "bkash")).strip().lower()
    whitelisted_provider_raw = params.get("whitelisted_provider")
    whitelisted_provider = (
        str(whitelisted_provider_raw).strip().lower()
        if whitelisted_provider_raw is not None
        else None
    )
    if pattern_provider not in PROVIDERS:
        raise ValueError(f"pattern_provider must be one of {PROVIDERS}")
    if whitelisted_provider is not None and whitelisted_provider not in PROVIDERS:
        raise ValueError(f"whitelisted_provider must be one of {PROVIDERS}")

    # Plan directions from the live balances without using future labels.  A
    # conservative debit budget does not count credits from earlier planned
    # events, so the stream remains valid even when multiple workers commit
    # adjacent ticks in a different order.
    snapshot = await engine.operational_snapshot()
    remaining_cash_out = float(snapshot["shared_cash_balance"])
    provider_snapshot = snapshot["provider_balances"]
    remaining_provider_outflow = {
        provider: float(provider_snapshot[provider]) for provider in PROVIDERS
    }

    accounts = _account_pool()
    clustered_accounts = rng.sample(list(accounts), k=4)
    sim_start = engine.sim_time
    scheduled: list[Tick] = []

    def transaction_tick(
        *,
        index: int,
        seconds_after_start: int,
        provider_id: str,
        account_id: str,
        amount_bdt: float,
        direction: str,
    ) -> Tick:
        # Include the event-time origin in the run identity. Replaying the
        # exact same run stays idempotent, while intentionally launching the
        # same seed later produces a new set of legitimate transactions.
        run_identity = sim_start.isoformat()
        transaction_id = _stable_uuid(
            seed, f"{run_identity}:transaction", index
        )
        return Tick(
            tick_id=_stable_uuid(seed, f"{run_identity}:tick", index),
            sim_time=sim_start + timedelta(seconds=seconds_after_start),
            kind="provider_txn",
            payload={
                "transaction_id": transaction_id,
                "provider_id": provider_id,
                "counterparty_msisdn": account_id,
                "amount_bdt": amount_bdt,
                "direction": direction,
                "synthetic": True,
                "data_quality": "fresh",
                "provider_whitelisted": provider_id == whitelisted_provider,
            },
        )

    for index in range(ordinary_count):
        provider = rng.choice(PROVIDERS)
        scheduled.append(
            transaction_tick(
                index=index,
                seconds_after_start=rng.randrange(WINDOW_SECONDS),
                provider_id=provider,
                account_id=rng.choice(accounts),
                amount_bdt=_ordinary_amount(rng),
                direction="out" if rng.random() < 0.68 else "in",
            )
        )

    # A slight deterministic jitter avoids a cartoonishly perfect test signal
    # while retaining the cadence feature a real detector should discover.
    cadence_jitter = (-3, 2, 0, 4, -2, 1)
    for offset in range(repeated_count):
        scheduled.append(
            transaction_tick(
                index=ordinary_count + offset,
                seconds_after_start=(
                    offset * REPEATED_CADENCE_SECONDS
                    + cadence_jitter[offset % len(cadence_jitter)]
                    + 4
                ),
                provider_id=pattern_provider,
                account_id=clustered_accounts[offset % len(clustered_accounts)],
                amount_bdt=REPEATED_AMOUNT_BDT,
                direction="out",
            )
        )

    scheduled.sort(key=lambda tick: (tick.sim_time, tick.tick_id))
    for tick in scheduled:
        provider = str(tick.payload["provider_id"])
        amount = float(tick.payload["amount_bdt"])
        preferred = str(tick.payload["direction"])
        can_cash_out = remaining_cash_out >= amount
        can_provider_out = remaining_provider_outflow[provider] >= amount

        if preferred == "out" and can_cash_out:
            direction = "out"
        elif preferred == "in" and can_provider_out:
            direction = "in"
        elif can_cash_out:
            direction = "out"
        elif can_provider_out:
            direction = "in"
        else:
            raise RuntimeError(
                "scenario B volume exceeds the live cash and provider debit budgets"
            )

        tick.payload["direction"] = direction
        if direction == "out":
            remaining_cash_out -= amount
        else:
            remaining_provider_outflow[provider] -= amount

    for tick in scheduled:
        await engine.enqueue_tick(tick)

    log.info(
        "scenario_B enqueued=%d providers=%s duration_min=%d synthetic=true",
        len(scheduled),
        PROVIDERS,
        WINDOW_MINUTES,
    )
    return {
        "enqueued": len(scheduled),
        "providers": list(PROVIDERS),
        "duration_minutes": WINDOW_MINUTES,
        "synthetic": True,
        "scenario": "B",
    }
