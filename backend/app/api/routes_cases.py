"""Minimal case-note API with one combined coordination timeline."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.infrastructure.broadcaster import broadcaster
from app.infrastructure.database import session_scope
from app.simulation.simulation_engine import get_engine

log = logging.getLogger(__name__)
router = APIRouter()


class CaseNoteIn(BaseModel):
    author_role: str = Field(min_length=1, max_length=80)
    note_text: str = Field(min_length=1, max_length=2_000)


def _note_dict(row: Any) -> dict[str, Any]:
    return {
        "note_id": str(row["note_id"]),
        "case_id": str(row["alert_token"]),
        "author_role": row["author_role"],
        "note_text": row["note_text"],
        "timestamp": row["noted_at"].isoformat(),
    }


async def _load_case(case_id: uuid.UUID) -> tuple[Any, list[dict[str, Any]]]:
    async with session_scope() as session:
        case = (
            await session.execute(
                text(
                    "SELECT alert_token, status, severity, provider_id, transitions "
                    "FROM shared.coordination_alerts WHERE alert_token = :case_id"
                ),
                {"case_id": case_id},
            )
        ).mappings().first()
        if case is None:
            raise HTTPException(404, f"no case {case_id}")
        rows = (
            await session.execute(
                text(
                    "SELECT note_id, alert_token, author_role, note_text, noted_at "
                    "FROM shared.case_notes WHERE alert_token = :case_id "
                    "ORDER BY noted_at, created_at, note_id"
                ),
                {"case_id": case_id},
            )
        ).mappings().all()
    return case, [_note_dict(row) for row in rows]


@router.post("/{case_id}/notes", status_code=201)
async def add_note(case_id: uuid.UUID, body: CaseNoteIn) -> dict[str, Any]:
    author_role = body.author_role.strip()
    note_text = body.note_text.strip()
    if not author_role or not note_text:
        raise HTTPException(422, "author_role and note_text cannot be blank")
    noted_at = get_engine().sim_time

    async with session_scope() as session:
        exists = (
            await session.execute(
                text(
                    "SELECT 1 FROM shared.coordination_alerts "
                    "WHERE alert_token = :case_id"
                ),
                {"case_id": case_id},
            )
        ).scalar_one_or_none()
        if exists is None:
            raise HTTPException(404, f"no case {case_id}")
        row = (
            await session.execute(
                text(
                    "INSERT INTO shared.case_notes "
                    "(alert_token, author_role, note_text, noted_at) "
                    "VALUES (:case_id, :author_role, :note_text, :noted_at) "
                    "RETURNING note_id, alert_token, author_role, note_text, noted_at"
                ),
                {
                    "case_id": case_id,
                    "author_role": author_role,
                    "note_text": note_text,
                    "noted_at": noted_at,
                },
            )
        ).mappings().one()
        note = _note_dict(row)
        await session.execute(
            text(
                "INSERT INTO shared.simulation_events "
                "(sim_time, event_type, provider_id, payload) "
                "SELECT :noted_at, 'coordination.NOTE', provider_id, "
                "CAST(:payload AS jsonb) FROM shared.coordination_alerts "
                "WHERE alert_token = :case_id"
            ),
            {
                "noted_at": noted_at,
                "payload": json.dumps(
                    {**note, "alert_token": str(case_id)},
                    separators=(",", ":"),
                ),
                "case_id": case_id,
            },
        )

    try:
        await broadcaster.broadcast(
            sim_time=noted_at,
            event_type="coordination.NOTE",
            payload=note,
        )
    except Exception:
        log.exception("case note committed but live broadcast failed case=%s", case_id)
    return note


@router.get("/{case_id}/notes")
async def list_notes(case_id: uuid.UUID) -> dict[str, Any]:
    _, notes = await _load_case(case_id)
    return {"case_id": str(case_id), "notes": notes}


@router.get("/{case_id}/history")
async def case_history(case_id: uuid.UUID) -> dict[str, Any]:
    case, _ = await _load_case(case_id)
    async with session_scope() as session:
        audit_rows = (
            await session.execute(
                text(
                    "SELECT id, sim_time, event_type, payload "
                    "FROM shared.simulation_events "
                    "WHERE (payload->>'alert_token' = :case_id "
                    "       OR payload->>'case_id' = :case_id) "
                    "  AND event_type LIKE 'coordination.%' "
                    "ORDER BY id"
                ),
                {"case_id": str(case_id)},
            )
        ).mappings().all()
    history: list[dict[str, Any]] = []
    for row in audit_rows:
        payload = row["payload"]
        if row["event_type"] == "coordination.NOTE":
            history.append({
                "type": "note",
                "timestamp": row["sim_time"].isoformat(),
                "audit_sequence": row["id"],
                "note_id": payload["note_id"],
                "author_role": payload["author_role"],
                "text": payload["note_text"],
            })
        else:
            history.append({
                "type": "transition",
                "timestamp": row["sim_time"].isoformat(),
                "audit_sequence": row["id"],
                "from": payload.get("from"),
                "to": payload.get("status", row["event_type"].split(".")[-1]),
                "author_role": payload.get("actor", "system"),
                "text": payload.get("reason", ""),
            })
    return {
        "case_id": str(case_id),
        "status": case["status"],
        "severity": case["severity"],
        "provider_id": case["provider_id"],
        "history": history,
    }
