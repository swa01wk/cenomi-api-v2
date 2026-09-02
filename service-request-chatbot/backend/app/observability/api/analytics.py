"""Agent analytics API — the numbers behind the assistant admin dashboard.

Endpoints
---------
GET /analytics/overview     — headline KPIs, daily series, agent breakdown
GET /analytics/questions    — question log, filterable to the unanswered ones
GET /analytics/feedback     — star distribution and the comment feed
GET /analytics/engagement   — sessions, time spent, recent conversations

All four are read-only aggregates over the agent observability tables and
accept the same window filters (``from``/``to``/``agent``/``user_id``).
Defaults to the trailing 30 days when no window is given, so a bare call is
still a bounded query.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query

from app.core.security import get_auth_context
from app.db.session import DbSession
from app.observability.repositories.analytics_repo import (
    AnalyticsFilters,
    AnalyticsRepository,
)
from app.types.chat import AuthContext

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/analytics")

DEFAULT_WINDOW_DAYS = 30
_MAX_PAGE_SIZE = 200


def _window(
    from_date: datetime | None,
    to_date: datetime | None,
    agent: str | None,
    user_id: UUID | None,
) -> AnalyticsFilters:
    """Resolve the query window, defaulting to the trailing 30 days."""
    resolved_to = to_date or datetime.now(timezone.utc)
    resolved_from = from_date or (resolved_to - timedelta(days=DEFAULT_WINDOW_DAYS))
    return AnalyticsFilters(
        from_date=resolved_from,
        to_date=resolved_to,
        agent=agent,
        user_id=user_id,
    )


# Shared query parameters — declared once so all four endpoints agree.
def _filters(
    from_date: datetime | None = Query(
        default=None,
        alias="from",
        description="Window start (ISO-8601). Defaults to 30 days before 'to'.",
    ),
    to_date: datetime | None = Query(
        default=None,
        alias="to",
        description="Window end (ISO-8601). Defaults to now.",
    ),
    agent: str | None = Query(
        default=None,
        description="Restrict to one agent, e.g. help_agent, rdd_handover_agent.",
    ),
    user_id: UUID | None = Query(
        default=None, description="Restrict to one user's activity."
    ),
) -> AnalyticsFilters:
    return _window(from_date, to_date, agent, user_id)


@router.get(
    "/overview",
    summary="Headline analytics — volume, success ratios, CSAT, cost",
    description=(
        "Everything the dashboard's top strip needs in one call: questions "
        "asked, answered vs unanswered ratios, SR funnel completion, latency, "
        "tokens and cost, star ratings, plus a daily series and per-agent split."
    ),
)
async def analytics_overview(
    db: DbSession,
    filters: AnalyticsFilters = Depends(_filters),
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    """Return the headline KPIs for the window."""
    repo = AnalyticsRepository(db)

    totals = await repo.turn_totals(filters)
    ratings = await repo.rating_totals(filters)
    funnel = await repo.sr_funnel(filters)
    engagement = await repo.engagement(filters)
    p95_latency = await repo.latency_percentile(filters, 0.95)
    series = await repo.daily_series(filters)
    agents = await repo.agent_breakdown(filters)

    return {
        "window": {
            "from": filters.from_date,
            "to": filters.to_date,
            "agent": filters.agent,
        },
        "totals": {
            **totals,
            "p95_latency_ms": p95_latency or 0,
            "avg_session_minutes": engagement["avg_session_minutes"],
            "total_time_spent_minutes": engagement["total_time_spent_minutes"],
        },
        "sr_funnel": funnel,
        "ratings": ratings,
        "daily": series,
        "by_agent": agents,
    }


@router.get(
    "/questions",
    summary="Question log — what was asked, and whether it was answered",
    description=(
        "Paginated log of user questions with each turn's outcome, latency and "
        "rating.  Filter ``answer_status=UNANSWERED`` for the content-gap "
        "report that drives FAQ improvements."
    ),
)
async def analytics_questions(
    db: DbSession,
    filters: AnalyticsFilters = Depends(_filters),
    answer_status: Literal["ANSWERED", "PARTIAL", "UNANSWERED", "FAILED"] | None = Query(
        default=None, description="Restrict to one outcome class."
    ),
    search: str | None = Query(
        default=None, description="Case-insensitive substring match on the question."
    ),
    page: int = Query(default=1, ge=1, description="1-based page number."),
    page_size: int = Query(default=50, ge=1, le=_MAX_PAGE_SIZE),
    include_top: bool = Query(
        default=False, description="Also return the most-asked questions."
    ),
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    """Return the paginated question log for the window."""
    repo = AnalyticsRepository(db)
    offset = (page - 1) * page_size

    items, total = await repo.questions(
        filters,
        answer_status=answer_status,
        search=search,
        limit=page_size,
        offset=offset,
    )

    payload: dict[str, Any] = {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_next": offset + len(items) < total,
    }
    if include_top:
        payload["top_questions"] = await repo.top_questions(filters)
    return payload


@router.get(
    "/feedback",
    summary="Star ratings — distribution, averages and the comment feed",
)
async def analytics_feedback(
    db: DbSession,
    filters: AnalyticsFilters = Depends(_filters),
    max_score: int | None = Query(
        default=None,
        ge=1,
        le=5,
        description="Only comments at or below this rating (e.g. 3 for detractors).",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=_MAX_PAGE_SIZE),
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    """Return rating aggregates plus the paginated comment feed."""
    repo = AnalyticsRepository(db)
    offset = (page - 1) * page_size

    summary = await repo.rating_totals(filters)
    comments, total = await repo.feedback_comments(
        filters, max_score=max_score, limit=page_size, offset=offset
    )

    return {
        "summary": summary,
        "comments": {
            "items": comments,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_next": offset + len(comments) < total,
        },
    }


@router.get(
    "/engagement",
    summary="Sessions, time spent, and recent conversations",
)
async def analytics_engagement(
    db: DbSession,
    filters: AnalyticsFilters = Depends(_filters),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=_MAX_PAGE_SIZE),
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    """Return engagement aggregates and the recent-conversation list."""
    repo = AnalyticsRepository(db)
    offset = (page - 1) * page_size

    summary = await repo.engagement(filters)
    hourly = await repo.hourly_sessions(filters)
    sessions, total = await repo.recent_sessions(
        filters, limit=page_size, offset=offset
    )

    return {
        "summary": summary,
        "hourly_sessions": hourly,
        "sessions": {
            "items": sessions,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_next": offset + len(sessions) < total,
        },
    }
