"""Simulation control router (start/stop/pause/resume/scenario)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from app.simulation.simulation_engine import SimulationEngine, Tick, get_engine
from app.infrastructure.database import validate_provider_id
from app.simulation.scenarios import (
    scenario_a, scenario_b, scenario_d,
)
from app.simulation.injection import (
    InsufficientSharedCashForBurst,
    inject_transactions,
)

router = APIRouter()


class ScenarioIn(BaseModel):
    scenario: Literal["A", "B", "C", "D"]
    params: dict = Field(default_factory=dict)


class TickIn(BaseModel):
    kind: Literal["cash_out", "cash_in", "inconsistency", "noop"]
    payload: dict = Field(default_factory=dict)


class InjectIn(BaseModel):
    provider: Literal["bkash", "nagad", "rocket"]
    number_of_transactions: int = Field(default=12, ge=1, le=200)
    amount_bdt: float | None = Field(default=None, gt=0)
    min_amount_bdt: float | None = Field(default=None, gt=0)
    max_amount_bdt: float | None = Field(default=None, gt=0)
    amount_pattern: Literal["varied", "near_identical"] = "near_identical"
    distinct_accounts: int = Field(default=4, ge=1, le=200)
    window_seconds: int = Field(default=30, ge=1, le=3600)
    is_salary_window: bool = False

    @model_validator(mode="after")
    def validate_amounts(self):
        if self.amount_bdt is not None:
            if self.min_amount_bdt is not None or self.max_amount_bdt is not None:
                raise ValueError("use amount_bdt or min/max range, not both")
            return self
        if self.min_amount_bdt is None or self.max_amount_bdt is None:
            raise ValueError("provide amount_bdt or both min_amount_bdt and max_amount_bdt")
        if self.max_amount_bdt < self.min_amount_bdt:
            raise ValueError("max_amount_bdt must be >= min_amount_bdt")
        if self.amount_pattern == "varied" and self.max_amount_bdt == self.min_amount_bdt:
            raise ValueError("varied amounts require a non-zero amount range")
        return self


def _engine() -> SimulationEngine:
    return get_engine()


@router.post("/control/start")
async def start():
    await _engine().start()
    return {"running": True}


@router.post("/control/stop")
async def stop():
    await _engine().stop()
    return {"running": False}


@router.post("/control/pause")
async def pause():
    _engine().pause()
    return {"paused": True}


@router.post("/control/resume")
async def resume():
    _engine().resume()
    return {"paused": False}


@router.get("/control/state")
async def state():
    e = _engine()
    return {
        "sim_time": e.sim_time.isoformat(),
        "queue_depth": e.queue_depth,
        "dead_letter_size": await e.durable_dead_letter_count(),
        "paused": not e._paused.is_set(),
    }


@router.post("/scenario")
async def fire_scenario(body: ScenarioIn):
    """Schedule a scenario. Each scenario is a *pure function* of (engine, params)."""
    if body.scenario == "A":
        return await scenario_a.run(_engine(), body.params)
    if body.scenario == "B":
        return await scenario_b.run(_engine(), body.params)
    if body.scenario == "C":
        # Strict Phase-2 fallback: telemetry-only. Does NOT mutate cash ledger.
        provider_id = validate_provider_id(
            str(body.params.get("provider_id", "nagad"))
        )
        await _engine().enqueue_tick(Tick(
            tick_id=str(uuid.uuid4()),
            sim_time=_engine().sim_time,
            kind="inconsistency",
            payload={
                "provider_id": provider_id,
                "confidence_score": 0.42,   # < 0.5 triggers frontend degraded layout
                "stale_after_s": body.params.get("stale_after_s", 90),
            },
        ))
        return {"queued": True, "scenario": "C", "telemetry_only": True}
    if body.scenario == "D":
        if body.params.get("provider_id") is not None:
            body.params["provider_id"] = validate_provider_id(
                str(body.params["provider_id"])
            )
        return await scenario_d.run(_engine(), body.params)
    raise HTTPException(400, f"Unknown scenario: {body.scenario}")


@router.post("/tick")
async def push_tick(body: TickIn):
    await _engine().enqueue_tick(Tick(
        tick_id=str(uuid.uuid4()),
        sim_time=_engine().sim_time,
        kind=body.kind,
        payload=body.payload,
    ))
    return {"queued": True}


@router.post("/inject")
async def inject(body: InjectIn):
    """Inject an operator-defined synthetic burst through normal provider ticks."""
    provider = validate_provider_id(body.provider)
    minimum = body.amount_bdt if body.amount_bdt is not None else body.min_amount_bdt
    maximum = body.amount_bdt if body.amount_bdt is not None else body.max_amount_bdt
    assert minimum is not None and maximum is not None
    if body.distinct_accounts > body.number_of_transactions:
        raise HTTPException(422, "distinct_accounts cannot exceed number_of_transactions")
    try:
        return await inject_transactions(
            _engine(),
            provider=provider,
            number_of_transactions=body.number_of_transactions,
            minimum_amount_bdt=minimum,
            maximum_amount_bdt=maximum,
            amount_pattern=body.amount_pattern,
            distinct_accounts=body.distinct_accounts,
            window_seconds=body.window_seconds,
            is_salary_window=body.is_salary_window,
        )
    except InsufficientSharedCashForBurst as exc:
        return JSONResponse(status_code=409, content=exc.as_dict())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
