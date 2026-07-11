from datetime import datetime, timezone
import unittest

from app.domain.liquidity.historical_analytics import (
    HistoricalContext,
    HistoricalAnalytics,
    PositionHistory,
)


class HistoricalAnalyticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.history = PositionHistory(
            transaction_count=250,
            average_outflow_bdt=800.0,
            average_inflow_bdt=450.0,
            drain_rate_bdt_per_min=100.0,
            average_daily_balance_bdt=25_000.0,
            consistency_score=0.8,
        )

    def test_enrichment_preserves_live_forecast_contract(self) -> None:
        live = {
            "position_id": "bkash",
            "ewma_drain_bdt_per_min": 100.0,
            "confidence_score": 0.5,
            "predicted_tte_min": 10.0,
        }

        enriched = HistoricalAnalytics.enrich_forecast(
            live, self.history, window_days=30
        )

        self.assertEqual(enriched["confidence_score"], 0.5)
        self.assertEqual(enriched["predicted_tte_min"], 10.0)
        self.assertGreater(enriched["confidence_score_with_history"], 0.5)
        self.assertEqual(
            enriched["historical_context"]["historical_transactions"], 250
        )

    def test_no_history_does_not_inflate_live_confidence(self) -> None:
        empty = PositionHistory(0, 0.0, 0.0, 0.0, 0.0, 0.0)
        enriched = HistoricalAnalytics.enrich_forecast(
            {"ewma_drain_bdt_per_min": 75.0, "confidence_score": 0.35},
            empty,
            window_days=7,
        )
        self.assertEqual(enriched["confidence_score_with_history"], 0.35)

    def test_summary_keeps_provider_ledgers_separate(self) -> None:
        context = HistoricalContext(
            window_days=30,
            as_of=datetime(2026, 7, 11, tzinfo=timezone.utc),
            positions={"shared_cash": self.history, "bkash": self.history},
        )
        summary = context.as_dict()
        self.assertEqual(summary["historical_window_days"], 30)
        self.assertEqual(
            summary["provider_specific_averages"]["bkash"][
                "historical_transactions"
            ],
            250,
        )
        self.assertEqual(
            summary["provider_specific_averages"]["nagad"][
                "historical_transactions"
            ],
            0,
        )


if __name__ == "__main__":
    unittest.main()
