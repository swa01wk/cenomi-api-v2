"""Metrics endpoints for admin dashboards — computed live from the database."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.routes.session_analytics import close_idle_sessions
from app.db.session import DbSession
from app.observability.analytics_queries import (
    compute_feedback,
    compute_funnel,
    compute_summary,
    compute_users,
    parse_window,
)

router = APIRouter(prefix="/observability")


def _window(
    from_ts: datetime | None, to_ts: datetime | None
) -> tuple[datetime, datetime]:
    return parse_window(from_ts, to_ts)


@router.get("/metrics/summary")
async def metrics_summary(
    db: DbSession,
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    agent: str | None = None,
    user_id: UUID | None = None,
) -> dict[str, Any]:
    """Graph health plus product KPIs for the requested window (default 7d)."""
    await close_idle_sessions(db)
    start, end = _window(from_ts, to_ts)
    return await compute_summary(
        db, start=start, end=end, agent=agent, user_id=user_id
    )


@router.get("/metrics/users")
async def metrics_users(
    db: DbSession,
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    agent: str | None = None,
    user_id: UUID | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    """Per-user tokens, cost, sessions, and feedback."""
    await close_idle_sessions(db)
    start, end = _window(from_ts, to_ts)
    return await compute_users(
        db,
        start=start,
        end=end,
        agent=agent,
        user_id=user_id,
        page=page,
        page_size=page_size,
    )


@router.get("/metrics/funnel")
async def metrics_funnel(
    db: DbSession,
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    agent: str | None = None,
    user_id: UUID | None = None,
) -> dict[str, Any]:
    """SR-creation funnel and last-stage drop-off."""
    await close_idle_sessions(db)
    start, end = _window(from_ts, to_ts)
    return await compute_funnel(
        db, start=start, end=end, agent=agent, user_id=user_id
    )


@router.get("/metrics/feedback")
async def metrics_feedback(
    db: DbSession,
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    user_id: UUID | None = None,
) -> dict[str, Any]:
    """Thumbs, usefulness, CSAT, and not-useful reasons."""
    start, end = _window(from_ts, to_ts)
    return await compute_feedback(db, start=start, end=end, user_id=user_id)
