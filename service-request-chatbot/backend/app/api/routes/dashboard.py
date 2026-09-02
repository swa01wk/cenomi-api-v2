"""Assistant-admin dashboard API.

    GET /dashboard/assistant-admin
    GET /dashboard/assistant-admin/conversations

Serves the contract the tenant frontend's assistant-admin dashboard already
renders (``libs/api/src/lib/assistantAdmin``), so no frontend rewrite is
needed to switch it onto this service.  Two things differ from the legacy
implementation it replaces, both improvements:

1. **The numbers are real.**  ``accuracy_pct`` was a groundedness proxy
   (did retrieval return any source?) and CSAT was derived from the same
   proxy.  Both now come from recorded truth: ``agent_traces.answer_status``
   for accuracy, and end-user star ratings in ``agent_feedback`` for CSAT.
2. **The stubs are filled.**  ``escalation_rate_pct``, ``escalated``,
   ``resolved`` and ``user_name`` were hard-coded; each is now computed and
   its definition documented on the field below.

Response envelope is ``{success, data}`` / ``{success, errors}`` to match the
frontend's ``httpRequest`` decoder.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.routes.msp_chat import _require_service_token
from app.db.models import User
from app.db.session import DbSession
from app.observability.repositories.analytics_repo import (
    AnalyticsFilters,
    AnalyticsRepository,
)
from app.observability.user_directory import find_user_ids, resolve_names

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Contract-fixed period keys; the frontend indexes on these exact strings.
_PERIODS: dict[str, int] = {"24h": 1, "7d": 7, "30d": 30}

_MAX_LIMIT = 100


def _pct(numerator: float, denominator: float) -> float:
    """Percentage rounded to one decimal; 0.0 when the denominator is empty."""
    if not denominator:
        return 0.0
    return round((numerator / denominator) * 100, 1)


def _time_label(moment: datetime | None, now: datetime) -> str:
    """Relative label the conversation list shows ("2h ago")."""
    if moment is None:
        return ""
    delta = now - moment
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


async def _resolve_user_names(
    db: DbSession, user_ids: list[str]
) -> dict[str, str]:
    """Map chat user ids to display names.

    Two sources, in order: the platform directory (where nearly every real
    user lives, reached by reversing the uuid5 subject hash), then the local
    ``users`` table used by login-gated chat.  Anything still unresolved —
    service accounts, the anonymous stub — falls back to a short id label.
    """
    names: dict[str, str] = {}

    try:
        directory = await resolve_names(user_ids)
        for uid, entry in directory.items():
            label = entry.get("name") or entry.get("email")
            if label:
                names[uid] = label
    except Exception:
        # A directory outage must not take the dashboard down with it.
        log.warning("dashboard.user_directory_failed", exc_info=True)

    missing = [u for u in user_ids if u and u not in names]
    parsed: list[UUID] = []
    for raw in missing:
        try:
            parsed.append(UUID(raw))
        except (ValueError, AttributeError, TypeError):
            continue
    if parsed:
        rows = await db.execute(
            select(User.id, User.username).where(User.id.in_(parsed))
        )
        for row in rows:
            names[str(row.id)] = row.username

    return names


async def _period_block(
    repo: AnalyticsRepository, days: int, now: datetime
) -> dict[str, Any]:
    """Build one period's metrics plus its trend arrays and delta label."""
    window = AnalyticsFilters(from_date=now - timedelta(days=days), to_date=now)
    previous = AnalyticsFilters(
        from_date=now - timedelta(days=days * 2), to_date=now - timedelta(days=days)
    )

    totals = await repo.turn_totals(window)
    series = await repo.daily_series(window)
    prior = await repo.turn_totals(previous)
    engagement = await repo.engagement(window)
    hourly = await repo.hourly_sessions(window)

    accuracy_pct = _pct(totals["answered"], totals["classified"])
    prior_accuracy = _pct(prior["answered"], prior["classified"])
    delta = round(accuracy_pct - prior_accuracy, 1)
    sign = "+" if delta >= 0 else ""

    return {
        "accuracy_pct": accuracy_pct,
        # How many turns that percentage is actually computed over. Without
        # this, "100%" beside "82 questions" reads as 82 of 82 when it may be
        # 2 of 2 — turns recorded before outcome classification shipped are
        # not counted. Coverage tells the reader how far to trust the figure.
        "accuracy_sample_size": totals["classified"],
        "accuracy_coverage_pct": _pct(totals["classified"], totals["questions"]),
        # Real questions only — silent refresh turns are excluded.
        "questions_count": totals["questions"],
        # Approximated as the busiest hour's distinct-session count — the
        # closest honest reading of concurrency from turn timestamps.
        "peak_concurrent_users": max(hourly) if hourly else 0,
        "avg_response_time_sec": round(totals["avg_latency_ms"] / 1000, 2),
        # Escalation = share of turns the agent could not answer
        # (UNANSWERED or FAILED) — the turns a human has to pick up.
        "escalation_rate_pct": _pct(
            totals["unanswered"] + totals["failed"], totals["classified"]
        ),
        "sessions_count": totals["sessions"],
        "questions_trend": [point["turns"] for point in series],
        "accuracy_trend": [
            round(point["answered_rate"] * 100, 1) for point in series
        ],
        "trend_labels": [point["date"] for point in series],
        "trend_delta_vs_previous_label": f"{sign}{delta}% vs previous {days}d",
        "avg_session_length_min": engagement["avg_session_minutes"],
    }


