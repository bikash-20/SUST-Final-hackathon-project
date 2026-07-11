from __future__ import annotations

import threading
import unittest
from datetime import datetime, timedelta, timezone

from app.api.routes_metrics import metrics_snapshot
from app.domain.metrics import RuntimeMetricsCollector, runtime_metrics


class RuntimeMetricsCollectorTest(unittest.TestCase):
    def test_empty_snapshot_contains_only_zero_or_undefined_metrics(self) -> None:
        snapshot = RuntimeMetricsCollector().snapshot()

        self.assertEqual(snapshot["processing_latency_ms"]["sample_count"], 0)
        self.assertIsNone(snapshot["processing_latency_ms"]["p50"])
        self.assertIsNone(snapshot["processing_latency_ms"]["p95"])
        self.assertEqual(snapshot["ticks"]["total_count"], 0)
        self.assertEqual(snapshot["ticks"]["success_count"], 0)
        self.assertEqual(snapshot["ticks"]["failure_count"], 0)
        self.assertIsNone(snapshot["ticks"]["success_rate"])
        self.assertEqual(snapshot["anomalies"]["evaluation_count"], 0)
        self.assertIsNone(snapshot["anomalies"]["explanation_coverage_pct"])
        self.assertEqual(snapshot["forecasts"]["count"], 0)
        lead = snapshot["forecasts"]["shortage_lead_time_seconds"]
        self.assertEqual(lead["sample_count"], 0)
        self.assertTrue(all(lead[key] is None for key in ("min", "mean", "p50", "p90", "max")))

    def test_latency_percentiles_and_tick_success_rate_are_measured(self) -> None:
        collector = RuntimeMetricsCollector()
        collector.record_tick(success=True, latency_ms=10)
        collector.record_tick(success=False, latency_ms=20)
        collector.record_tick(success=True, latency_ms=30)

        snapshot = collector.snapshot()
        self.assertEqual(snapshot["processing_latency_ms"]["p50"], 20.0)
        self.assertEqual(snapshot["processing_latency_ms"]["p95"], 29.0)
        self.assertEqual(snapshot["ticks"]["success_count"], 2)
        self.assertEqual(snapshot["ticks"]["failure_count"], 1)
        self.assertEqual(snapshot["ticks"]["success_rate"], 0.666667)

    def test_rolling_samples_are_bounded(self) -> None:
        collector = RuntimeMetricsCollector(sample_capacity=3)
        for latency in (1, 2, 100, 10):
            collector.record_tick(success=True, latency_ms=latency)

        snapshot = collector.snapshot()
        self.assertEqual(snapshot["ticks"]["total_count"], 4)
        self.assertEqual(snapshot["processing_latency_ms"]["sample_count"], 3)
        self.assertEqual(snapshot["processing_latency_ms"]["p50"], 10.0)

    def test_explanation_coverage_uses_detections_as_denominator(self) -> None:
        collector = RuntimeMetricsCollector()
        collector.record_anomaly_evaluation(
            detected=False, explanation_present=False
        )
        collector.record_anomaly_evaluation(
            detected=True, explanation_present=False
        )
        collector.record_anomaly_evaluation(
            detected=True, explanation_present=True
        )

        anomaly = collector.snapshot()["anomalies"]
        self.assertEqual(anomaly["evaluation_count"], 3)
        self.assertEqual(anomaly["detection_count"], 2)
        self.assertEqual(anomaly["detection_rate"], 0.666667)
        self.assertEqual(anomaly["explanation_coverage_pct"], 50.0)

    def test_shortage_lead_time_pairs_first_warning_with_observation(self) -> None:
        collector = RuntimeMetricsCollector()
        start = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)
        collector.record_forecast(
            position_id="nagad", observed_at=start, shortage_expected=True
        )
        collector.record_forecast(
            position_id="nagad",
            observed_at=start + timedelta(minutes=1),
            shortage_expected=True,
        )

        lead = collector.record_shortage_observed(
            position_id="nagad", observed_at=start + timedelta(minutes=5)
        )
        unmatched = collector.record_shortage_observed(
            position_id="bkash", observed_at=start + timedelta(minutes=6)
        )

        self.assertEqual(lead, 300.0)
        self.assertIsNone(unmatched)
        forecasts = collector.snapshot()["forecasts"]
        self.assertEqual(forecasts["count"], 2)
        self.assertEqual(forecasts["observed_shortage_count"], 2)
        self.assertEqual(forecasts["matched_shortage_count"], 1)
        self.assertEqual(
            forecasts["shortage_lead_time_seconds"]["mean"], 300.0
        )

    def test_recovery_closes_stale_shortage_warning(self) -> None:
        collector = RuntimeMetricsCollector()
        start = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)
        collector.record_forecast(
            position_id="shared_cash", observed_at=start, shortage_expected=True
        )
        collector.record_forecast(
            position_id="shared_cash",
            observed_at=start + timedelta(minutes=1),
            shortage_expected=False,
        )

        lead = collector.record_shortage_observed(
            position_id="shared_cash",
            observed_at=start + timedelta(minutes=2),
        )
        self.assertIsNone(lead)
        self.assertEqual(
            collector.snapshot()["forecasts"]["matched_shortage_count"], 0
        )

    def test_concurrent_recording_does_not_lose_updates(self) -> None:
        collector = RuntimeMetricsCollector(sample_capacity=64)
        per_thread = 250
        workers = 8

        def record() -> None:
            for _ in range(per_thread):
                collector.record_tick(success=True, latency_ms=1.0)
                collector.record_anomaly_evaluation(
                    detected=True, explanation_present=True
                )

        threads = [threading.Thread(target=record) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        snapshot = collector.snapshot()
        expected = per_thread * workers
        self.assertEqual(snapshot["ticks"]["success_count"], expected)
        self.assertEqual(snapshot["anomalies"]["evaluation_count"], expected)
        self.assertEqual(snapshot["anomalies"]["detection_count"], expected)
        self.assertEqual(snapshot["processing_latency_ms"]["sample_count"], 64)

    def test_invalid_measurements_are_rejected(self) -> None:
        collector = RuntimeMetricsCollector()
        with self.assertRaises(ValueError):
            collector.record_tick(success=True, latency_ms=float("nan"))
        with self.assertRaises(ValueError):
            collector.record_forecast(
                position_id="nagad",
                observed_at=datetime(2026, 7, 11, 9, 0),
                shortage_expected=True,
            )


class RuntimeMetricsRouteTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        runtime_metrics.reset()

    async def asyncTearDown(self) -> None:
        runtime_metrics.reset()

    async def test_route_returns_shared_collector_snapshot(self) -> None:
        runtime_metrics.record_tick(success=True, latency_ms=12.5)

        response = await metrics_snapshot()

        self.assertEqual(response["ticks"]["success_count"], 1)
        self.assertEqual(response["processing_latency_ms"]["p50"], 12.5)


if __name__ == "__main__":
    unittest.main()
