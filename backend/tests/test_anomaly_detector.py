from __future__ import annotations

import importlib
import json
import sys
import types
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

from app.domain.risk import (
    AnomalyDetectorConfig,
    SlidingWindowAnomalyDetector,
    TransactionObservation,
)


START = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)


def observation(
    index: int,
    *,
    minute: float,
    amount: float,
    account: str,
    provider: str = "bkash",
    direction: str = "out",
    is_salary_window: bool | None = None,
) -> TransactionObservation:
    return TransactionObservation(
        transaction_id=f"txn-{provider}-{index}",
        observed_at=START + timedelta(minutes=minute),
        provider_id=provider,
        account_id=account,
        amount_bdt=amount,
        direction=direction,
        is_salary_window=is_salary_window,
    )


class SlidingWindowAnomalyDetectorTests(unittest.TestCase):
    def test_detects_unlabelled_repeated_pattern_amid_varied_traffic(self) -> None:
        detector = SlidingWindowAnomalyDetector()
        index = 0
        result = None
        for minute in range(12):
            result = detector.observe(
                observation(
                    index,
                    minute=minute + 0.10,
                    amount=620 + minute * 137,
                    account=f"SYN-ACC-{40 + minute:04d}",
                )
            )
            index += 1
            result = detector.observe(
                observation(
                    index,
                    minute=minute + 0.20,
                    amount=4999,
                    account=f"SYN-ACC-{(minute % 4) + 1:04d}",
                    is_salary_window=True,
                )
            )
            index += 1

        assert result is not None
        self.assertTrue(result.triggered)
        self.assertFalse(result.calendar_adjustment_applied)
        self.assertIn("narrow/repeated", result.calendar_context)
        self.assertGreaterEqual(result.risk_score, 0.65)
        self.assertLessEqual(result.risk_score, 1.0)
        self.assertEqual(result.evidence.dominant_repeated_amount_bdt, "4999.00")
        self.assertEqual(result.evidence.dominant_repeated_amount_frequency, 12)
        self.assertEqual(result.evidence.dominant_amount_distinct_account_count, 4)
        self.assertGreater(result.evidence.cadence_regularity, 0.95)
        self.assertGreater(result.evidence.dominant_amount_velocity_per_minute, 1.0)
        self.assertTrue(result.requires_human_review)
        self.assertTrue(result.possible_benign_explanations)
        self.assertAlmostEqual(result.confidence + result.uncertainty, 1.0, places=4)
        json.dumps(result.to_dict())

    def test_varied_high_velocity_traffic_does_not_trigger(self) -> None:
        detector = SlidingWindowAnomalyDetector()
        result = None
        for index in range(80):
            result = detector.observe(
                observation(
                    index,
                    minute=index / 8,
                    amount=100 + index * 11.13,
                    account=f"SYN-ACC-{index:04d}",
                    is_salary_window=True,
                )
            )
        assert result is not None
        self.assertFalse(result.triggered)
        self.assertTrue(result.calendar_adjustment_applied)
        self.assertLess(result.risk_score, result.raw_risk_score + 0.0001)
        self.assertIn("salary-window adjustment", result.calendar_context)
        self.assertEqual(result.evidence.dominant_repeated_amount_frequency, 1)
        self.assertLess(result.risk_score, 0.65)

    def test_window_is_event_time_based_and_expires_old_transactions(self) -> None:
        detector = SlidingWindowAnomalyDetector()
        for index in range(6):
            detector.observe(
                observation(
                    index,
                    minute=index,
                    amount=5000,
                    account=f"SYN-ACC-{index % 3:04d}",
                )
            )

        result = detector.snapshot("bkash", at=START + timedelta(minutes=18))
        self.assertEqual(result.evidence.window_transaction_count, 0)
        self.assertEqual(result.evidence.outgoing_transaction_count, 0)
        self.assertFalse(result.triggered)
        with self.assertRaisesRegex(ValueError, "at is required"):
            SlidingWindowAnomalyDetector().snapshot("rocket")

    def test_duplicate_and_late_replay_are_not_double_counted(self) -> None:
        detector = SlidingWindowAnomalyDetector()
        first = observation(1, minute=5, amount=1000, account="SYN-ACC-0001")
        detector.observe(first)
        duplicate_result = detector.observe(first)
        self.assertEqual(duplicate_result.evidence.window_transaction_count, 1)

        detector.snapshot("bkash", at=START + timedelta(minutes=20))
        late_result = detector.observe(
            observation(2, minute=2, amount=1000, account="SYN-ACC-0002")
        )
        self.assertEqual(late_result.evidence.window_transaction_count, 0)

    def test_provider_windows_are_isolated(self) -> None:
        detector = SlidingWindowAnomalyDetector()
        bkash_result = None
        for index in range(10):
            bkash_result = detector.observe(
                observation(
                    index,
                    minute=index,
                    amount=4999,
                    account=f"SYN-ACC-{index % 4:04d}",
                )
            )
            detector.observe(
                observation(
                    index,
                    minute=index,
                    amount=700 + index,
                    account=f"SYN-ACC-{index + 20:04d}",
                    provider="nagad",
                )
            )
        assert bkash_result is not None
        self.assertTrue(bkash_result.triggered)
        self.assertFalse(detector.snapshot("nagad").triggered)
        self.assertEqual(
            detector.snapshot("nagad").evidence.window_transaction_count, 10
        )

    def test_allowlist_reduces_certainty_but_never_silently_suppresses(self) -> None:
        standard = SlidingWindowAnomalyDetector()
        allowlisted = SlidingWindowAnomalyDetector(
            AnomalyDetectorConfig(whitelisted_providers=frozenset({"bkash"}))
        )
        standard_result = None
        allowlisted_result = None
        for index in range(10):
            event = observation(
                index,
                minute=index,
                amount=4999,
                account=f"SYN-ACC-{index % 4:04d}",
            )
            standard_result = standard.observe(event)
            allowlisted_result = allowlisted.observe(event)
        assert standard_result is not None and allowlisted_result is not None
        self.assertEqual(standard_result.risk_score, allowlisted_result.risk_score)
        self.assertTrue(allowlisted_result.triggered)
        self.assertLess(allowlisted_result.confidence, standard_result.confidence)
        self.assertTrue(
            any(
                "allowlisted" in explanation
                for explanation in allowlisted_result.possible_benign_explanations
            )
        )

    def test_rejects_invalid_or_naive_input(self) -> None:
        detector = SlidingWindowAnomalyDetector()
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            detector.observe(
                TransactionObservation(
                    transaction_id="txn-1",
                    observed_at=datetime(2026, 7, 11, 9, 0),
                    provider_id="bkash",
                    account_id="SYN-ACC-0001",
                    amount_bdt=100,
                    direction="out",
                )
            )
        with self.assertRaisesRegex(ValueError, "positive finite"):
            detector.observe(
                observation(
                    2,
                    minute=0,
                    amount=float("nan"),
                    account="SYN-ACC-0002",
                )
            )


