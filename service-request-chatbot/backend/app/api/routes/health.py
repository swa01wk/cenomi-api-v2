"""Liveness, readiness, and detailed health endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import settings
from app.core.redis import get_redis
from app.db.session import get_db_session

log = structlog.get_logger(__name__)

router = APIRouter()


@router.get(
    "/health",
    summary="Liveness probe",
    description="Returns 200 immediately.  Used by load balancers to detect crashed instances.",
)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/ready",
    summary="Readiness probe",
    description=(
        "Verifies DB and Redis connectivity before reporting ready.  "
        "Returns 200 when all dependencies are reachable, 503 otherwise."
    ),
    responses={
        200: {"description": "All dependencies healthy."},
        503: {"description": "One or more dependencies unreachable."},
    },
)
async def ready() -> dict[str, object]:
    checks: dict[str, object] = {}
    all_ok = True

    # -- Database ------------------------------------------------------------
    try:
        async with get_db_session() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception as exc:
        log.warning("health.ready.db_check_failed", error=str(exc))
        checks["database"] = {"status": "error", "detail": str(exc)}
        all_ok = False

    # -- Redis ---------------------------------------------------------------
    try:
        redis = get_redis()
        await redis.ping()  # type: ignore[attr-defined]
        checks["redis"] = {"status": "ok"}
    except Exception as exc:
        log.warning("health.ready.redis_check_failed", error=str(exc))
        checks["redis"] = {"status": "error", "detail": str(exc)}
        all_ok = False

    from fastapi.responses import JSONResponse

    payload = {"status": "ready" if all_ok else "degraded", "checks": checks}
    return JSONResponse(content=payload, status_code=200 if all_ok else 503)


@router.post(
    "/dev/mock/reset",
    summary="Reset mock SR state (dev only)",
    description=(
        "Clears the in-memory mock SR state store so the next lifecycle test "
        "starts fresh.  Only works when SERVICE_REQUEST_API_BASE_URL is unset "
        "(mock mode).  Returns 404 in production."
    ),
)
async def reset_mock_state() -> dict[str, str]:
    if settings.service_request_api_base_url:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Not available in production mode")

    from app.agents.services.service_request_api_service import reset_mock_sr_state
    reset_mock_sr_state()
    log.info("dev.mock.reset", action="mock SR state cleared")
    return {"status": "ok", "message": "Mock SR state cleared"}
