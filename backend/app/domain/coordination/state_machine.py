"""Coordination FSM with acknowledgement, escalation, and resolution.

This is the canonical state machine referenced in
``docs/02_DATA_FLOW_AND_STATE_MACHINES.md``. The FSM token is a row in
``shared.coordination_alerts``; every transition appends an entry to the
``transitions`` JSONB column AND mirrors the event into
``shared.simulation_events.payload`` so the audit trail is queryable
both ways.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final, Literal

from sqlalchemy import text

from app.infrastructure.broadcaster import broadcaster
from app.infrastructure.database import session_scope

log = logging.getLogger(__name__)

Status = Literal["PENDING", "ACKNOWLEDGED", "ESCALATED", "RESOLVED"]
ALLOWED_TRANSITIONS: Final[dict[Status, set[Status]]] = {
    "PENDING":      {"ACKNOWLEDGED", "ESCALATED"},
    "ACKNOWLEDGED": {"RESOLVED", "ESCALATED"},
    "ESCALATED":    {"RESOLVED"},
    "RESOLVED":     set(),
}


class InvalidTransition(RuntimeError):
    """Raised when a transition violates the FSM."""


@dataclass(frozen=True, slots=True)
class AlertToken:
    alert_token: uuid.UUID
    status: Status
    severity: str
    provider_id: str | None
    sim_time: datetime
    transitions: list[dict[str, Any]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(obj: Any) -> str:
    import json
    return json.dumps(obj, default=str, separators=(",", ":"))


class CoordinationFSM:
    """Aggregate root for alert coordination. The only path that mutates
    ``shared.coordination_alerts`` and ``shared.simulation_events`` for
    coordination events.
    """

    async def raise_alert(
        self,
        *,
        agent_id: uuid.UUID | None,
        provider_id: str | None,
        severity: str = "medium",
        reason: str = "auto",
        sim_time: datetime | None = None,
    ) -> AlertToken:
        """Create a new PENDING alert and mirror the event into the
        simulation_events JSONB payload for the audit trail.
        """
        sim_time = sim_time or _now()
        event_payload: dict[str, Any]
        async with session_scope() as s:
            row = (await s.execute(
                text(
                    """
                    INSERT INTO shared.coordination_alerts
                        (agent_id, provider_id, severity, status,
                         transitions, sim_time)
                    VALUES (:a, :p, :sv, 'PENDING',
                            CAST(:tx AS jsonb), :t)
                    RETURNING alert_token, status, severity, provider_id,
                              agent_id, sim_time, transitions
                    """
                ),
                {
                    "a": agent_id,
                    "p": provider_id,
                    "sv": severity,
                    "tx": _json([{
                        "to": "PENDING",
                        "at": sim_time.isoformat(),
                        "by": "system",
                        "reason": reason,
                    }]),
                    "t": sim_time,
                },
            )).mappings().first()

            # Build one canonical representation for both the durable stream
            # row and the post-commit in-process fan-out.
            event_payload = {
                "alert_token": str(row["alert_token"]),
                "status": "PENDING",
                "severity": row["severity"],
                "provider_id": row["provider_id"],
                "agent_id": str(row["agent_id"]) if row["agent_id"] else None,
                "reason": reason,
                "transitions": row["transitions"],
            }
            await s.execute(
                text(
                    """
                    INSERT INTO shared.simulation_events
                        (sim_time, event_type, agent_id, provider_id, payload)
                    VALUES (:t, 'coordination.PENDING', :a, :p,
                            CAST(:payload AS jsonb))
                    """
                ),
                {
                    "t": sim_time,
                    "a": agent_id,
                    "p": provider_id,
                    "payload": _json(event_payload),
                },
            )

        # session_scope commits on clean exit. Never publish uncommitted
        # coordination state to SSE subscribers.
        await self._broadcast_committed(
            sim_time=sim_time,
            status="PENDING",
            payload=event_payload,
        )
        return AlertToken(
            alert_token=row["alert_token"],
            status="PENDING",
            severity=severity,
            provider_id=provider_id,
            sim_time=row["sim_time"],
            transitions=row["transitions"],
        )

    async def transit(
        self,
        *,
        alert_token: uuid.UUID,
        to: Status,
        actor: str,
        reason: str = "",
        sim_time: datetime | None = None,
    ) -> AlertToken:
        """Drive the FSM. Rejects illegal transitions.

        Mirrors every accepted transition into shared.simulation_events
        so the audit trail is reconstructable from either table.
        """
        if to not in {"PENDING", "ACKNOWLEDGED", "ESCALATED", "RESOLVED"}:
            raise InvalidTransition(f"unknown target status: {to}")
        sim_time = sim_time or _now()
        event_payload: dict[str, Any]
        async with session_scope() as s:
            row = (await s.execute(
                text(
                    "SELECT status, transitions, agent_id "
                    "FROM shared.coordination_alerts "
                    "WHERE alert_token = :t FOR UPDATE"
                ),
                {"t": alert_token},
            )).mappings().first()
            if row is None:
                raise LookupError(f"no alert {alert_token}")

            current: Status = row["status"]
            if to not in ALLOWED_TRANSITIONS[current]:
                raise InvalidTransition(
                    f"illegal transition {current} -> {to} for {alert_token}"
                )

            transitions = list(row["transitions"]) + [{
                "from": current,
                "to": to,
                "at": sim_time.isoformat(),
                "by": actor,
                "reason": reason,
            }]

            updated = (await s.execute(
                text(
                    """
                    UPDATE shared.coordination_alerts
                       SET status      = :to,
                           transitions = CAST(:tx AS jsonb),
                           updated_at  = now(),
                           sim_time    = :t
                     WHERE alert_token = :tk
                    RETURNING alert_token, status, severity, provider_id,
                              agent_id, sim_time, transitions
                    """
                ),
                {
                    "to": to,
                    "tx": _json(transitions),
                    "t": sim_time,
                    "tk": alert_token,
                },
            )).mappings().first()

            event_payload = {
                "alert_token": str(alert_token),
                "status": to,
                "from": current,
                "severity": updated["severity"],
                "provider_id": updated["provider_id"],
                "agent_id": (
                    str(updated["agent_id"]) if updated["agent_id"] else None
                ),
                "actor": actor,
                "reason": reason,
                "transitions": transitions,
            }
            await s.execute(
                text(
                    """
                    INSERT INTO shared.simulation_events
                        (sim_time, event_type, agent_id, provider_id, payload)
                    VALUES (:t, :e, :a, :p, CAST(:payload AS jsonb))
                    """
                ),
                {
                    "t": sim_time,
                    "e": f"coordination.{to}",
                    "a": updated["agent_id"],
                    "p": updated["provider_id"],
                    "payload": _json(event_payload),
                },
            )

        await self._broadcast_committed(
            sim_time=sim_time,
            status=to,
            payload=event_payload,
        )
        return AlertToken(
            alert_token=updated["alert_token"],
            status=updated["status"],
            severity=updated["severity"],
            provider_id=updated["provider_id"],
            sim_time=updated["sim_time"],
            transitions=updated["transitions"],
        )

    @staticmethod
    async def _broadcast_committed(
        *,
        sim_time: datetime,
        status: Status,
        payload: dict[str, Any],
    ) -> None:
        """Fan out an already committed coordination transition.

        The database event is the durable source of truth. A transient
        in-process fan-out failure is logged rather than turning a committed
        transition into a misleading HTTP failure that a caller might retry.
        """
        try:
            await broadcaster.broadcast(
                sim_time=sim_time,
                event_type=f"coordination.{status}",
                payload=payload,
            )
        except Exception:
            log.exception(
                "coordination transition committed but live broadcast failed: "
                "alert_token=%s status=%s",
                payload.get("alert_token"),
                status,
            )


# Module-level singleton
coordination_fsm = CoordinationFSM()
