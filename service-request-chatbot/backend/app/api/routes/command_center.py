"""AI Command Center API (ticket 3784).

    GET /dashboard/ai-command-center/overview      — AI Dashboard (executive)
    GET /dashboard/ai-command-center/usage         — AI Analytics (usage & engagement)
    GET /dashboard/ai-command-center/performance   — AI Performance (quality & reliability)
    GET /dashboard/ai-command-center/productivity  — AI Productivity (automation & efficiency)

One contract for the four sections of the client's readiness reference.  KPI
definitions are the K-numbers in ``docs/ai-command-center-full-plan.md`` §3.

Contract rules
--------------
* ``{success, data}`` envelope and ``x-internal-api-token`` guard — the same as
  ``/dashboard/assistant-admin`` — so the tenant frontend's existing client
  works unchanged.
* Every ratio is ``{value, numerator, denominator}``.  ``value`` is ``null``
  when the denominator is zero: an empty window has no rate, and the page
  must not show 0% as if it had been measured.
* Headline figures carry ``previous`` (the same-length window just before)
  and ``delta``.
* Answer Rate always ships with its classification coverage; LLM cost with
  its pricing coverage.  Neither number is safe to read alone.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.routes.dashboard import _resolve_user_names
from app.api.routes.msp_chat import _require_service_token
from app.db.session import DbSession
from app.observability.repositories.analytics_repo import AnalyticsFilters
from app.observability.analytics_access import require_analytics_viewer
from app.observability.platform_stats import ai_share_of_service_requests
from app.observability.repositories.command_center_repo import CommandCenterRepository

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/dashboard/ai-command-center", tags=["ai-command-center"])

DEFAULT_WINDOW_DAYS = 30
MAX_WINDOW_DAYS = 365
_MAX_PAGE_SIZE = 100

# Dashboards poll and several tabs share a window; 60 s keeps repeat loads off
# the database without letting a fresh SR take more than a minute to appear.
_CACHE_TTL_SECONDS = 60.0
_CACHE_MAX_ENTRIES = 256
_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}


# ---------------------------------------------------------------------------
# Window + response helpers
# ---------------------------------------------------------------------------


def resolve_window(
    from_date: datetime | None, to_date: datetime | None, *, now: datetime | None = None
) -> tuple[datetime, datetime]:
    """Validate the requested window, defaulting to the trailing 30 days.

    A defaulted ``to`` is truncated to the minute so repeated loads share a
    cache entry.
    """
    current = now or datetime.now(timezone.utc)
    end = to_date or current.replace(second=0, microsecond=0)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = from_date or (end - timedelta(days=DEFAULT_WINDOW_DAYS))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if start >= end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'from' must be earlier than 'to'.",
        )
    if end - start > timedelta(days=MAX_WINDOW_DAYS):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"The window can be at most {MAX_WINDOW_DAYS} days.",
        )
    return start, end


def previous_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """The window of the same length that ends where this one starts."""
    return start - (end - start), start


def ratio(numerator: float, denominator: float) -> dict[str, Any]:
    """A rate with its inputs; ``value`` is ``None`` when nothing was measured."""
    return {
        "value": round(numerator / denominator, 4) if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
    }


def with_delta(current: float | None, previous: float | None) -> dict[str, Any]:
    """A headline figure beside the same figure for the previous window."""
    delta = (
        round(current - previous, 4)
        if current is not None and previous is not None
        else None
    )
    return {"value": current, "previous": previous, "delta": delta}


def _envelope(data: dict[str, Any]) -> dict[str, Any]:
    return {"success": True, "data": data}


def _window_payload(start: datetime, end: datetime, agent: str | None) -> dict[str, Any]:
    prev_start, prev_end = previous_window(start, end)
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "agent": agent,
        "previous": {"from": prev_start.isoformat(), "to": prev_end.isoformat()},
    }


async def _cached(
    key: tuple[Any, ...], build: Callable[[], Awaitable[dict[str, Any]]]
) -> dict[str, Any]:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and hit[0] > now:
        return hit[1]
    try:
        payload = await build()
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("command_center.build_failed", section=key[0], error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not build this section. Please try again.",
        ) from exc
    if len(_cache) >= _CACHE_MAX_ENTRIES:
        for stale in [k for k, (expires, _) in _cache.items() if expires <= now]:
            _cache.pop(stale, None)
        if len(_cache) >= _CACHE_MAX_ENTRIES:
            _cache.clear()
    _cache[key] = (now + _CACHE_TTL_SECONDS, payload)
    return payload


def clear_cache() -> None:
    """Drop cached sections — used by tests and after a backfill."""
    _cache.clear()


def _window_query(
    from_date: datetime | None = Query(
        default=None, alias="from", description="Window start (ISO-8601). Default: 30 days before 'to'."
    ),
    to_date: datetime | None = Query(
        default=None, alias="to", description="Window end (ISO-8601). Default: now."
    ),
    agent: str | None = Query(
        default=None, description="Restrict to one agent, e.g. rdd_handover_agent."
    ),
) -> AnalyticsFilters:
    start, end = resolve_window(from_date, to_date)
    return AnalyticsFilters(from_date=start, to_date=end, agent=agent or None)


def _previous(filters: AnalyticsFilters) -> AnalyticsFilters:
    assert filters.from_date is not None and filters.to_date is not None
    start, end = previous_window(filters.from_date, filters.to_date)
    return AnalyticsFilters(from_date=start, to_date=end, agent=filters.agent)


# ---------------------------------------------------------------------------
# Shared KPI blocks
# ---------------------------------------------------------------------------


def answer_rate_block(totals: dict[str, Any]) -> dict[str, Any]:
    """K8 Answer Rate with K8 coverage and K9 Escalation Rate, from turn totals."""
    classified = totals["classified"]
    return {
        "answer_rate": ratio(totals["answered"], classified),
        "classification_coverage": ratio(classified, totals["questions"]),
        "escalation_rate": ratio(totals["unanswered"] + totals["failed"], classified),
        "answer_status_mix": {
            "answered": totals["answered"],
            "partial": totals["partial"],
            "unanswered": totals["unanswered"],
            "failed": totals["failed"],
            "unclassified": totals["questions"] - classified,
        },
    }


def tool_reliability_block(tools: list[dict[str, Any]]) -> dict[str, Any]:
    """K16 overall success rate across every captured platform call."""
    calls = sum(t["calls"] for t in tools)
    failed = sum(t["failed"] for t in tools)
    return ratio(calls - failed, calls)


def rating_sentiment(distribution: dict[str, int]) -> dict[str, Any]:
    """K12 positive (4–5 stars) and negative (1–2 stars) shares of ratings."""
    rated = sum(distribution.values())
    positive = distribution.get("4", 0) + distribution.get("5", 0)
    negative = distribution.get("1", 0) + distribution.get("2", 0)
    return {
        "positive": ratio(positive, rated),
        "negative": ratio(negative, rated),
        "neutral": ratio(distribution.get("3", 0), rated),
    }


def thumbs_up_rate(thumbs: dict[str, Any]) -> dict[str, Any]:
    """Share of 👍 among all thumbs votes on replies in the window."""
    return ratio(thumbs["up"], thumbs["up"] + thumbs["down"])


def cost_block(llm: dict[str, Any], total_cost: float) -> dict[str, Any]:
    """K18 LLM cost with the share of calls that could be priced."""
    return {
        "total_usd": round(total_cost, 6),
        "total_tokens": llm["total_tokens"],
        "llm_calls": llm["total_calls"],
        "pricing_coverage": llm["cost_coverage"],
    }


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


@router.get(
    "/overview",
    summary="AI Dashboard — executive health for the window",
    dependencies=[Depends(_require_service_token), Depends(require_analytics_viewer)],
)
async def command_center_overview(
    db: DbSession, filters: AnalyticsFilters = Depends(_window_query)
) -> dict[str, Any]:
    """Ten KPI cards, usage and answer trends, SR funnel, agent and tool health."""

    async def build() -> dict[str, Any]:
        repo = CommandCenterRepository(db)
        prev = _previous(filters)

        totals = await repo.turn_totals(filters)
        prev_totals = await repo.turn_totals(prev)
        users = await repo.user_activity(filters)
        prev_users = await repo.user_activity(prev)
        ratings = await repo.rating_totals(filters)
        prev_ratings = await repo.rating_totals(prev)
        latency = await repo.latency_percentiles(filters)
        prev_latency = await repo.latency_percentiles(prev)
        srs = await repo.sr_outcomes(filters)
        prev_srs = await repo.sr_outcomes(prev)
        tools = await repo.tool_health(filters)
        llm = await repo.llm_usage(filters)
        thumbs = await repo.thumbs_totals(filters)
        prev_thumbs = await repo.thumbs_totals(prev)

        answers = answer_rate_block(totals)
        prev_answers = answer_rate_block(prev_totals)

        return _envelope(
            {
                "window": _window_payload(filters.from_date, filters.to_date, filters.agent),
                "kpis": {
                    "active_users": with_delta(users["active_users"], prev_users["active_users"]),
                    "interactions": with_delta(totals["questions"], prev_totals["questions"]),
                    "srs_created": with_delta(srs["srs_created"], prev_srs["srs_created"]),
                    "answer_rate": {
                        **answers["answer_rate"],
                        "previous": prev_answers["answer_rate"]["value"],
                        "coverage": answers["classification_coverage"],
                    },
                    "avg_rating": {
                        **with_delta(ratings["avg_rating"], prev_ratings["avg_rating"]),
                        "ratings": ratings["ratings_count"],
                    },
                    "thumbs_up_rate": {
                        **thumbs_up_rate(thumbs),
                        "previous": thumbs_up_rate(prev_thumbs)["value"],
                    },
                    "sr_completion_rate": ratio(srs["srs_completed"], srs["srs_created"]),
                    "p50_latency_ms": with_delta(latency["p50"], prev_latency["p50"]),
                    "p95_latency_ms": with_delta(latency["p95"], prev_latency["p95"]),
                    "llm_cost": cost_block(llm, totals["total_cost"]),
                    "tool_reliability": tool_reliability_block(tools),
                    "escalation_rate": {
                        **answers["escalation_rate"],
                        "previous": prev_answers["escalation_rate"]["value"],
                    },
                },
                "usage_trend": await repo.daily_series(filters),
                "answer_trend": await repo.answer_trend(filters),
                "hourly_sessions": await repo.hourly_sessions(filters),
                "sr_funnel": await repo.workflow_funnel(filters),
                "agent_health": await repo.agent_scorecard(filters),
                "tool_reliability": tools,
            }
        )

    return await _cached(("overview", *_key(filters)), build)


@router.get(
    "/usage",
    summary="AI Analytics — who uses the assistant, for what, and how much",
    dependencies=[Depends(_require_service_token), Depends(require_analytics_viewer)],
)
async def command_center_usage(
    db: DbSession, filters: AnalyticsFilters = Depends(_window_query)
) -> dict[str, Any]:
    """Users, sessions, engagement, agent and intent mix, workflow usage."""

    async def build() -> dict[str, Any]:
        repo = CommandCenterRepository(db)
        users = await repo.user_activity(filters)
        engagement = await repo.engagement(filters)
        contained = await repo.containment(filters)
        totals = await repo.turn_totals(filters)
        active = users["active_users"]

        return _envelope(
            {
                "window": _window_payload(filters.from_date, filters.to_date, filters.agent),
                "kpis": {
                    "active_users": active,
                    "new_users": users["new_users"],
                    "repeat_user_rate": ratio(users["repeat_users"], active),
                    "interactions": users["questions"],
                    "interactions_per_user": ratio(users["questions"], active),
                    "sessions": totals["sessions"],
                    "turns_per_session": engagement["avg_turns_per_session"],
                    "avg_session_seconds": engagement["avg_session_seconds"],
                    "median_session_seconds": engagement["median_session_seconds"],
                    "containment_rate": ratio(contained["contained"], contained["sessions"]),
                },
                "daily": await repo.daily_series(filters),
                "by_agent": await repo.agent_breakdown(filters),
                "by_intent": await repo.intent_distribution(filters),
                "by_function": await repo.function_distribution(filters),
                "workflow_funnel": await repo.workflow_funnel(filters),
                "hourly_sessions": await repo.hourly_sessions(filters),
                "top_questions": await repo.top_questions(filters, limit=10),
                "top_users": await _named(db, await repo.top_users(filters, limit=10)),
                "by_mall": await repo.claim_distribution(filters, dimension="mall"),
                "by_role": await repo.claim_distribution(filters, dimension="role"),
                "by_channel": await repo.channel_distribution(filters),
            }
        )

    return await _cached(("usage", *_key(filters)), build)


@router.get(
    "/performance",
    summary="AI Performance — answer quality, feedback, latency and reliability",
    dependencies=[Depends(_require_service_token), Depends(require_analytics_viewer)],
)
async def command_center_performance(
    db: DbSession,
    filters: AnalyticsFilters = Depends(_window_query),
    max_score: int | None = Query(
        default=None, ge=1, le=5, description="Comment feed: only ratings at or below this."
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=_MAX_PAGE_SIZE),
) -> dict[str, Any]:
    """Answer and rating quality, latency percentiles, tool and LLM health."""

    async def build() -> dict[str, Any]:
        repo = CommandCenterRepository(db)
        totals = await repo.turn_totals(filters)
        ratings = await repo.rating_totals(filters)
        participation = await repo.feedback_participation(filters)
        thumbs = await repo.thumbs_totals(filters)
        tools = await repo.tool_health(filters)
        llm = await repo.llm_usage(filters)
        offset = (page - 1) * page_size
        comments, comments_total = await repo.feedback_comments(
            filters, max_score=max_score, limit=page_size, offset=offset
        )
        answers = answer_rate_block(totals)
        parse_failures = sum(p["parse_failures"] for p in llm["by_prompt"])

        return _envelope(
            {
                "window": _window_payload(filters.from_date, filters.to_date, filters.agent),
                "kpis": {
                    "answer_rate": answers["answer_rate"],
                    "classification_coverage": answers["classification_coverage"],
                    "escalation_rate": answers["escalation_rate"],
                    "avg_rating": ratings["avg_rating"],
                    "ratings": ratings["ratings_count"],
                    **rating_sentiment(ratings["distribution"]),
                    "feedback_participation": ratio(
                        participation["rated_sessions"], participation["sessions"]
                    ),
                    "thumbs_up_rate": thumbs_up_rate(thumbs),
                    "thumbs_participation": ratio(thumbs["voted_replies"], thumbs["replies"]),
                    "tool_reliability": tool_reliability_block(tools),
                    "llm_parse_failure_rate": ratio(parse_failures, llm["total_calls"]),
                    "latency_ms": await repo.latency_percentiles(filters),
                    "failed_turns": ratio(totals["failed_traces"], totals["total_turns"]),
                },
                "answer_status_mix": answers["answer_status_mix"],
                "answer_trend": await repo.answer_trend(filters),
                "rating_distribution": ratings["distribution"],
                "ratings_by_source": ratings["by_source"],
                "rating_trend": [
                    {"date": d["date"], "ratings": d["ratings"], "avg_rating": d["avg_rating"]}
                    for d in await repo.daily_series(filters)
                ],
                "thumbs": {
                    "up": thumbs["up"],
                    "down": thumbs["down"],
                    "by_source": thumbs["by_source"],
                },
                "thumbs_trend": await repo.thumbs_daily(filters),
                "latency_trend": await repo.latency_daily(filters),
                "thumbs_by_function": await repo.thumbs_by_function(filters),
                "downvote_reasons": await repo.downvote_reasons(filters),
                "downvoted_replies": await repo.downvoted_replies(filters, limit=10),
                "agent_scorecard": await repo.agent_scorecard(filters),
                "node_latency": await repo.node_latency(filters),
                "tool_health": tools,
                "llm_usage": llm,
                "failure_reasons": await repo.failure_reasons(filters),
                "comments": {
                    "items": comments,
                    "total": comments_total,
                    "page": page,
                    "page_size": page_size,
                    "has_next": offset + len(comments) < comments_total,
                },
            }
        )

    return await _cached(
        ("performance", *_key(filters), max_score, page, page_size), build
    )


@router.get(
    "/productivity",
    summary="AI Productivity — service requests the assistant created and moved",
    dependencies=[Depends(_require_service_token), Depends(require_analytics_viewer)],
)
async def command_center_productivity(
    db: DbSession,
    filters: AnalyticsFilters = Depends(_window_query),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=_MAX_PAGE_SIZE),
) -> dict[str, Any]:
    """SR volume, submission and completion, automation mix, decisions, cost."""

    async def build() -> dict[str, Any]:
        repo = CommandCenterRepository(db)
        srs = await repo.sr_outcomes(filters)
        mix = await repo.automation_mix(filters)
        submitted_cost = await repo.submitted_cost(filters)
        totals = await repo.turn_totals(filters)
        llm = await repo.llm_usage(filters)
        offset = (page - 1) * page_size
        log_items, log_total = await repo.sr_log(filters, limit=page_size, offset=offset)
        decisions = srs["approvals"] + srs["rejections"]
        platform = await ai_share_of_service_requests(filters.from_date, filters.to_date)

        return _envelope(
            {
                "window": _window_payload(filters.from_date, filters.to_date, filters.agent),
                "kpis": {
                    "srs_created": srs["srs_created"],
                    "sr_submission_rate": ratio(
                        srs["create_intent_submitted"], srs["create_intent_sessions"]
                    ),
                    "sr_completion_rate": ratio(srs["srs_completed"], srs["srs_created"]),
                    "automated_srs": mix["automated"],
                    "assisted_srs": mix["assisted"],
                    "human_intervention_rate": ratio(mix["assisted"], mix["submitted_sessions"]),
                    "correction_rate": ratio(mix["corrected"], mix["submitted_sessions"]),
                    "approvals": srs["approvals"],
                    "rejections": srs["rejections"],
                    "approval_rate": ratio(srs["approvals"], decisions),
                    "cost_per_sr_usd": ratio(round(submitted_cost, 6), srs["srs_created"]),
                    # Of SRs created on the platform in the assistant's flows,
                    # the share the assistant created (platform created_via).
                    # None when the platform data is unavailable.
                    "ai_share_of_srs": (
                        ratio(platform["ai"], platform["total"]) if platform else None
                    ),
                    "llm_cost": cost_block(llm, totals["total_cost"]),
                },
                "automation_mix": mix,
                "decisions_by_flow": srs["decisions_by_flow"],
                "decision_times": await repo.decision_times(filters),
                "decision_step_times": await repo.decision_step_times(filters),
                "by_function": await repo.productivity_by_function(filters),
                "platform_srs_by_function": platform["by_function"] if platform else [],
                "tasks_daily": await repo.tasks_daily(filters),
                "by_agent": await repo.productivity_by_agent(filters),
                "funnel": await repo.workflow_funnel(filters),
                "sr_log": {
                    "items": await _named(db, log_items),
                    "total": log_total,
                    "page": page,
                    "page_size": page_size,
                    "has_next": offset + len(log_items) < log_total,
                },
            }
        )

    return await _cached(("productivity", *_key(filters), page, page_size), build)


async def _named(db: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add ``user_name`` to rows carrying ``user_id`` — platform name, else a short id."""
    names = await _resolve_user_names(db, [r["user_id"] for r in rows if r.get("user_id")])
    return [
        {
            **row,
            "user_name": names.get(row.get("user_id") or "")
            or (f"User {row['user_id'][:8]}" if row.get("user_id") else ""),
        }
        for row in rows
    ]


def _key(filters: AnalyticsFilters) -> tuple[Any, ...]:
    return (
        filters.from_date.isoformat() if filters.from_date else None,
        filters.to_date.isoformat() if filters.to_date else None,
        filters.agent,
    )
