from datetime import datetime, timedelta, timezone
import unittest

from app.domain.liquidity.forecaster import EWMALiquidityForecaster


class EWMALiquidityForecasterTest(unittest.TestCase):
    def test_tte_is_computed_from_committed_deltas(self) -> None:
        model = EWMALiquidityForecaster(alpha=0.5)
        start = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)
        forecast = None
        balance = 10_000.0
        for minute in range(1, 7):
            balance -= 1_000.0
            forecast = model.update(
                position_id="nagad",
                balance_bdt=balance,
                delta_bdt=-1_000.0,
                at=start + timedelta(minutes=minute),
            )
        assert forecast is not None
        self.assertAlmostEqual(forecast.ewma_drain_bdt_per_min, 1_000.0)
        self.assertAlmostEqual(forecast.predicted_tte_min or -1, 4.0)
        self.assertEqual(forecast.sample_count, 6)
        self.assertGreater(forecast.confidence_score, 0.8)

    def test_replenishment_removes_false_shortage(self) -> None:
        model = EWMALiquidityForecaster(alpha=1.0)
        at = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)
        forecast = model.update(
            position_id="shared_cash",
            balance_bdt=50_000,
            delta_bdt=5_000,
            at=at,
        )
        self.assertIsNone(forecast.predicted_tte_min)
        self.assertEqual(forecast.status, "stable_or_replenishing")


if __name__ == "__main__":
    unittest.main()