@router.get(
    "/assistant-admin",
    summary="Assistant admin dashboard — periods, questions, sessions, CSAT",
    dependencies=[Depends(_require_service_token)],
)
async def assistant_admin_dashboard(db: DbSession) -> dict[str, Any]:
    """Return the full dashboard payload in the frontend's envelope."""
    now = datetime.now(timezone.utc)
    repo = AnalyticsRepository(db)

    try:
        periods = {
            key: await _period_block(repo, days, now)
            for key, days in _PERIODS.items()
        }

        month = AnalyticsFilters(from_date=now - timedelta(days=30), to_date=now)
        totals = await repo.turn_totals(month)
        ratings = await repo.rating_totals(month)
        engagement = await repo.engagement(month)
        top = await repo.top_questions(month, limit=20)
        hourly_today = await repo.hourly_sessions(
            AnalyticsFilters(from_date=now - timedelta(days=1), to_date=now)
        )
        sessions, _ = await repo.recent_sessions(month, limit=10)
    except Exception as exc:
        log.exception("dashboard.assistant_admin.failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not build the dashboard. Please try again.",
        ) from exc

    names = await _resolve_user_names(db, [s["user_id"] for s in sessions if s["user_id"]])

    # CSAT as a percentage of the 5-star scale, from real end-user ratings.
    avg_rating = ratings["avg_rating"]
    csat_pct = round((avg_rating / 5) * 100, 1) if avg_rating is not None else 0.0

    return {
        "success": True,
        "data": {
            "periods": periods,
            "top_questions": [
                {"question": row["question"], "count": row["count"]} for row in top
            ],
            "hourly_sessions_today": hourly_today,
            "summary_strip": {
                "questions_not_answered_pct": _pct(
                    totals["unanswered"] + totals["failed"], totals["classified"]
                ),
                "user_satisfaction_csat_pct": csat_pct,
                "avg_session_length_min": engagement["avg_session_minutes"],
                # Fallback = the agent replied without being able to answer.
                "fallback_rate_pct": _pct(totals["unanswered"], totals["classified"]),
                "questions_per_session": engagement["avg_turns_per_session"],
                # Additive fields — the raw rating signal behind CSAT.
                "avg_rating": avg_rating,
                "ratings_count": ratings["ratings_count"],
                "rating_distribution": ratings["distribution"],
            },
            "recent_conversations": [
                _conversation_row(session, names, now) for session in sessions
            ],
        },
    }


def _conversation_row(
    session: dict[str, Any], names: dict[str, str], now: datetime
) -> dict[str, Any]:
    """Map one aggregated session onto the frontend's conversation row shape."""
    user_id = session["user_id"] or ""
    agent_label = session.get("agent_label") or ""
    # The column is a message preview, so show what the user opened with.
    # A session of only silent turns has no question — leave it blank rather
    # than substituting the agent name, which would read as something the
    # user said.
    preview = (session.get("first_question") or "").strip()
    return {
        "id": session["session_id"],
        "user_name": names.get(user_id) or (f"User {user_id[:8]}" if user_id else ""),
        "message_preview": preview,
        "turns": session["turns"],
        # Escalated = at least one turn the agent could not answer.
        "escalated": session["unanswered_turns"] > 0,
        "resolved": session["resolved"],
        "time_label": _time_label(session["last_activity_at"], now),
        # Additive fields the current UI ignores but the richer view uses.
        # `agent` is the human label — the raw routing key is `agent_key`.
        "agent": agent_label,
        "agent_key": session["agent"],
        "duration_seconds": session["duration_seconds"],
        "avg_rating": session["avg_rating"],
        "ratings_count": session.get("ratings_count", 0),
        # What the user wrote alongside their stars, joined when a session
        # carries more than one comment.
        "comment": session.get("comment"),
        "srs_created": session["srs_created"],
    }


