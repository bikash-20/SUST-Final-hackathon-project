from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
import uuid

from app.simulation.scenarios import scenario_a


class _AdvancingEngine:
    """Fake engine that exposes the former enqueue-time clock race."""

    def __init__(self) -> None:
        self.sim_time = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)
        self.agent_id = uuid.UUID(int=1)
        self.ticks = []

    async def enqueue_tick(self, tick) -> None:
        self.ticks.append(tick)
        # A live worker/pump may advance while the scenario is still enqueueing.
        self.sim_time += timedelta(minutes=3)


class ScenarioATests(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_uses_one_immutable_event_time_origin(self) -> None:
        engine = _AdvancingEngine()
        start = engine.sim_time

        result = await scenario_a.run(
            engine,
            {
                "providers": ["nagad"],
                "n_bursts": 3,
                "interval_seconds": 60,
                "drain_per_burst": 1_000,
                "cash_in_per_step": 1_000,
            },
        )

        self.assertEqual(result["enqueued"], 6)
        self.assertNotIn("tte", result)
        self.assertNotIn("advisory_tte", result)
        self.assertEqual(
            [tick.sim_time for tick in engine.ticks],
            [
                start,
                start,
                start + timedelta(minutes=1),
                start + timedelta(minutes=1),
                start + timedelta(minutes=2),
                start + timedelta(minutes=2),
            ],
        )


if __name__ == "__main__":
    unittest.main()
