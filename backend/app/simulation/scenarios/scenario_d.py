"""Scenario D — Coordination FSM.

The FSM lives in ``app.domain.coordination.state_machine``. This module
is the deterministic scenario wrapper that:

1. Raises a new alert (kind='coordination_raise') -> state PENDING.
2. Optionally auto-acknowledges it.
3. Optionally completes the demo at ESCALATED or RESOLVED while preserving
   the audited intermediate transition(s).

The alert token is stored both in
``shared.coordination_alerts.transitions`` (canonical) AND mirrored
into the ``shared.simulation_events`` JSONB payload (replay stream).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.domain.coordination.state_machine import coordination_fsm
from app.simulation.simulation_engine import Tick

log = logging.getLogger(__name__)


async def run(engine, params: dict[str, Any]) -> dict[str, Any]:
    severity = str(params.get("severity", "high"))
    provider_id = params.get("provider_id")
    auto_ack = bool(params.get("auto_ack", True))
    final_status = str(params.get("final_status", "ESCALATED")).upper()
    if final_status not in {"ACKNOWLEDGED", "ESCALATED", "RESOLVED"}:
        raise ValueError("final_status must be ACKNOWLEDGED, ESCALATED, or RESOLVED")
    recipient = str(params.get("recipient", "provider operations desk"))
    owner = str(params.get("owner", "operations shift lead"))
    recommended_action = str(
        params.get(
            "recommended_action",
            "contact the agent, verify the evidence, and record the human decision",
        )
    )
    reason = str(
        params.get(
            "reason",
            f"High-priority coordination case. Received by: {recipient}. "
            f"Owner: {owner}. Recommended action: {recommended_action}. "
            "No automatic transfer, blocking, or fund freeze.",
        )
    )

    sim_t: datetime = engine.sim_time
    agent_id = engine.agent_id

    # 1. Raise -> PENDING (mirrored to simulation_events)
    token = await coordination_fsm.raise_alert(
        agent_id=agent_id,
        provider_id=provider_id,
        severity=severity,
        reason=reason,
        sim_time=sim_t,
    )

    enqueued = 0
    # 2. Optionally drive PENDING -> ACKNOWLEDGED via the FSM directly.
    if auto_ack:
        token = await coordination_fsm.transit(
            alert_token=token.alert_token,
            to="ACKNOWLEDGED",
            actor="ops_demo",
            reason="auto_ack for live demo",
            sim_time=sim_t,
        )
    elif final_status == "ACKNOWLEDGED":
        token = await coordination_fsm.transit(
            alert_token=token.alert_token,
            to="ACKNOWLEDGED",
            actor="ops_demo",
            reason="acknowledged for live demo",
            sim_time=sim_t,
        )
    if final_status == "RESOLVED":
        if token.status == "PENDING":
            token = await coordination_fsm.transit(
                alert_token=token.alert_token,
                to="ACKNOWLEDGED",
                actor="ops_demo",
                reason="acknowledged before resolution",
                sim_time=sim_t,
            )
        token = await coordination_fsm.transit(
            alert_token=token.alert_token,
            to="RESOLVED",
            actor="ops_demo",
            reason="resolved after human review",
            sim_time=sim_t,
        )
    elif final_status == "ESCALATED":
        token = await coordination_fsm.transit(
            alert_token=token.alert_token,
            to="ESCALATED",
            actor="ops_demo",
            reason="escalated for provider operations review",
            sim_time=sim_t,
        )
    # 3. Emit an awaiting-resolution tick so the UI shows the live card.
    await engine.enqueue_tick(Tick(
        tick_id=str(uuid.uuid4()),
        sim_time=sim_t,
        kind="coordination_awaiting",
        payload={
            "alert_token": str(token.alert_token),
            "status": token.status,
            "severity": severity,
            "provider_id": provider_id,
        },
    ))
    enqueued += 1

    log.info("scenario_D raised alert_token=%s status=%s",
             token.alert_token, token.status)
    return {
        "scenario": "D",
        "alert_token": str(token.alert_token),
        "status": token.status,
        "severity": severity,
        "recipient": recipient,
        "owner": owner,
        "recommended_action": recommended_action,
        "auto_ack": auto_ack,
        "final_status": token.status,
        "enqueued": enqueued,
    }
