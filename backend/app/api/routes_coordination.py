"""Coordination router — alert FSM transitions + inspection."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.domain.coordination.state_machine import (
    InvalidTransition,
    coordination_fsm,
)
from app.infrastructure.database import session_scope
from app.simulation.simulation_engine import get_engine

router = APIRouter()

Status = Literal["PENDING", "ACKNOWLEDGED", "ESCALATED", "RESOLVED"]


class TransitIn(BaseModel):
    alert_token: uuid.UUID
    to: Status
    actor: str = "unknown"
    reason: str = ""


@router.post("/transit")
async def transit(body: TransitIn):
    """Drive the alert FSM. Mirrors every transition into
    ``shared.simulation_events`` JSONB payload for replay/audit.
    """
    try:
        token = await coordination_fsm.transit(
            alert_token=body.alert_token,
            to=body.to,
            actor=body.actor,
            reason=body.reason,
            sim_time=get_engine().sim_time,
        )
    except InvalidTransition as e:
        raise HTTPException(409, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))
    return {
        "alert_token": str(token.alert_token),
        "status": token.status,
        "severity": token.severity,
        "provider_id": token.provider_id,
        "sim_time": token.sim_time.isoformat(),
        "transitions": token.transitions,
    }


@router.get("/alerts")
async def list_alerts(
    status: Status | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    async with session_scope() as s:
        if status is None:
            rows = (await s.execute(
                text(
                    "SELECT alert_token, status, severity, provider_id, "
                    "       sim_time, transitions "
                    "FROM shared.coordination_alerts "
                    "ORDER BY sim_time DESC LIMIT :lim"
                ),
                {"lim": limit},
            )).mappings().all()
        else:
            rows = (await s.execute(
                text(
                    "SELECT alert_token, status, severity, provider_id, "
                    "       sim_time, transitions "
                    "FROM shared.coordination_alerts "
                    "WHERE status = :st "
                    "ORDER BY sim_time DESC LIMIT :lim"
                ),
                {"st": status, "lim": limit},
            )).mappings().all()
    return [
        {
            "alert_token": str(r["alert_token"]),
            "status": r["status"],
            "severity": r["severity"],
            "provider_id": r["provider_id"],
            "sim_time": r["sim_time"].isoformat(),
            "transitions": r["transitions"],
        }
        for r in rows
    ]


@router.get("/dead_letter")
async def list_dead_letter(
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Inspect durable dead-letter rows (replaces in-memory list)."""
    async with session_scope() as s:
        rows = (await s.execute(
            text(
                "SELECT tick_id, agent_id, provider_id, sim_time, kind, "
                "       payload, retries, last_error, created_at "
                "FROM shared.dead_letter_logs "
                "ORDER BY sim_time DESC LIMIT :lim"
            ),
            {"lim": limit},
        )).mappings().all()
    return [dict(r) for r in rows]
