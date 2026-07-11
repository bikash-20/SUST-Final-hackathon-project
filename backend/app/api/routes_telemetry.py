"""Telemetry router — replay-safe REST + live SSE stream.

The SSE endpoint subscribes to the in-process ``broadcaster``. The
engine emits on every tick persistence, so the front-end gets
millisecond-grade updates with zero polling.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from app.infrastructure.database import session_scope
from app.infrastructure.broadcaster import broadcaster
from app.simulation.simulation_engine import get_engine

log = logging.getLogger(__name__)
router = APIRouter()


async def _operational_snapshot() -> dict[str, Any]:
    current = await get_engine().operational_snapshot()
    async with session_scope() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT alert_token, status, severity, provider_id, "
                    "       sim_time, transitions "
                    "FROM shared.coordination_alerts "
                    "ORDER BY sim_time DESC LIMIT 100"
                )
            )
        ).mappings().all()
        analytical_rows = (
            await session.execute(
                text(
                    """
                    SELECT sim_time, payload->'result' AS result
                      FROM shared.simulation_events
                     WHERE event_type = 'tick.done'
                       AND payload->'result' IS NOT NULL
                       AND (
                            payload->'result'->'liquidity_forecast' IS NOT NULL
                            OR payload->'result'->'provider_liquidity_forecast' IS NOT NULL
                            OR payload->'result'->'anomaly_detection' IS NOT NULL
                       )
                     ORDER BY id DESC
                     LIMIT 500
                    """
                )
            )
        ).mappings().all()
    current["alerts"] = [
        {
            "alert_token": str(row["alert_token"]),
            "status": row["status"],
            "severity": row["severity"],
            "provider_id": row["provider_id"],
            "sim_time": row["sim_time"].isoformat(),
            "transitions": row["transitions"],
        }
        for row in rows
    ]

    # Rehydrate the latest durable analytical evidence after a browser or API
    # restart. Incremental SSE events continue from the snapshot boundary.
    forecasts: dict[str, dict[str, Any]] = {}
    anomaly_detections: list[dict[str, Any]] = []
    seen_detections: set[str] = set()
    for row in analytical_rows:
        result = row["result"]
        if not isinstance(result, dict):
            continue
        forecast = result.get("liquidity_forecast")
        if isinstance(forecast, dict):
            position_id = str(forecast.get("position_id", "")).strip()
            if position_id and position_id not in forecasts:
                forecasts[position_id] = forecast
        provider_forecast = result.get("provider_liquidity_forecast")
        if isinstance(provider_forecast, dict):
            position_id = str(provider_forecast.get("position_id", "")).strip()
            if position_id and position_id not in forecasts:
                forecasts[position_id] = provider_forecast

        detection = result.get("anomaly_detection")
        if not isinstance(detection, dict) or detection.get("triggered") is not True:
            continue
        detection_id = str(
            result.get("transaction_id")
            or detection.get("detection_id")
            or f"{row['sim_time'].isoformat()}:{len(anomaly_detections)}"
        )
        if detection_id in seen_detections:
            continue
        seen_detections.add(detection_id)
        if len(anomaly_detections) < 100:
            anomaly_detections.append(
                {
                    **detection,
                    "detection_id": detection_id,
                    "sim_time": row["sim_time"].isoformat(),
                }
            )

    current["liquidity_forecasts"] = forecasts
    current["anomaly_detections"] = anomaly_detections
    return current


@router.get("/snapshot")
async def snapshot() -> dict[str, Any]:
    """Current shared-cash and provider-separated e-money positions."""
    return await _operational_snapshot()


@router.get("/events")
async def list_events(
    since: datetime | None = Query(default=None,
                                   description="Return events with sim_time > since"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """Replay-safe: a client that fell behind passes its last-seen sim_time.

    The shared.simulation_events table is append-only so no tick is
    ever lost between the ticker and the SSE subscriber.
    """
    async with session_scope() as s:
        if since is None:
            rows = (await s.execute(
                text(
                    "SELECT sim_time, event_type, agent_id, provider_id, payload "
                    "FROM shared.simulation_events "
                    "ORDER BY sim_time DESC LIMIT :lim"
                ),
                {"lim": limit},
            )).mappings().all()
        else:
            rows = (await s.execute(
                text(
                    "SELECT sim_time, event_type, agent_id, provider_id, payload "
                    "FROM shared.simulation_events "
                    "WHERE sim_time > :since "
                    "ORDER BY sim_time ASC LIMIT :lim"
                ),
                {"since": since, "lim": limit},
            )).mappings().all()
    return [dict(r) for r in rows]


def _sse_format(event: str, data: dict[str, Any], event_id: str) -> str:
    """Format one SSE message."""
    return (
        f"id: {event_id}\n"
        f"event: {event}\n"
        f"data: {json.dumps(data, default=str, separators=(',', ':'))}\n\n"
    )


@router.get("/stream")
async def stream(request: Request):
    """Server-Sent Events telemetry stream.

    Protocol details:
      * Each yielded message carries an ``id: <process-epoch>:<sequence>``.
        A browser may reconnect with it, while IDs from an earlier backend
        process are safely recognized as stale.
      * Every connection receives a current snapshot first. The stream then
        starts after the high-water mark captured immediately before that
        snapshot, preventing older buffered events from overwriting it.
      * A heartbeat is emitted every 15 s so proxies don't kill the
        connection during quiet ticks.
      * The connection closes cleanly when the client disconnects
        (``request.is_disconnected()``) — no leaked tasks.
    """

    async def gen():
        last_header = request.headers.get("last-event-id")
        boundary = await broadcaster.stream_boundary(last_header)
        last_id = boundary.sequence

        if last_header and not boundary.same_epoch_reconnect:
            log.info("ignoring stale telemetry cursor from an earlier process")

        # Capture the boundary before hydration. Anything broadcast while the
        # snapshot is being built has an id above it and is replayed after the
        # snapshot; anything older is already represented by the snapshot.
        try:
            current = await _operational_snapshot()
            yield _sse_format(
                "snapshot",
                {
                    "id": last_id,
                    "sim_time": current["sim_time"],
                    "event_type": "snapshot",
                    "payload": current,
                },
                event_id=boundary.cursor,
            )
            yield _sse_format(
                "ready",
                {
                    "id": last_id,
                    "sim_time": datetime.now(timezone.utc).isoformat(),
                    "event_type": "ready",
                    "payload": {
                        "buffer_size": broadcaster.buffer_size,
                        "stream_epoch": broadcaster.epoch,
                    },
                },
                event_id=boundary.cursor,
            )
        except Exception:
            log.exception("failed to hydrate telemetry snapshot")
            return

        while True:
            if await request.is_disconnected():
                log.info("telemetry stream client disconnected")
                return
            evt = await broadcaster.wait_event(since_id=last_id, timeout=15.0)
            if evt is None:
                # Heartbeat keep-alive
                yield ": keep-alive\n\n"
                continue
            last_id = evt.id
            yield _sse_format(
                evt.event_type,
                {
                    "id": evt.id,
                    "sim_time": evt.sim_time.isoformat(),
                    "event_type": evt.event_type,
                    "payload": evt.payload,
                },
                event_id=broadcaster.cursor_for(evt.id),
            )

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers=headers)
