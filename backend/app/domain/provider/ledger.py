"""Provider-scoped e-money ledger operations.

Every operation authenticates with the corresponding PostgreSQL provider
role.  The shared application role never receives direct access to provider
tables, while this aggregate exposes the small, whitelisted surface needed by
the simulation and combined operational view.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text

from app.infrastructure.database import (
    PROVIDERS,
    provider_session_scope,
    session_scope,
    validate_provider_id,
)


class InsufficientProviderBalance(RuntimeError):
    """Raised when a committed movement would overdraw provider e-money."""


@dataclass(frozen=True, slots=True)
class ProviderBalanceResult:
    provider_id: str
    agent_id: uuid.UUID
    balance_bdt: Decimal
    version_id: int
    updated_at: datetime

    def as_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "balance_bdt": float(self.balance_bdt),
            "version_id": self.version_id,
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ProviderCustomerTransactionResult:
    """Committed, cross-ledger customer transaction result.

    ``applied`` is false only when ``transaction_id`` has already committed;
    callers must then skip analytics so replay cannot double-count evidence.
    """

    transaction_id: uuid.UUID
    applied: bool
    shared_balance_bdt: Decimal
    shared_version_id: int
    provider_balance: ProviderBalanceResult


class ProviderLedger:
    async def apply_customer_transaction(
        self,
        *,
        transaction_id: uuid.UUID,
        provider_id: str,
        agent_id: uuid.UUID,
        counterparty_id: str,
        amount_bdt: Decimal,
        direction: str,
        sim_time: datetime,
        freshness: str = "fresh",
    ) -> ProviderCustomerTransactionResult:
        """Apply physical cash and inverse e-money movements atomically.

        The database function is ``SECURITY DEFINER`` but tightly allowlisted;
        the shared application role keeps zero direct privileges on provider
        schemas.  A unique transaction UUID makes retries exact-once.
        """
        provider = validate_provider_id(provider_id)
        if direction not in {"in", "out"}:
            raise ValueError("direction must be 'in' or 'out'")
        if amount_bdt <= 0:
            raise ValueError("amount_bdt must be positive")
        async with session_scope() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT applied,
                               shared_balance_bdt,
                               shared_version_id,
                               provider_balance_bdt,
                               provider_version_id,
                               provider_updated_at
                          FROM shared.apply_provider_customer_transaction(
                               :transaction_id,
                               :agent_id,
                               :provider_id,
                               :counterparty_id,
                               :amount_bdt,
                               :direction,
                               :freshness,
                               :sim_time
                          )
                        """
                    ),
                    {
                        "transaction_id": transaction_id,
                        "agent_id": agent_id,
                        "provider_id": provider,
                        "counterparty_id": counterparty_id,
                        "amount_bdt": amount_bdt,
                        "direction": direction,
                        "freshness": freshness,
                        "sim_time": sim_time,
                    },
                )
            ).mappings().one()

        provider_balance = ProviderBalanceResult(
            provider_id=provider,
            agent_id=agent_id,
            balance_bdt=Decimal(row["provider_balance_bdt"]),
            version_id=int(row["provider_version_id"]),
            updated_at=row["provider_updated_at"],
        )
        return ProviderCustomerTransactionResult(
            transaction_id=transaction_id,
            applied=bool(row["applied"]),
            shared_balance_bdt=Decimal(row["shared_balance_bdt"]),
            shared_version_id=int(row["shared_version_id"]),
            provider_balance=provider_balance,
        )

    async def get_balance(
        self,
        *,
        provider_id: str,
        agent_id: uuid.UUID,
    ) -> ProviderBalanceResult:
        provider = validate_provider_id(provider_id)
        async with provider_session_scope(provider) as session:
            row = (
                await session.execute(
                    text(
                        f"SELECT balance_bdt, version_id, updated_at "
                        f"FROM {provider}.provider_balance WHERE agent_id = :agent"
                    ),
                    {"agent": agent_id},
                )
            ).mappings().first()
        if row is None:
            raise LookupError(f"no {provider} balance for agent {agent_id}")
        return ProviderBalanceResult(
            provider_id=provider,
            agent_id=agent_id,
            balance_bdt=Decimal(row["balance_bdt"]),
            version_id=int(row["version_id"]),
            updated_at=row["updated_at"],
        )

    async def apply_delta(
        self,
        *,
        provider_id: str,
        agent_id: uuid.UUID,
        delta_bdt: Decimal,
    ) -> ProviderBalanceResult:
        provider = validate_provider_id(provider_id)
        if delta_bdt == 0:
            return await self.get_balance(provider_id=provider, agent_id=agent_id)
        async with provider_session_scope(provider) as session:
            row = (
                await session.execute(
                    text(
                        f"""
                        UPDATE {provider}.provider_balance
                           SET balance_bdt = balance_bdt + :delta,
                               version_id = version_id + 1,
                               updated_at = now()
                         WHERE agent_id = :agent
                           AND balance_bdt + :delta >= 0
                        RETURNING balance_bdt, version_id, updated_at
                        """
                    ),
                    {"agent": agent_id, "delta": delta_bdt},
                )
            ).mappings().first()
            if row is None:
                existing = (
                    await session.execute(
                        text(
                            f"SELECT balance_bdt FROM {provider}.provider_balance "
                            "WHERE agent_id = :agent"
                        ),
                        {"agent": agent_id},
                    )
                ).first()
                if existing is None:
                    raise LookupError(f"no {provider} balance for agent {agent_id}")
                raise InsufficientProviderBalance(
                    f"{provider} balance {existing.balance_bdt} cannot apply delta {delta_bdt}"
                )
        return ProviderBalanceResult(
            provider_id=provider,
            agent_id=agent_id,
            balance_bdt=Decimal(row["balance_bdt"]),
            version_id=int(row["version_id"]),
            updated_at=row["updated_at"],
        )

    async def record_transaction(
        self,
        *,
        transaction_id: uuid.UUID,
        provider_id: str,
        agent_id: uuid.UUID,
        counterparty_id: str,
        amount_bdt: Decimal,
        direction: str,
        sim_time: datetime,
        freshness: str = "fresh",
    ) -> None:
        provider = validate_provider_id(provider_id)
        if direction not in {"in", "out"}:
            raise ValueError("direction must be 'in' or 'out'")
        if amount_bdt <= 0:
            raise ValueError("amount_bdt must be positive")
        async with provider_session_scope(provider) as session:
            await session.execute(
                text(
                    f"""
                    INSERT INTO {provider}.provider_txn
                        (transaction_id, agent_id, counterparty_msisdn, amount_bdt,
                         direction, freshness, sim_time)
                    VALUES (:transaction_id, :agent, :counterparty, :amount, :direction,
                            :freshness, :sim_time)
                    ON CONFLICT (transaction_id) WHERE transaction_id IS NOT NULL
                    DO NOTHING
                    """
                ),
                {
                    "transaction_id": transaction_id,
                    "agent": agent_id,
                    "counterparty": counterparty_id,
                    "amount": amount_bdt,
                    "direction": direction,
                    "freshness": freshness,
                    "sim_time": sim_time,
                },
            )

    async def snapshot(self, *, agent_id: uuid.UUID) -> dict[str, dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        for provider in PROVIDERS:
            result[provider] = (
                await self.get_balance(provider_id=provider, agent_id=agent_id)
            ).as_dict()
        return result


provider_ledger = ProviderLedger()
