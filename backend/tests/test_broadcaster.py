from __future__ import annotations

from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase, TestCase

from app.infrastructure.broadcaster import Broadcaster


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StreamCursorTests(TestCase):
    def test_cursor_is_scoped_to_one_broadcaster_process(self) -> None:
        previous_process = Broadcaster()
        current_process = Broadcaster()

        old_cursor = previous_process.cursor_for(179)

        self.assertIsNone(current_process.sequence_from_cursor(old_cursor))
        self.assertIsNone(current_process.sequence_from_cursor("179"))
        self.assertEqual(
            current_process.sequence_from_cursor(current_process.cursor_for(7)), 7
        )


class BroadcasterReconnectTests(IsolatedAsyncioTestCase):
    async def test_stale_last_event_id_does_not_stall_restarted_broadcaster(
        self,
    ) -> None:
        previous_process = Broadcaster()
        current_process = Broadcaster()
        boundary = await current_process.stream_boundary(
            previous_process.cursor_for(179)
        )

        self.assertEqual(boundary.sequence, 0)
        self.assertFalse(boundary.same_epoch_reconnect)

        emitted = await current_process.broadcast(
            sim_time=_now(), event_type="tick.done", payload={"tick": 1}
        )
        received = await current_process.wait_event(
            since_id=boundary.sequence, timeout=0.05
        )

        self.assertEqual(received, emitted)
        self.assertIsNotNone(received)
        assert received is not None
        self.assertEqual(received.id, 1)

    async def test_snapshot_boundary_skips_older_buffer_but_keeps_concurrent_events(
        self,
    ) -> None:
        hub = Broadcaster()
        await hub.broadcast(
            sim_time=_now(), event_type="tick.done", payload={"tick": 1}
        )
        await hub.broadcast(
            sim_time=_now(), event_type="tick.done", payload={"tick": 2}
        )

        boundary = await hub.stream_boundary()
        self.assertEqual(boundary.sequence, 2)

        # Buffered events represented by the fresh snapshot are not replayed.
        self.assertIsNone(await hub.wait_event(boundary.sequence, timeout=0.01))

        # An event arriving during/after hydration remains available.
        emitted = await hub.broadcast(
            sim_time=_now(), event_type="coordination.PENDING", payload={"tick": 3}
        )
        received = await hub.wait_event(boundary.sequence, timeout=0.05)
        self.assertEqual(received, emitted)
        self.assertIsNotNone(received)
        assert received is not None
        self.assertEqual(received.event_type, "coordination.PENDING")
