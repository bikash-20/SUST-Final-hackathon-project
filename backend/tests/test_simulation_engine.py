from datetime import datetime, timedelta, timezone
from decimal import Decimal
from contextlib import asynccontextmanager
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.domain.provider.ledger import (
    ProviderBalanceResult,
    ProviderCustomerTransactionResult,
)
from app.domain.liquidity import HistoricalContext
from app.simulation.simulation_engine import SimulationEngine, Tick


class SimulationEngineAnalyticsIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_is_durable_before_worker_visibility(self) -> None:
        engine = SimulationEngine(agent_id=uuid.UUID(int=1))
        order: list[str] = []

        async def persist(*args, **kwargs):
            order.append("persisted")

        async def put(tick):
            order.append("queued")

        engine._queue = SimpleNamespace(put=put)
        tick = Tick(
            tick_id=str(uuid.UUID(int=77)),
            sim_time=engine.sim_time,
            kind="noop",
            payload={},
        )
        with patch.object(engine, "_persist_event", new=persist):
            await engine.enqueue_tick(tick)

        self.assertEqual(order, ["persisted", "queued"])

    async def test_restart_restores_durable_clock_watermark(self) -> None:
        engine = SimulationEngine(agent_id=uuid.UUID(int=1))
        durable_time = engine.sim_time + timedelta(days=2, minutes=17)

        class _Result:
            def scalar_one(self):
                return durable_time

        class _Session:
            async def execute(self, statement, params):
                self.statement = statement
                self.params = params
                return _Result()

        @asynccontextmanager
        async def _scope():
            yield _Session()

        with patch(
            "app.simulation.simulation_engine.session_scope",
            new=_scope,
        ):
            await engine._restore_clock_watermark()

        self.assertEqual(engine.sim_time, durable_time)

    async def test_advisory_dedupes_active_case_but_reopens_after_resolution(self) -> None:
        engine = SimulationEngine(agent_id=uuid.UUID(int=1))
        first_token = uuid.UUID(int=101)
        recurrence_token = uuid.UUID(int=202)
        statuses = AsyncMock(
            side_effect=["PENDING", "ACKNOWLEDGED", "ESCALATED", "RESOLVED"]
        )

        with (
            patch.object(engine, "_advisory_status", new=statuses),
            patch(
                "app.simulation.simulation_engine.coordination_fsm.raise_alert",
                new=AsyncMock(
                    side_effect=[
                        SimpleNamespace(alert_token=first_token),
                        SimpleNamespace(alert_token=recurrence_token),
                    ]
                ),
            ) as raise_alert,
        ):
            kwargs = {
                "key": "liquidity:nagad",
                "provider_id": "nagad",
                "severity": "high",
                "sim_time": datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc),
                "reason": "dynamic liquidity pressure",
            }
            opened = await engine._raise_advisory_once(**kwargs)
            pending_duplicate = await engine._raise_advisory_once(**kwargs)
            acknowledged_duplicate = await engine._raise_advisory_once(**kwargs)
            escalated_duplicate = await engine._raise_advisory_once(**kwargs)
            reopened = await engine._raise_advisory_once(**kwargs)

        self.assertEqual(opened, str(first_token))
        self.assertEqual(pending_duplicate, str(first_token))
        self.assertEqual(acknowledged_duplicate, str(first_token))
        self.assertEqual(escalated_duplicate, str(first_token))
        self.assertEqual(reopened, str(recurrence_token))
        self.assertEqual(statuses.await_count, 4)
        self.assertEqual(raise_alert.await_count, 2)
        self.assertEqual(
            engine._active_advisories["liquidity:nagad"],
            str(recurrence_token),
        )

    async def test_provider_drain_commits_balance_and_computes_tte(self) -> None:
        engine = SimulationEngine(agent_id=uuid.UUID(int=1))
        start = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)
        balances = [
            ProviderBalanceResult(
                provider_id="nagad",
                agent_id=engine.agent_id,
                balance_bdt=Decimal(str(30_000 - 1_000 * index)),
                version_id=index,
                updated_at=start + timedelta(minutes=index),
            )
            for index in range(1, 7)
        ]
        with patch(
            "app.simulation.simulation_engine.provider_ledger.apply_delta",
            new=AsyncMock(side_effect=balances),
        ) as mutate, patch.object(
            engine,
            "_historical_context",
            new=AsyncMock(
                return_value=HistoricalContext(30, start, positions={})
            ),
        ):
            result = None
            for index in range(1, 7):
                result = await engine._dispatch(
                    Tick(
                        tick_id=str(index),
                        sim_time=start + timedelta(minutes=index),
                        kind="provider_drain",
                        payload={
                            "provider_id": "nagad",
                            "amount_bdt": 1_000,
                            "interval_seconds": 60,
                        },
                    )
                )
        assert result is not None
        self.assertEqual(mutate.await_count, 6)
        self.assertEqual(result["provider_balance"]["balance_bdt"], 24_000.0)
        forecast = result["liquidity_forecast"]
        self.assertAlmostEqual(forecast["ewma_drain_bdt_per_min"], 1_000.0)
        self.assertAlmostEqual(forecast["predicted_tte_min"], 24.0)
        self.assertNotEqual(forecast["predicted_tte_min"], 9.5)

    async def test_unlabelled_transactions_drive_detector_and_cash_forecast(self) -> None:
        engine = SimulationEngine(agent_id=uuid.UUID(int=1))
        start = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)
        transaction_results = [
            ProviderCustomerTransactionResult(
                transaction_id=uuid.UUID(int=index),
                applied=True,
                shared_balance_bdt=Decimal(str(500_000 - 4_999 * index)),
                shared_version_id=index,
                provider_balance=ProviderBalanceResult(
                    provider_id="bkash",
                    agent_id=engine.agent_id,
                    balance_bdt=Decimal(str(120_000 + 4_999 * index)),
                    version_id=index,
                    updated_at=start + timedelta(minutes=index),
                ),
            )
            for index in range(1, 13)
        ]
        with (
            patch(
                "app.simulation.simulation_engine.provider_ledger.apply_customer_transaction",
                new=AsyncMock(side_effect=transaction_results),
            ) as apply_transaction,
            patch.object(
                engine,
                "_raise_advisory_once",
                new=AsyncMock(return_value="alert-test-token"),
            ) as raise_case,
            patch.object(
                engine,
                "_historical_context",
                new=AsyncMock(
                    return_value=HistoricalContext(30, start, positions={})
                ),
            ),
        ):
            result = None
            for index in range(12):
                result = await engine._dispatch(
                    Tick(
                        tick_id=str(uuid.UUID(int=index + 1)),
                        sim_time=start + timedelta(minutes=index),
                        kind="provider_txn",
                        payload={
                            "transaction_id": str(uuid.UUID(int=index + 1)),
                            "provider_id": "bkash",
                            "counterparty_msisdn": f"SYN-ACC-{index % 4}",
                            "amount_bdt": 4_999,
                            "direction": "out",
                            "synthetic": True,
                        },
                    )
                )
        assert result is not None
        self.assertEqual(apply_transaction.await_count, 12)
        self.assertTrue(result["anomaly_detection"]["triggered"])
        self.assertGreaterEqual(result["anomaly_detection"]["risk_score"], 0.65)
        self.assertTrue(result["anomaly_detection"]["requires_human_review"])
        self.assertEqual(result["coordination_alert_token"], "alert-test-token")
        self.assertGreaterEqual(raise_case.await_count, 1)
        self.assertNotIn("stream", result)
        self.assertEqual(result["shared_cash_balance"], 440_012.0)
        self.assertEqual(result["provider_balance"]["balance_bdt"], 179_988.0)

    async def test_provider_transaction_replay_skips_online_analytics(self) -> None:
        engine = SimulationEngine(agent_id=uuid.UUID(int=1))
        at = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)
        transaction_id = uuid.UUID(int=88)
        replay = ProviderCustomerTransactionResult(
            transaction_id=transaction_id,
            applied=False,
            shared_balance_bdt=Decimal("495001.00"),
            shared_version_id=2,
            provider_balance=ProviderBalanceResult(
                provider_id="bkash",
                agent_id=engine.agent_id,
                balance_bdt=Decimal("124999.00"),
                version_id=2,
                updated_at=at,
            ),
        )
        with patch(
            "app.simulation.simulation_engine.provider_ledger.apply_customer_transaction",
            new=AsyncMock(return_value=replay),
        ):
            result = await engine._dispatch(
                Tick(
                    tick_id=str(transaction_id),
                    sim_time=at,
                    kind="provider_txn",
                    payload={
                        "transaction_id": str(transaction_id),
                        "provider_id": "bkash",
                        "counterparty_msisdn": "SYN-ACC-0001",
                        "amount_bdt": 4_999,
                        "direction": "out",
                        "synthetic": True,
                    },
                )
            )

        self.assertTrue(result["idempotent_replay"])
        self.assertNotIn("anomaly_detection", result)
        self.assertNotIn("liquidity_forecast", result)


if __name__ == "__main__":
    unittest.main()
