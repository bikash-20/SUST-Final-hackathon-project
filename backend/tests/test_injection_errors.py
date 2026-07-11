from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app.simulation.injection import (
    InsufficientSharedCashForBurst,
    inject_transactions,
)
from app.simulation.simulation_engine import SimulationEngine


class _PostgresCheckViolation(Exception):
    sqlstate = "23514"

    def __str__(self) -> str:
        return "insufficient shared cash for agent demo"


class _LowBalanceEngine:
    sim_time = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)

    async def operational_snapshot(self):
        return {"shared_cash_balance": 100.0}


class InjectionErrorTests(unittest.IsolatedAsyncioTestCase):
    def test_wrapped_postgres_check_violation_is_classified(self) -> None:
        wrapped = IntegrityError("SELECT ledger_fn()", {}, _PostgresCheckViolation())
        self.assertTrue(
            SimulationEngine._is_insufficient_shared_cash_error(wrapped)
        )

    async def test_preflight_rejects_burst_with_structured_balance(self) -> None:
        with self.assertRaises(InsufficientSharedCashForBurst) as raised:
            await inject_transactions(
                _LowBalanceEngine(),
                provider="bkash",
                number_of_transactions=2,
                minimum_amount_bdt=60.0,
                maximum_amount_bdt=60.0,
                amount_pattern="near_identical",
                distinct_accounts=2,
                window_seconds=5,
                is_salary_window=False,
            )
        self.assertEqual(raised.exception.as_dict(), {
            "error": "insufficient_shared_cash",
            "message": "Not enough shared cash to complete this transaction burst.",
            "available_balance": 100.0,
        })


if __name__ == "__main__":
    unittest.main()
