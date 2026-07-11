"""Operator-triggered synthetic bursts routed through ordinary provider ticks."""
from __future__ import annotations

import asyncio
import random
import uuid
from datetime import timedelta
from typing import Any

from app.simulation.simulation_engine import Tick


class InsufficientSharedCashForBurst(RuntimeError):
    def __init__(self, available_balance: float) -> None:
        self.available_balance = available_balance
        super().__init__("Not enough shared cash to complete this transaction burst.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "error": "insufficient_shared_cash",
            "message": str(self),
            "available_balance": self.available_balance,
        }


async def inject_transactions(
    engine,
    *,
    provider: str,
    number_of_transactions: int,
    minimum_amount_bdt: float,
    maximum_amount_bdt: float,
    amount_pattern: str,
    distinct_accounts: int,
    window_seconds: int,
    is_salary_window: bool,
) -> dict[str, Any]:
    """Create normal ``provider_txn`` ticks and await their ordinary results.

    Event times are distributed across ``window_seconds`` but processing is
    immediate, so a judge can compare a tight burst with a spread-out series
    without waiting several real minutes.
    """
    origin = engine.sim_time
    run_id = uuid.uuid4()
    rng = random.Random(run_id.int)

    amounts: list[float] = []
    if amount_pattern == "near_identical":
        base = round((minimum_amount_bdt + maximum_amount_bdt) / 2.0, 2)
        for index in range(number_of_transactions):
            # Keep a strong exact dominant amount while allowing a small
            # minority of one-paisa variations to remain "near-identical".
            amount = base if index % 6 else max(0.01, base + (0.01 if index % 12 else -0.01))
            amounts.append(round(amount, 2))
    else:
        cent_min = round(minimum_amount_bdt * 100)
        cent_max = round(maximum_amount_bdt * 100)
        available_values = max(1, cent_max - cent_min + 1)
        for index in range(number_of_transactions):
            # A deterministic stride avoids accidental repeated dominant
            # amounts when the supplied range has enough cent values.
            offset = (index * 7919 + rng.randrange(available_values)) % available_values
            amounts.append(round((cent_min + offset) / 100.0, 2))

    snapshot_before = await engine.operational_snapshot()
    total_bdt = round(sum(amounts), 2)
    available_before = float(snapshot_before["shared_cash_balance"])
    if total_bdt > available_before:
        raise InsufficientSharedCashForBurst(
            available_balance=available_before,
        )

    loop = asyncio.get_running_loop()
    completions: list[asyncio.Future[dict[str, Any]]] = []
    for index, amount in enumerate(amounts):
        seconds_after_start = (
            0
            if number_of_transactions == 1
            else round(index * window_seconds / (number_of_transactions - 1))
        )
        transaction_id = uuid.uuid5(run_id, f"transaction:{index}")
        completion: asyncio.Future[dict[str, Any]] = loop.create_future()

        async def completed(
            tick: Tick,
            result: dict[str, Any],
            future: asyncio.Future[dict[str, Any]] = completion,
        ) -> None:
            if not future.done():
                future.set_result({"result": result})

        async def failed(
            tick: Tick,
            error: dict[str, Any],
            future: asyncio.Future[dict[str, Any]] = completion,
        ) -> None:
            if not future.done():
                future.set_result({"error": error})

        await engine.enqueue_tick(Tick(
            tick_id=str(uuid.uuid5(run_id, f"tick:{index}")),
            sim_time=origin + timedelta(seconds=seconds_after_start),
            kind="provider_txn",
            payload={
                "transaction_id": str(transaction_id),
                "provider_id": provider,
                "counterparty_msisdn": (
                    f"SYN-INJECT-{str(run_id)[:8]}-{index % distinct_accounts:03d}"
                ),
                "amount_bdt": amount,
                "direction": "out",
                "interval_seconds": max(
                    1.0, window_seconds / max(1, number_of_transactions - 1)
                ),
                "synthetic": True,
                "data_quality": "fresh",
                "is_salary_window": is_salary_window,
                "source": "operator_injector",
            },
            on_complete=completed,
            on_error=failed,
        ))
        completions.append(completion)

    try:
        completion_results = await asyncio.wait_for(
            asyncio.gather(*completions),
            timeout=max(15.0, number_of_transactions * 0.5),
        )
    except TimeoutError as exc:
        raise RuntimeError("injected transactions did not complete in time") from exc

    failures = [
        item["error"] for item in completion_results if "error" in item
    ]
    insufficient = next(
        (
            failure for failure in failures
            if failure.get("error") == "insufficient_shared_cash"
        ),
        None,
    )
    if insufficient is not None:
        raise InsufficientSharedCashForBurst(
            available_balance=float(insufficient["available_balance"])
        )
    if failures:
        raise RuntimeError(str(failures[0].get("message", "injected tick failed")))
    results = [item["result"] for item in completion_results]

    detections = [
        result["anomaly_detection"]
        for result in results
        if isinstance(result.get("anomaly_detection"), dict)
    ]
    # Report the final full-window evaluation, not an early intermediate peak
    # produced while the burst is still accumulating.
    final_detection = detections[-1]
    final_result = results[-1]
    snapshot_after = await engine.operational_snapshot()
    return {
        "injected": number_of_transactions,
        "provider": provider,
        "amount_pattern": amount_pattern,
        "amount_range_bdt": [minimum_amount_bdt, maximum_amount_bdt],
        "total_bdt": total_bdt,
        "distinct_accounts": distinct_accounts,
        "window_seconds": window_seconds,
        "is_salary_window": is_salary_window,
        "synthetic": True,
        "anomaly_outcome": final_detection,
        "latest_liquidity_forecast": final_result.get("liquidity_forecast"),
        "latest_provider_forecast": final_result.get("provider_liquidity_forecast"),
        "balance_state": {
            "sim_time": snapshot_after["sim_time"],
            "shared_cash_balance": snapshot_after["shared_cash_balance"],
            "shared_cash_version": snapshot_after["shared_cash_version"],
            "provider_position": snapshot_after["provider_positions"][provider],
        },
    }
