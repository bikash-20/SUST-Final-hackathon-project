"""In-process pub/sub hub for the SSE telemetry stream.

The SimulationEngine calls ``broadcast(event)`` whenever it appends a
row to ``shared.simulation_events``. The SSE endpoint subscribes via
``wait_event()`` and yields the event to the browser as soon as it
arrives — no client polling, no refresh latency.

Design
------
* Single ``asyncio.Condition`` guards an internal deque. Multiple SSE
  consumers wake up concurrently when a new event is broadcast.
* The deque is bounded (``MAX_BUFFER``) so a runaway producer cannot
  grow memory unbounded.
* Browser-facing cursors contain a random per-process epoch as well as
  the in-process sequence number. A ``Last-Event-ID`` from a previous
  backend process can therefore never hold a new stream behind the old
  process's higher sequence number.
* A connection takes a high-water boundary before hydrating its fresh
  snapshot. Events at or below that boundary are already represented by
  the snapshot; events broadcast while hydration is in progress remain
  available in the buffer and are delivered afterwards.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Deque, Final, Optional
from uuid import uuid4

log = logging.getLogger(__name__)

MAX_BUFFER: Final = 1024


@dataclass(slots=True)
class StreamEvent:
    id: int                          # monotonic within one process epoch
    sim_time: datetime
    event_type: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StreamBoundary:
    """Atomic starting point for one hydrated SSE connection."""

    sequence: int
    cursor: str
    same_epoch_reconnect: bool


class Broadcaster:
    """Singleton in-memory pub/sub hub."""

    def __init__(self) -> None:
        self._cond = asyncio.Condition()
        self._buffer: Deque[StreamEvent] = deque(maxlen=MAX_BUFFER)
        self._next_id = 1
        # UUID hex is safe inside an SSE ``id`` field (no whitespace/newlines).
        self._epoch = uuid4().hex

    @property
    def epoch(self) -> str:
        return self._epoch

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)

    def cursor_for(self, sequence: int) -> str:
        """Encode an EventSource-compatible, process-scoped cursor."""
        if sequence < 0:
            raise ValueError("stream sequence cannot be negative")
        return f"{self._epoch}:{sequence}"

    def sequence_from_cursor(self, cursor: str | None) -> int | None:
        """Decode only cursors issued by this broadcaster process.

        Legacy numeric IDs and IDs from a restarted process deliberately
        return ``None``. Treating either as a local sequence could make a
        new process wait until it catches up with an unrelated old counter.
        """
        if not cursor:
            return None
        epoch, separator, raw_sequence = cursor.partition(":")
        if separator != ":" or epoch != self._epoch or not raw_sequence.isdigit():
            return None
        return int(raw_sequence)

    async def stream_boundary(self, last_event_id: str | None = None) -> StreamBoundary:
        """Capture the buffer high-water mark used before snapshot hydration.

        A fresh operational snapshot supersedes buffered events at or below
        this sequence. ``same_epoch_reconnect`` is informational: even a valid
        reconnect cursor is not replayed ahead of a newer full snapshot.
        """
        async with self._cond:
            sequence = self._next_id - 1
            return StreamBoundary(
                sequence=sequence,
                cursor=self.cursor_for(sequence),
                same_epoch_reconnect=(
                    self.sequence_from_cursor(last_event_id) is not None
                ),
            )

    async def broadcast(
        self,
        *,
        sim_time: datetime,
        event_type: str,
        payload: dict[str, Any],
    ) -> StreamEvent:
        async with self._cond:
            evt = StreamEvent(
                id=self._next_id,
                sim_time=sim_time,
                event_type=event_type,
                payload=payload,
            )
            self._next_id += 1
            self._buffer.append(evt)
            self._cond.notify_all()
            return evt

    async def wait_event(
        self,
        since_id: int = 0,
        timeout: float = 15.0,
    ) -> Optional[StreamEvent]:
        """Block until an event with id > since_id arrives, or timeout."""
        async with self._cond:
            # Drain anything already buffered that is newer than since_id.
            for evt in self._buffer:
                if evt.id > since_id:
                    return evt
            try:
                await asyncio.wait_for(self._cond.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return None
            for evt in self._buffer:
                if evt.id > since_id:
                    return evt
            return None


# Module-level singleton.
broadcaster = Broadcaster()