@dataclass(slots=True)
class _FakeTick:
    tick_id: str
    sim_time: datetime
    kind: str
    payload: dict[str, Any]


class _FakeEngine:
    def __init__(self, start: datetime = START) -> None:
        self.sim_time = start
        self.ticks: list[_FakeTick] = []

    async def enqueue_tick(self, tick: _FakeTick) -> None:
        self.ticks.append(tick)

    async def operational_snapshot(self) -> dict[str, Any]:
        return {
            "shared_cash_balance": 500_000.0,
            "provider_balances": {
                "bkash": 120_000.0,
                "nagad": 30_000.0,
                "rocket": 90_000.0,
            },
        }


class ScenarioBTests(unittest.IsolatedAsyncioTestCase):
    async def test_scenario_is_unlabelled_deterministic_and_detectable(self) -> None:
        fake_engine_module = types.ModuleType("app.simulation.simulation_engine")
        fake_engine_module.Tick = _FakeTick
        module_name = "app.simulation.scenarios.scenario_b"
        with patch.dict(
            sys.modules,
            {"app.simulation.simulation_engine": fake_engine_module},
        ):
            sys.modules.pop(module_name, None)
            scenario_b = importlib.import_module(module_name)
            first_engine = _FakeEngine()
            first_summary = await scenario_b.run(first_engine, {"seed": 48151623})
            sys.modules.pop(module_name, None)
            scenario_b = importlib.import_module(module_name)
            second_engine = _FakeEngine()
            second_summary = await scenario_b.run(second_engine, {"seed": 48151623})
            later_engine = _FakeEngine(START + timedelta(hours=1))
            await scenario_b.run(later_engine, {"seed": 48151623})
        sys.modules.pop(module_name, None)

        self.assertEqual(first_summary, second_summary)
        self.assertEqual(first_engine.ticks, second_engine.ticks)
        self.assertNotEqual(
            [tick.payload["transaction_id"] for tick in first_engine.ticks],
            [tick.payload["transaction_id"] for tick in later_engine.ticks],
        )
        self.assertEqual(len(first_engine.ticks), 102)
        self.assertEqual(
            first_engine.ticks,
            sorted(first_engine.ticks, key=lambda tick: (tick.sim_time, tick.tick_id)),
        )

        expected_keys = {
            "transaction_id",
            "provider_id",
            "counterparty_msisdn",
            "amount_bdt",
            "direction",
            "synthetic",
            "data_quality",
            "provider_whitelisted",
        }
        for tick in first_engine.ticks:
            self.assertEqual(set(tick.payload), expected_keys)
            self.assertTrue(tick.payload["synthetic"])
            self.assertTrue(
                str(tick.payload["counterparty_msisdn"]).startswith("SYN-ACC-")
            )
            for forbidden in ("stream", "anomaly", "ground_truth", "label"):
                self.assertNotIn(forbidden, tick.payload)
                self.assertFalse(any(forbidden in key for key in first_summary))

        # Conservative budgets make every debit valid even if worker commit
        # order differs from event-time order; credits are deliberately not
        # counted as funding for later debits.
        self.assertLessEqual(
            sum(
                float(tick.payload["amount_bdt"])
                for tick in first_engine.ticks
                if tick.payload["direction"] == "out"
            ),
            500_000.0,
        )
        opening_provider = {"bkash": 120_000.0, "nagad": 30_000.0, "rocket": 90_000.0}
        for provider, opening in opening_provider.items():
            self.assertLessEqual(
                sum(
                    float(tick.payload["amount_bdt"])
                    for tick in first_engine.ticks
                    if tick.payload["provider_id"] == provider
                    and tick.payload["direction"] == "in"
                ),
                opening,
            )

        detector = SlidingWindowAnomalyDetector()
        for tick in first_engine.ticks:
            payload = tick.payload
            detector.observe(
                TransactionObservation(
                    transaction_id=str(payload["transaction_id"]),
                    observed_at=tick.sim_time,
                    provider_id=str(payload["provider_id"]),
                    account_id=str(payload["counterparty_msisdn"]),
                    amount_bdt=payload["amount_bdt"],
                    direction=str(payload["direction"]),
                )
            )
        signal = detector.snapshot("bkash")
        self.assertTrue(signal.triggered)
        self.assertEqual(signal.evidence.dominant_repeated_amount_bdt, "4999.00")
        self.assertGreaterEqual(
            signal.evidence.dominant_repeated_amount_frequency, 12
        )

    async def test_optional_allowlist_is_provider_context_not_event_label(self) -> None:
        fake_engine_module = types.ModuleType("app.simulation.simulation_engine")
        fake_engine_module.Tick = _FakeTick
        module_name = "app.simulation.scenarios.scenario_b"
        with patch.dict(
            sys.modules,
            {"app.simulation.simulation_engine": fake_engine_module},
        ):
            sys.modules.pop(module_name, None)
            scenario_b = importlib.import_module(module_name)
            engine = _FakeEngine()
            await scenario_b.run(
                engine,
                {
                    "seed": 7,
                    "ordinary_transactions": 18,
                    "repeated_amount_transactions": 4,
                    "whitelisted_provider": "nagad",
                },
            )
        sys.modules.pop(module_name, None)

        for tick in engine.ticks:
            self.assertEqual(
                tick.payload["provider_whitelisted"],
                tick.payload["provider_id"] == "nagad",
            )


if __name__ == "__main__":
    unittest.main()