@router.get(
    "/assistant-admin/insights",
    summary="Agent health — node latency, API reliability, token spend, funnel",
    dependencies=[Depends(_require_service_token)],
)
async def assistant_admin_insights(
    db: DbSession,
    days: int = Query(
        default=30, ge=1, le=365, description="Look-back window in days."
    ),
) -> dict[str, Any]:
    """Operational view of the agent, in the dashboard's envelope.

    Reads the tracing tables the tenant-facing metrics do not touch:
    ``agent_runs`` for per-node latency, ``agent_tool_calls`` for platform API
    reliability, and ``agent_llm_calls`` for token spend.
    """
    now = datetime.now(timezone.utc)
    repo = AnalyticsRepository(db)
    filters = AnalyticsFilters(from_date=now - timedelta(days=days), to_date=now)

    try:
        nodes = await repo.node_latency(filters)
        tools = await repo.tool_health(filters)
        llm = await repo.llm_usage(filters)
        funnel = await repo.workflow_funnel(filters)
        totals = await repo.turn_totals(filters)
    except Exception as exc:
        log.exception("dashboard.assistant_admin_insights.failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not build agent health. Please try again.",
        ) from exc

    return {
        "success": True,
        "data": {
            "window_days": days,
            "highlights": {
                "slowest_node": nodes[0] if nodes else None,
                "least_reliable_tool": (
                    min(tools, key=lambda x: x["success_rate"]) if tools else None
                ),
                "biggest_token_consumer": (
                    llm["by_prompt"][0] if llm["by_prompt"] else None
                ),
            },
            "node_latency": nodes,
            "tool_health": tools,
            "llm_usage": llm,
            "workflow_funnel": funnel,
            "turn_totals": {
                "total_turns": totals["total_turns"],
                "failed_traces": totals["failed_traces"],
                "avg_latency_ms": totals["avg_latency_ms"],
            },
        },
    }


@router.get(
    "/assistant-admin/conversations/{session_id}",
    summary="One conversation — the questions asked and the replies given",
    dependencies=[Depends(_require_service_token)],
    responses={status.HTTP_404_NOT_FOUND: {"description": "No such conversation."}},
)
async def assistant_admin_conversation_detail(
    session_id: UUID,
    db: DbSession,
    include_silent: bool = Query(
        default=False,
        description="Include background sync turns, which carry no question.",
    ),
) -> dict[str, Any]:
    """Return the transcript behind a conversation row."""
    repo = AnalyticsRepository(db)
    try:
        turns = await repo.session_transcript(session_id)
    except Exception as exc:
        log.exception("dashboard.conversation_detail.failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not load the conversation. Please try again.",
        ) from exc

    if not turns:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No conversation found for session {session_id}.",
        )

    visible = turns if include_silent else [t for t in turns if not t["is_silent"]]
    user_id = turns[0]["user_id"]
    names = await _resolve_user_names(db, [user_id])

    return {
        "success": True,
        "data": {
            "session_id": str(session_id),
            "turns": [
                {
                    "trace_id": t["trace_id"],
                    "asked_at": t["at"],
                    "question": t["question"],
                    "answer": t["answer"],
                    "agent": t["agent_label"],
                    "intent": t["intent"],
                    "answer_status": t["answer_status"],
                    "latency_ms": t["latency_ms"],
                    "rating": t["rating"],
                    "comment": t["comment"],
                    "error": t["error"],
                }
                for t in visible
            ],
            "total_turns": len(turns),
            "questions": sum(1 for t in turns if not t["is_silent"]),
            "silent_turns": sum(1 for t in turns if t["is_silent"]),
            "started_at": turns[0]["at"],
            "last_activity_at": turns[-1]["at"],
            "user_name": names.get(user_id) or f"User {user_id[:8]}",
        },
    }


@router.get(
    "/assistant-admin/conversations",
    summary="Paginated conversation list for the assistant admin dashboard",
    dependencies=[Depends(_require_service_token)],
)
async def assistant_admin_conversations(
    db: DbSession,
    page: int = Query(default=1, ge=1, description="1-based page number."),
    limit: int = Query(default=20, ge=1, le=_MAX_LIMIT),
    q: str | None = Query(
        default=None, description="Case-insensitive match on the questions asked."
    ),
    days: int = Query(
        default=30, ge=1, le=365, description="Look-back window in days."
    ),
) -> dict[str, Any]:
    """Return one page of recent conversations."""
    now = datetime.now(timezone.utc)
    repo = AnalyticsRepository(db)
    filters = AnalyticsFilters(from_date=now - timedelta(days=days), to_date=now)

    try:
        # The list shows a session id, a person's name and the questions asked,
        # so the search covers all three. Names are not in the chat tables —
        # they come from the platform directory — so they are resolved to user
        # ids here and matched in SQL alongside the rest. Paging stays in the
        # database rather than slicing a capped list in memory.
        user_ids = await find_user_ids(q) if q else []
        sessions, total = await repo.recent_sessions(
            filters,
            search=q,
            user_ids=user_ids,
            limit=limit,
            offset=(page - 1) * limit,
        )
    except Exception as exc:
        log.exception("dashboard.assistant_admin_conversations.failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not load conversations. Please try again.",
        ) from exc

    names = await _resolve_user_names(db, [s["user_id"] for s in sessions if s["user_id"]])

    return {
        "success": True,
        "data": {
            "items": [_conversation_row(s, names, now) for s in sessions],
            "total": total,
            "page": page,
            "limit": limit,
        },
    }
