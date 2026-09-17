"""AI Command Center (ticket 3784) — contract helpers, SQL, and route bodies.

* Helpers: window validation, ratio / delta shapes, the answer-rate block.
* SQL: every ``CommandCenterRepository`` statement compiles against the
  PostgreSQL dialect (same technique as ``test_analytics_repo``).
* Routes: each handler runs end to end against a stubbed repository, so an
  undefined name or wrong key fails here rather than on the dashboard.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.api.routes import command_center as cc
from app.observability.repositories.analytics_repo import AnalyticsFilters
from app.observability.repositories.command_center_repo import CommandCenterRepository

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
WINDOW = AnalyticsFilters(from_date=NOW - timedelta(days=30), to_date=NOW)
SCOPED = AnalyticsFilters(
    from_date=NOW - timedelta(days=30), to_date=NOW, agent="rdd_handover_agent", user_id=uuid4()
)


# ── Window ─────────────────────────────────────────────────────────────────


def test_default_window_is_trailing_30_days_truncated_to_the_minute() -> None:
    now = NOW.replace(second=41, microsecond=7)
    start, end = cc.resolve_window(None, None, now=now)
    assert end == NOW
    assert end - start == timedelta(days=30)


def test_naive_bounds_are_treated_as_utc() -> None:
    start, end = cc.resolve_window(datetime(2026, 9, 1), datetime(2026, 9, 2))
    assert start.tzinfo is timezone.utc and end.tzinfo is timezone.utc


@pytest.mark.parametrize(
    "start,end",
    [
        (NOW, NOW - timedelta(days=1)),
        (NOW, NOW),
        (NOW - timedelta(days=366), NOW),
    ],
)
def test_invalid_windows_are_rejected(start: datetime, end: datetime) -> None:
    with pytest.raises(HTTPException) as exc:
        cc.resolve_window(start, end)
    assert exc.value.status_code == 422


def test_previous_window_has_the_same_length_and_abuts() -> None:
    start, end = NOW - timedelta(days=7), NOW
    prev_start, prev_end = cc.previous_window(start, end)
    assert prev_end == start
    assert prev_end - prev_start == end - start


# ── Shapes ─────────────────────────────────────────────────────────────────


def test_ratio_has_no_value_when_nothing_was_measured() -> None:
    assert cc.ratio(0, 0) == {"value": None, "numerator": 0, "denominator": 0}
    assert cc.ratio(1, 3) == {"value": 0.3333, "numerator": 1, "denominator": 3}


def test_with_delta_handles_missing_sides() -> None:
    assert cc.with_delta(10, 4) == {"value": 10, "previous": 4, "delta": 6}
    assert cc.with_delta(4.5, None)["delta"] is None


def _totals(**overrides):
    base = {
        "total_turns": 12, "questions": 10, "answered": 7, "partial": 1,
        "unanswered": 1, "failed": 1, "classified": 10, "failed_traces": 1,
        "success_rate": 0.7, "failure_rate": 0.2, "sessions": 4,
        "unique_users": 2, "avg_latency_ms": 900, "total_tokens": 1000,
        "total_cost": 0.5,
    }
    base.update(overrides)
    return base


def test_answer_rate_block_uses_classified_questions() -> None:
    block = cc.answer_rate_block(_totals(classified=8, questions=10))
    assert block["answer_rate"] == {"value": 0.875, "numerator": 7, "denominator": 8}
    assert block["classification_coverage"]["value"] == 0.8
    # UNANSWERED + FAILED — the questions the assistant could not answer.
    assert block["escalation_rate"]["numerator"] == 2
    assert block["answer_status_mix"]["unclassified"] == 2


def test_rating_sentiment_splits_stars() -> None:
    out = cc.rating_sentiment({"1": 1, "2": 1, "3": 2, "4": 3, "5": 3})
    assert out["positive"]["value"] == 0.6
    assert out["negative"]["value"] == 0.2
    assert out["neutral"]["value"] == 0.2


def test_tool_reliability_block_is_weighted_by_calls() -> None:
    tools = [{"calls": 90, "failed": 0}, {"calls": 10, "failed": 5}]
    assert cc.tool_reliability_block(tools)["value"] == 0.95
    assert cc.tool_reliability_block([])["value"] is None


# ── SQL compiles ───────────────────────────────────────────────────────────


class CompilingSession:
    """Compiles each statement for Postgres, then returns one canned row."""

    def __init__(self, row: object, rows: list[object] | None = None) -> None:
        self._row = row
        self._rows = rows or []
        self.compiled: list[str] = []

    async def execute(self, statement):
        self.compiled.append(
            str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        )
        result = MagicMock()
        result.one.return_value = self._row
        result.all.return_value = self._rows
        result.scalar_one.return_value = 0
        result.__iter__ = lambda _self: iter(self._rows)
        return result


def _zero_row() -> SimpleNamespace:
    names = [
        "p50", "p95", "p99", "active", "new", "repeat", "questions", "sessions",
        "contained", "rated", "create_intent", "create_intent_submitted", "srs",
        "completed", "submitted", "assisted", "corrected", "avg_seconds",
        "median_seconds", "total_seconds", "avg_turns", "total_questions",
        "not_recorded", "multiple", "calls", "failed", "avg_ms", "p95_ms",
    ]
    return SimpleNamespace(**{n: 0 for n in names})


@pytest.mark.parametrize("filters", [WINDOW, SCOPED], ids=["unscoped", "agent+user"])
@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("latency_percentiles", {}),
        ("user_activity", {}),
        ("containment", {}),
        ("feedback_participation", {}),
        ("answer_trend", {}),
        ("intent_distribution", {}),
        ("channel_distribution", {}),
        ("claim_distribution", {"dimension": "mall"}),
        ("claim_distribution", {"dimension": "role"}),
        ("function_distribution", {}),
        ("productivity_by_function", {}),
        ("decision_step_times", {}),
        ("failure_reasons", {}),
        ("agent_scorecard", {}),
        ("sr_outcomes", {}),
        ("automation_mix", {}),
        ("tasks_daily", {}),
        ("submitted_cost", {}),
        ("productivity_by_agent", {}),
        ("decision_times", {}),
        ("sr_log", {"limit": 5}),
        ("node_latency", {}),
        ("tool_health", {}),
        ("llm_usage", {}),
        ("engagement", {}),
    ],
)
async def test_every_query_compiles(method: str, kwargs: dict, filters: AnalyticsFilters) -> None:
    session = CompilingSession(row=_zero_row())
    await getattr(CommandCenterRepository(session), method)(filters, **kwargs)
    assert session.compiled, f"{method} compiled nothing"


async def test_agent_scope_reaches_trace_child_tables() -> None:
    session = CompilingSession(row=_zero_row())
    await CommandCenterRepository(session).tool_health(SCOPED)
    assert "rdd_handover_agent" in session.compiled[0]


async def test_decision_times_group_by_reuses_one_sr_id_expression() -> None:
    """Rebuilding ``->> 'sr_id'`` in GROUP BY binds a second parameter that
    Postgres refuses to match to the select list (GroupingError on live data)."""
    session = CompilingSession(row=_zero_row())
    await CommandCenterRepository(session).decision_times(WINDOW)
    sql = session.compiled[0]
    assert "GROUP BY analytics_events.properties ->> 'sr_id'" in sql


# ── Route bodies ───────────────────────────────────────────────────────────


def _stub_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.turn_totals = AsyncMock(return_value=_totals())
    repo.thumbs_totals = AsyncMock(return_value={
        "replies": 20, "voted_replies": 8, "up": 6, "down": 2,
        "by_source": {"help_answer": {"up": 4, "down": 1},
                      "sr_step": {"up": 1, "down": 1},
                      "sr_created": {"up": 1, "down": 0}},
    })
    repo.thumbs_daily = AsyncMock(return_value=[{"date": "2026-09-16", "up": 6, "down": 2}])
    repo.thumbs_by_function = AsyncMock(
        return_value=[{"function": "handover", "replies": 12, "up": 4, "down": 2}]
    )
    repo.downvoted_replies = AsyncMock(return_value=[])
    repo.user_activity = AsyncMock(
        return_value={"active_users": 4, "new_users": 1, "repeat_users": 2, "questions": 10}
    )
    repo.rating_totals = AsyncMock(return_value={
        "ratings_count": 2, "avg_rating": 4.5, "comments_count": 1,
        "distribution": {"1": 0, "2": 0, "3": 0, "4": 1, "5": 1},
        "by_source": {"help_answer": {"count": 2, "avg_rating": 4.5},
                      "sr_created": {"count": 0, "avg_rating": None}},
    })
    repo.latency_percentiles = AsyncMock(return_value={"p50": 900, "p95": 4000, "p99": 9000})
    repo.sr_outcomes = AsyncMock(return_value={
        "srs_created": 3, "srs_completed": 1, "submitted_sessions": 3,
        "create_intent_sessions": 5, "create_intent_submitted": 3,
        "approvals": 2, "rejections": 1,
        "decisions_by_flow": [{"flow": "work_permit", "approved": 2, "rejected": 1}],
    })
    repo.tool_health = AsyncMock(return_value=[
        {"tool": "lease_tenant_api", "tool_label": "Lease lookup", "calls": 10,
         "failed": 1, "success_rate": 0.9, "avg_ms": 300, "p95_ms": 600}
    ])
    repo.llm_usage = AsyncMock(return_value={
        "by_prompt": [{"prompt": "faq_prompt", "parse_failures": 1}],
        "total_calls": 20, "total_tokens": 5000, "cost_coverage": 0.5,
    })
    repo.daily_series = AsyncMock(return_value=[
        {"date": "2026-09-15", "turns": 4, "answered": 3, "not_answered": 1,
         "answered_rate": 0.75, "sessions": 2, "avg_latency_ms": 800,
         "ratings": 1, "avg_rating": 4.0}
    ])
    repo.answer_trend = AsyncMock(return_value=[])
    repo.hourly_sessions = AsyncMock(return_value=[0] * 24)
    repo.workflow_funnel = AsyncMock(return_value=[])
    repo.agent_scorecard = AsyncMock(return_value=[])
    repo.engagement = AsyncMock(return_value={
        "sessions": 4, "avg_session_seconds": 120, "avg_session_minutes": 2.0,
        "median_session_seconds": 100, "total_time_spent_minutes": 8.0,
        "avg_turns_per_session": 2.5, "total_questions": 10,
    })
    repo.containment = AsyncMock(return_value={"sessions": 4, "contained": 3})
    repo.agent_breakdown = AsyncMock(return_value=[])
    repo.intent_distribution = AsyncMock(return_value=[])
    repo.claim_distribution = AsyncMock(
        return_value={
            "items": [{"key": "Nakheel Mall", "questions": 5, "sessions": 2, "users": 1}],
            "not_recorded_questions": 3,
            "multiple_questions": 2,
        }
    )
    repo.function_distribution = AsyncMock(
        return_value=[{"function": "handover", "questions": 4, "sessions": 2, "users": 1,
                       "classified": 4, "answered": 4}]
    )
    repo.productivity_by_function = AsyncMock(
        return_value=[{"function": "handover", "srs_created": 3, "srs_completed": 1}]
    )
    repo.decision_step_times = AsyncMock(return_value=[])
    repo.channel_distribution = AsyncMock(
        return_value=[{"channel": "copilot_rail", "questions": 6, "sessions": 3, "users": 2}]
    )
    repo.top_questions = AsyncMock(return_value=[])
    repo.top_users = AsyncMock(return_value=[])
    repo.feedback_participation = AsyncMock(return_value={"sessions": 4, "rated_sessions": 1})
    repo.feedback_comments = AsyncMock(return_value=([], 0))
    repo.node_latency = AsyncMock(return_value=[])
    repo.failure_reasons = AsyncMock(return_value=[])
    repo.automation_mix = AsyncMock(return_value={
        "submitted_sessions": 3, "automated": 2, "assisted": 1, "corrected": 1, "failed": 2,
    })
    repo.submitted_cost = AsyncMock(return_value=0.09)
    repo.sr_log = AsyncMock(return_value=([], 0))
    repo.decision_times = AsyncMock(return_value=[])
    repo.tasks_daily = AsyncMock(return_value=[])
    repo.productivity_by_agent = AsyncMock(return_value=[])
    return repo


@pytest.fixture(autouse=True)
def _fresh_cache():
    cc.clear_cache()
    yield
    cc.clear_cache()


async def _call(handler, platform=None, **kwargs):
    with (
        patch.object(cc, "CommandCenterRepository", return_value=_stub_repo()),
        patch.object(cc, "_resolve_user_names", new=AsyncMock(return_value={})),
        patch.object(cc, "ai_share_of_service_requests", new=AsyncMock(return_value=platform)),
    ):
        return await handler(db=AsyncMock(), filters=WINDOW, **kwargs)


async def test_overview_route_runs() -> None:
    body = await _call(cc.command_center_overview)
    kpis = body["data"]["kpis"]
    assert body["success"] is True
    assert set(kpis) == {
        "active_users", "interactions", "srs_created", "answer_rate", "avg_rating",
        "sr_completion_rate", "p95_latency_ms", "llm_cost", "tool_reliability",
        "escalation_rate", "thumbs_up_rate",
    }
    assert kpis["thumbs_up_rate"]["value"] == 0.75
    assert kpis["thumbs_up_rate"]["previous"] == 0.75
    assert kpis["answer_rate"]["coverage"]["denominator"] == 10
    assert kpis["llm_cost"]["pricing_coverage"] == 0.5
    assert kpis["sr_completion_rate"]["value"] == 0.3333


async def test_usage_route_runs() -> None:
    data = (await _call(cc.command_center_usage))["data"]
    kpis = data["kpis"]
    assert data["by_channel"][0]["channel"] == "copilot_rail"
    assert data["by_mall"]["items"][0]["key"] == "Nakheel Mall"
    assert data["by_role"]["not_recorded_questions"] == 3
    assert data["by_function"][0]["function"] == "handover"
    assert kpis["repeat_user_rate"]["value"] == 0.5
    assert kpis["containment_rate"]["value"] == 0.75


async def test_performance_route_runs() -> None:
    body = await _call(cc.command_center_performance, max_score=None, page=1, page_size=20)
    kpis = body["data"]["kpis"]
    assert kpis["positive"]["value"] == 1.0
    assert kpis["feedback_participation"]["value"] == 0.25
    assert kpis["thumbs_up_rate"] == {"value": 0.75, "numerator": 6, "denominator": 8}
    assert kpis["thumbs_participation"]["value"] == 0.4
    assert body["data"]["thumbs"]["by_source"]["sr_step"] == {"up": 1, "down": 1}
    assert body["data"]["thumbs_by_function"][0]["function"] == "handover"
    assert body["data"]["thumbs_trend"][0]["up"] == 6
    assert kpis["llm_parse_failure_rate"]["value"] == 0.05
    assert body["data"]["comments"]["has_next"] is False


async def test_productivity_route_runs() -> None:
    kpis = (await _call(cc.command_center_productivity, page=1, page_size=20))["data"]["kpis"]
    assert kpis["sr_submission_rate"]["value"] == 0.6
    assert kpis["approval_rate"]["value"] == 0.6667
    assert kpis["human_intervention_rate"]["value"] == 0.3333
    assert kpis["cost_per_sr_usd"]["value"] == 0.03
    # Platform unavailable (not migrated / not configured): no share, not 0%.
    assert kpis["ai_share_of_srs"] is None


async def test_productivity_reports_ai_share_from_the_platform() -> None:
    platform = {
        "total": 20,
        "ai": 5,
        "by_function": [{"function": "handover", "total": 20, "ai": 5}],
    }
    data = (
        await _call(cc.command_center_productivity, platform=platform, page=1, page_size=20)
    )["data"]
    assert data["kpis"]["ai_share_of_srs"] == {"value": 0.25, "numerator": 5, "denominator": 20}
    assert data["platform_srs_by_function"][0]["ai"] == 5
    assert data["by_function"][0]["function"] == "handover"


async def test_rows_get_a_display_name() -> None:
    names = {"u-1": "Aisha Khan"}
    with patch.object(cc, "_resolve_user_names", new=AsyncMock(return_value=names)):
        rows = await cc._named(AsyncMock(), [{"user_id": "u-1"}, {"user_id": "abcdef123456"}])
    assert [r["user_name"] for r in rows] == ["Aisha Khan", "User abcdef12"]


async def test_sections_are_cached_per_window() -> None:
    repo = _stub_repo()
    with (
        patch.object(cc, "CommandCenterRepository", return_value=repo),
        patch.object(cc, "_resolve_user_names", new=AsyncMock(return_value={})),
    ):
        await cc.command_center_usage(db=AsyncMock(), filters=WINDOW)
        await cc.command_center_usage(db=AsyncMock(), filters=WINDOW)
        await cc.command_center_usage(db=AsyncMock(), filters=SCOPED)
    assert repo.user_activity.await_count == 2


async def test_query_failure_becomes_a_500_not_a_traceback() -> None:
    repo = _stub_repo()
    repo.turn_totals = AsyncMock(side_effect=RuntimeError("db down"))
    with (
        patch.object(cc, "CommandCenterRepository", return_value=repo),
        pytest.raises(HTTPException) as exc,
    ):
        await cc.command_center_usage(db=AsyncMock(), filters=WINDOW)
    assert exc.value.status_code == 500


def test_thumbs_up_rate_block() -> None:
    assert cc.thumbs_up_rate({"up": 3, "down": 1})["value"] == 0.75
    # No votes: nothing measured, not 0%.
    assert cc.thumbs_up_rate({"up": 0, "down": 0})["value"] is None
