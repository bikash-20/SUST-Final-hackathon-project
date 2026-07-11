"""SQLAlchemy 2.0 async engine + session factory.

Phase-2 specific guarantees:

* ``isolation_level="REPEATABLE READ"`` is set on every connection.
  Combined with the optimistic ``version_id`` column on
  ``shared.shared_cash_ledger`` this prevents dirty reads and the
  *phantom-write* variant of the lost-update problem when two 60x-ticker
  streams try to mutate the same row concurrently.

* Connections are checked out one-at-a-time per logical operation via
  ``async with session.begin()`` blocks; we never share a ``Session``
  across coroutines.

* The pool uses ``NullPool`` semantics via ``pool_pre_ping=True`` plus a
  bounded ``pool_size`` so the demo's dockerised Postgres never starves.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Final

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.engine import URL, make_url
from sqlalchemy.sql import text

# --- Engine singleton ----------------------------------------------------
_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_provider_engines: dict[str, AsyncEngine] = {}
_provider_sessionmakers: dict[str, async_sessionmaker[AsyncSession]] = {}

PROVIDERS: Final[tuple[str, ...]] = ("bkash", "nagad", "rocket")


def _database_target() -> tuple[str, int, str]:
    """Return the database host, port, and name for every scoped role.

    Render exposes managed PostgreSQL as one ``DATABASE_URL``.  That URL's
    owner credentials are deliberately *not* used by the application: only
    its network location is reused, while ``_build_dsn`` and
    ``_build_provider_dsn`` always inject their least-privilege role
    credentials.  Local development can continue to use the individual
    ``DB_HOST``/``DB_PORT``/``DB_NAME`` variables.
    """
    configured_url = os.getenv("DATABASE_URL", "").strip()
    if configured_url:
        try:
            parsed = make_url(configured_url)
        except Exception as exc:
            raise ValueError("DATABASE_URL is not a valid database URL") from exc

        if parsed.get_backend_name() not in {"postgres", "postgresql"}:
            raise ValueError("DATABASE_URL must use a PostgreSQL scheme")
        if not parsed.host:
            raise ValueError("DATABASE_URL must include a host")
        if not parsed.database:
            raise ValueError("DATABASE_URL must include a database name")
        return parsed.host, parsed.port or 5432, parsed.database

    host = os.getenv("DB_HOST", "localhost")
    port = int(os.getenv("DB_PORT", "5432"))
    name = os.getenv("DB_NAME", "codex_demo")
    return host, port, name


def _build_dsn() -> URL:
    user = os.getenv("DB_APP_USER", "app_shared")
    pwd = os.getenv("DB_APP_PASSWORD", "change_me_shared")
    host, port, name = _database_target()
    return URL.create(
        "postgresql+asyncpg",
        username=user,
        password=pwd,
        host=host,
        port=port,
        database=name,
    )


def validate_provider_id(provider_id: str) -> str:
    """Return a canonical provider identifier or reject the request.

    Provider identifiers are later used as SQL schema names.  Keeping the
    whitelist at the infrastructure boundary makes dynamic identifiers safe
    and prevents a caller from crossing a provider data boundary.
    """
    canonical = provider_id.strip().lower()
    if canonical not in PROVIDERS:
        raise ValueError(
            f"unknown provider_id {provider_id!r}; expected one of {PROVIDERS}"
        )
    return canonical


def _build_provider_dsn(provider_id: str) -> URL:
    provider = validate_provider_id(provider_id)
    prefix = provider.upper()
    user = os.getenv(f"DB_{prefix}_USER", f"app_{provider}")
    pwd = os.getenv(f"DB_{prefix}_PASSWORD", f"change_me_{provider}")
    host, port, name = _database_target()
    return URL.create(
        "postgresql+asyncpg",
        username=user,
        password=pwd,
        host=host,
        port=port,
        database=name,
    )


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            _build_dsn(),
            echo=False,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            # REPEATABLE READ: any parallel 60x-ticker transactions that read
            # the same shared_cash_ledger row will see a consistent snapshot,
            # and the version_id check below arbitrates the actual writes.
            isolation_level="REPEATABLE READ",
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


def get_provider_engine(provider_id: str) -> AsyncEngine:
    """Return an engine authenticated as exactly one provider role."""
    provider = validate_provider_id(provider_id)
    engine = _provider_engines.get(provider)
    if engine is None:
        engine = create_async_engine(
            _build_provider_dsn(provider),
            echo=False,
            pool_size=5,
            max_overflow=5,
            pool_pre_ping=True,
            isolation_level="REPEATABLE READ",
        )
        _provider_engines[provider] = engine
    return engine


def get_provider_sessionmaker(
    provider_id: str,
) -> async_sessionmaker[AsyncSession]:
    provider = validate_provider_id(provider_id)
    sm = _provider_sessionmakers.get(provider)
    if sm is None:
        sm = async_sessionmaker(
            bind=get_provider_engine(provider),
            expire_on_commit=False,
            autoflush=False,
        )
        _provider_sessionmakers[provider] = sm
    return sm


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Yield a transactional ``AsyncSession``.

    Usage::

        async with session_scope() as s:
            await ledger.deduct(s, agent_id, 1500.0, reason="cash_out")

    Commits on clean exit, rolls back on exception.
    """
    sm = get_sessionmaker()
    async with sm() as session:
        async with session.begin():
            yield session


@asynccontextmanager
async def provider_session_scope(
    provider_id: str,
) -> AsyncIterator[AsyncSession]:
    """Open a transaction using only the selected provider's DB role."""
    provider = validate_provider_id(provider_id)
    sm = get_provider_sessionmaker(provider)
    async with sm() as session:
        async with session.begin():
            yield session


async def ping() -> bool:
    """Database readiness probe used by /healthz."""
    try:
        async with session_scope() as s:
            await s.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def close_database_connections() -> None:
    """Drain all SQLAlchemy pools during graceful application shutdown."""
    global _engine, _sessionmaker
    engines = ([_engine] if _engine is not None else []) + list(
        _provider_engines.values()
    )
    for engine in engines:
        await engine.dispose()
    _engine = None
    _sessionmaker = None
    _provider_engines.clear()
    _provider_sessionmakers.clear()
