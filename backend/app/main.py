"""FastAPI application factory.

Wires the SimulationEngine to a single demo agent and exposes the
control + telemetry endpoints declared in ``docs/04_API_CONTRACT.md``.
"""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI

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

    @app.get("/healthz")
    async def healthz():
        return {"ok": await ping(), "engine_running": get_engine().is_running}

    app.include_router(simulation_router, prefix="/v1/simulation")
    app.include_router(telemetry_router, prefix="/v1/telemetry")
    app.include_router(coordination_router, prefix="/v1/coordination")
    app.include_router(metrics_router, prefix="/v1/metrics")
    app.include_router(cases_router, prefix="/v1/cases")
    return app


app = create_app()
