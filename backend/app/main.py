"""FastAPI application factory.

Wires the SimulationEngine to a single demo agent and exposes the
control + telemetry endpoints declared in ``docs/04_API_CONTRACT.md``.
"""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware

from app.infrastructure.database import close_database_connections, ping
from app.simulation.simulation_engine import init_default_engine, get_engine
from app.api.routes_simulation import router as simulation_router
from app.api.routes_telemetry import router as telemetry_router
from app.api.routes_coordination import router as coordination_router
from app.api.routes_metrics import router as metrics_router
from app.api.routes_cases import router as cases_router

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s :: %(message)s")


def _cors_origins() -> list[str]:
    configured = os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    )
    return [
        origin.strip().rstrip("/")
        for origin in configured.split(",")
        if origin.strip()
    ]


@asynccontextmanager
async def lifespan(app: FastAPI):
    agent_id = uuid.UUID(os.getenv("DEMO_AGENT_ID", "00000000-0000-0000-0000-000000000001"))
    engine = init_default_engine(agent_id=agent_id)
    await engine.start()
    log.info("app startup ok agent=%s", agent_id)
    try:
        yield
    finally:
        await engine.stop()
        await close_database_connections()
        log.info("app shutdown ok")


def create_app() -> FastAPI:
    app = FastAPI(
        title="LiquiGuard API",
        version="0.2.0",
        lifespan=lifespan,
    )

    origins = _cors_origins()
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        )

    @app.get("/health")
    async def health():
        """Process-only liveness endpoint suitable for an external ping."""
        return {"ok": True}

    @app.get("/healthz")
    async def healthz(response: Response):
        """Readiness endpoint used by Render's deploy health check."""
        database_ok = await ping()
        if not database_ok:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"ok": database_ok, "engine_running": get_engine().is_running}

    app.include_router(simulation_router, prefix="/v1/simulation")
    app.include_router(telemetry_router, prefix="/v1/telemetry")
    app.include_router(coordination_router, prefix="/v1/coordination")
    app.include_router(metrics_router, prefix="/v1/metrics")
    app.include_router(cases_router, prefix="/v1/cases")
    return app


app = create_app()
