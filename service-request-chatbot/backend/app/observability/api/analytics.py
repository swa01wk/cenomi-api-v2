"""Agent analytics API — the numbers behind the assistant admin dashboard.

Endpoints
---------
GET /analytics/overview       — headline KPIs, daily series, agent breakdown
GET /analytics/questions      — question log, filterable to the unanswered ones
GET /analytics/sessions/{id}  — one conversation's transcript with its ratings
GET /analytics/feedback       — star distribution and the comment feed
GET /analytics/engagement     — sessions, time spent, recent conversations
GET /analytics/insights       — node latency, API health, token spend, funnel

All are read-only aggregates over the agent observability tables and accept
the same window filters (``from``/``to``/``agent``/``user_id``). Defaults to
the trailing 30 days when no window is given, so a bare call is still a
bounded query.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status

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
            # Success and failure rates are computed over classified turns
            # only; coverage says what share of questions that is, so a rate
            # from a thin sample cannot be mistaken for a rate over all of them.
            "classification_coverage": (
                round(totals["classified"] / totals["questions"], 4)
                if totals["questions"]
                else 0.0
            ),
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
    questions_only: bool = Query(
        default=False,
        description=(
            "Exclude mid-workflow form input — a lease code, a date, 'yes' — "
            "leaving only what users actually asked."
        ),
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
        questions_only=questions_only,
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
    "/sessions/{session_id}",
    summary="One conversation — what was asked, what came back, how it rated",
    description=(
        "Full turn-by-turn transcript for a session, each turn carrying its "
        "answer, outcome, latency and any star rating or comment.  This is "
        "what a reviewer opens from a rating to see the exchange behind it."
    ),
    responses={status.HTTP_404_NOT_FOUND: {"description": "No such session."}},
)
async def analytics_session(
    session_id: UUID,
    db: DbSession,
    include_silent: bool = Query(
        default=False,
        description=(
            "Include background sync turns (blank questions the UI fires on "
            "upload or form patches). Off by default — they are not exchanges."
        ),
    ),
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    """Return one conversation's transcript with ratings attached."""
    repo = AnalyticsRepository(db)
    turns = await repo.session_transcript(session_id)
    if not turns:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No conversation found for session {session_id}.",
        )

    visible = turns if include_silent else [t for t in turns if not t["is_silent"]]
    rated = [t for t in turns if t["rating"] is not None]

    return {
        "session_id": str(session_id),
        "turns": visible,
        "summary": {
            "total_turns": len(turns),
            "questions": sum(1 for t in turns if not t["is_silent"]),
            "silent_turns": sum(1 for t in turns if t["is_silent"]),
            "started_at": turns[0]["at"],
            "last_activity_at": turns[-1]["at"],
            "ratings": [
                {
                    "trace_id": t["trace_id"],
                    "rating": t["rating"],
                    "comment": t["comment"],
                    "source": t["rating_source"],
                }
                for t in rated
            ],
        },
    }


@router.get(
    "/insights",
    summary="Operational insights — where time, money and failures actually go",
    description=(
        "Reads the tracing tables the dashboard does not otherwise touch: "
        "per-node latency from agent_runs, platform API reliability from "
        "agent_tool_calls, token spend and parse reliability from "
        "agent_llm_calls, and workflow stage drop-off from the traces."
    ),
)
async def analytics_insights(
    db: DbSession,
    filters: AnalyticsFilters = Depends(_filters),
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    """Return operational insights for the window."""
    repo = AnalyticsRepository(db)

    nodes = await repo.node_latency(filters)
    tools = await repo.tool_health(filters)
    llm = await repo.llm_usage(filters)
    funnel = await repo.workflow_funnel(filters)

    # The headline each section is worth surfacing on its own.
    slowest = nodes[0] if nodes else None
    worst_tool = min(tools, key=lambda x: x["success_rate"]) if tools else None
    top_prompt = llm["by_prompt"][0] if llm["by_prompt"] else None

    return {
        "window": {"from": filters.from_date, "to": filters.to_date},
        "highlights": {
            "slowest_node": slowest,
            "least_reliable_tool": worst_tool,
            "biggest_token_consumer": top_prompt,
        },
        "node_latency": nodes,
        "tool_health": tools,
        "llm_usage": llm,
        "workflow_funnel": funnel,
    }


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
