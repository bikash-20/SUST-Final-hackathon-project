"""Measured runtime metrics endpoint."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.domain.metrics import runtime_metrics


router = APIRouter()


@router.get("")
async def metrics_snapshot() -> dict[str, Any]:
    """Return measured in-process analytics and processing evidence."""
    return runtime_metrics.snapshot()
