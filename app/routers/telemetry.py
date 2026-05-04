"""Health checks and Prometheus metrics endpoints."""
from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from app.config import settings
from app.sandbox.pool import pool
from app.schemas import PoolHealthResponse

router = APIRouter(tags=["Telemetry & Operations"])


@router.get("/health", response_model=PoolHealthResponse, summary="Service & Sandbox Pool Health")
async def health_check():
    """Returns the operational status of the service, Docker connectivity, and warm pool capacity."""
    stats = pool.get_stats()
    return PoolHealthResponse(
        status="HEALTHY",
        available_sandboxes=stats["idle_counts"],
        active_sandboxes=stats["active_count"],
        pool_min_idle=settings.POOL_MIN_IDLE,
        pool_max_idle=settings.POOL_MAX_IDLE,
        docker_connected=stats["docker_connected"],
        mock_mode=stats["mock_mode"]
    )


@router.get("/metrics", summary="Prometheus Telemetry Metrics")
async def get_prometheus_metrics():
    """Exports runtime Prometheus metrics (Evaluation durations, pass rates, sandbox latency)."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
