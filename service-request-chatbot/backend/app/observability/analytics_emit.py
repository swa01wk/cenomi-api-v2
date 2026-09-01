"""Fail-safe analytics emit used from orchestration and TraceManager.

Mirrors TraceManager: never raises into the chat turn.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.repositories.analytics_event_repo import AnalyticsEventRepository

log = structlog.get_logger(__name__)


async def emit_analytics(
    db: AsyncSession,
    *,
    user_id: UUID,
    session_id: UUID,
    event_name: str,
    trace_id: UUID | None = None,
    properties: dict[str, Any] | None = None,
    once: bool = False,
    dedupe_sr_id: str | None = None,
) -> None:
    """Insert an analytics event; swallow every failure."""
    try:
        repo = AnalyticsEventRepository(db)
        if once:
            await repo.emit_once(
                user_id=user_id,
                session_id=session_id,
                event_name=event_name,
                trace_id=trace_id,
                properties=properties,
                dedupe_sr_id=dedupe_sr_id,
            )
        else:
            await repo.emit(
                user_id=user_id,
                session_id=session_id,
                event_name=event_name,
                trace_id=trace_id,
                properties=properties,
            )
    except Exception:
        log.exception(
            "analytics.emit.failed",
            event_name=event_name,
            session_id=str(session_id),
        )
