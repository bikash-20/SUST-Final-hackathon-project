"""Scenario A — Hidden Shortage.

Each event represents a customer cash-in service: the shop receives physical
cash while the selected provider e-money position is depleted.  The engine
commits both movements and derives an EWMA time-to-exhaustion forecast from
the resulting provider deltas.  No forecast value is injected by this file.
"""
from __future__ import annotations

import logging
import random
import uuid
from datetime import timedelta
from typing import Any

from app.infrastructure.database import validate_provider_id
from app.simulation.simulation_engine import Tick

log = logging.getLogger(__name__)

async def run(engine, params: dict[str, Any]) -> dict[str, Any]:
    """Hidden shortage injector. Pure function of (engine, params).

    params:
        n_bursts         : int   (default 18)
        drain_per_burst  : float BDT (default 1250)
        cash_in_per_step : float BDT (default 1000)
        providers        : list[str]  (default ['nagad'])
        interval_seconds : int   simulated spacing (default 60)
    """
    rng = random.Random(int(engine.sim_time.timestamp()) ^ 0xA)
    n_bursts = int(params.get("n_bursts", 18))
    drain_per_burst = float(params.get("drain_per_burst", 1250.0))
    cash_in_per_step = float(params.get("cash_in_per_step", 1000.0))
    interval_seconds = int(params.get("interval_seconds", 60))
    raw_providers = list(params.get("providers", ["nagad"]))
    providers = [validate_provider_id(str(provider)) for provider in raw_providers]
    if not 3 <= n_bursts <= 120:
        raise ValueError("n_bursts must be between 3 and 120")
    if drain_per_burst <= 0 or cash_in_per_step <= 0:
        raise ValueError("scenario amounts must be positive")
    if not 10 <= interval_seconds <= 600:
        raise ValueError("interval_seconds must be between 10 and 600")

    enqueued = 0
    # Pin every event to one immutable origin. The engine clock advances while
    # enqueue awaits persistence, so consulting it inside the loop stretches
    # a one-minute series into irregular gaps and corrupts the EWMA slope.
    sim_start = engine.sim_time
    for i in range(n_bursts):
        provider = providers[i % len(providers)]
        event_time = sim_start + timedelta(seconds=i * interval_seconds)
        amount = round(drain_per_burst + rng.uniform(-50, 50), 2)
        await engine.enqueue_tick(Tick(
            tick_id=str(uuid.uuid4()),
            sim_time=event_time,
            kind="provider_drain",
            payload={
                "provider_id": provider,
                "amount_bdt": amount,
                "interval_seconds": interval_seconds * len(providers),
                "scenario": "A",
            },
        ))
        # The agent receives physical cash for the e-money it provides.
        await engine.enqueue_tick(Tick(
            tick_id=str(uuid.uuid4()),
            sim_time=event_time,
            kind="cash_in",
            payload={
                "amount_bdt": cash_in_per_step,
                "interval_seconds": interval_seconds,
                "scenario": "A",
                "reason": "customer_cash_in_service",
            },
        ))
        enqueued += 2

    log.info("scenario_A enqueued=%d providers=%s", enqueued, providers)
    return {"enqueued": enqueued, "providers": providers,
            "scenario": "A", "n_bursts": n_bursts,
            "forecast": "computed_from_committed_provider_deltas"}
