"""Unit tests for the analytics repository.

Every method is exercised through a session stub that **compiles each
statement against the PostgreSQL dialect** before returning canned rows.  That
catches malformed SQL construction (wrong cast, bad label, unbound subquery)
without needing a live database — the failure mode that mocked-session tests
normally sail straight past.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.observability.repositories.analytics_repo import (
    AnalyticsFilters,
    AnalyticsRepository,
)

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
WINDOW = AnalyticsFilters(from_date=NOW - timedelta(days=30), to_date=NOW)


class CompilingSession:
    """Session stub that compiles every statement, then returns canned rows."""

    def __init__(self, row: object = None, rows: list[object] | None = None) -> None:
        self._row = row
        self._rows = rows or []
        self.compiled: list[str] = []

    async def execute(self, statement):  # noqa: ANN001 — mirrors AsyncSession
        # literal_binds forces every parameter through the dialect too.
        self.compiled.append(
            str(
                statement.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
        )
        result = MagicMock()
        result.one.return_value = self._row
        result.scalar_one.return_value = 0
        result.scalar_one_or_none.return_value = None
        result.__iter__ = lambda _self: iter(self._rows)
        return result


def _totals_row(**overrides):
    base = dict(
        total_turns=10, questions=8, answered=6, partial=1, unanswered=2,
        failed_answers=1, classified=10, failed_traces=1, sessions=4, users=2,
        avg_latency_ms=900.0, tokens=1000, cost=0.5,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── SQL compiles ───────────────────────────────────────────────────────────


async def test_turn_totals_compiles_and_derives_rates() -> None:
    session = CompilingSession(row=_totals_row())
    result = await AnalyticsRepository(session).turn_totals(WINDOW)

    assert session.compiled, "no statement was compiled"
    assert result["total_turns"] == 10
    # Silent refresh turns count as processed turns but not as questions.
    assert result["questions"] == 8
    assert result["success_rate"] == 0.6
    # Failure = unanswered + failed over the same denominator.
    assert result["failure_rate"] == 0.3
    assert result["avg_latency_ms"] == 900


async def test_turn_totals_handles_an_empty_window() -> None:
    session = CompilingSession(
        row=_totals_row(
            total_turns=0, questions=0, answered=0, partial=0, unanswered=0,
            failed_answers=0, classified=0, sessions=0, users=0,
            avg_latency_ms=None, tokens=0, cost=0,
        )
    )
    result = await AnalyticsRepository(session).turn_totals(WINDOW)

    # No division by zero, and no None leaking into the response.
    assert result["success_rate"] == 0.0
    assert result["failure_rate"] == 0.0
    assert result["avg_latency_ms"] == 0


@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("latency_percentile", {}),
        ("sr_funnel", {}),
        ("daily_series", {}),
        ("questions", {}),
        ("top_questions", {}),
        ("feedback_comments", {}),
        ("engagement", {}),
        ("hourly_sessions", {}),
        ("recent_sessions", {}),
        ("agent_breakdown", {}),
    ],
)
async def test_every_query_compiles_for_postgres(method: str, kwargs: dict) -> None:
    """Each method must produce valid PostgreSQL, not just valid Python."""
    session = CompilingSession(row=_engagement_row())
    repo = AnalyticsRepository(session)

    await getattr(repo, method)(WINDOW, **kwargs)

    assert session.compiled, f"{method} compiled no statement"
    for sql in session.compiled:
        assert "SELECT" in sql.upper()


def _engagement_row():
    return SimpleNamespace(
        sessions=3, avg_seconds=120.0, median_seconds=100.0, total_seconds=360.0,
        avg_turns=2.0, total_questions=6,
        # rating_totals reads these off the same row object
        count=5, avg_score=4.0, with_comment=2,
        star_1=0, star_2=1, star_3=0, star_4=1, star_5=3,
        help_count=3, sr_count=2, help_avg=4.0, sr_avg=4.0,
    )


async def test_rating_totals_shapes_distribution_and_sources() -> None:
    session = CompilingSession(row=_engagement_row())
    result = await AnalyticsRepository(session).rating_totals(WINDOW)

    assert result["ratings_count"] == 5
    assert result["avg_rating"] == 4.0
    assert sum(result["distribution"].values()) == 5
    assert set(result["distribution"]) == {"1", "2", "3", "4", "5"}
    assert result["by_source"]["help_answer"]["count"] == 3
    assert result["by_source"]["sr_created"]["count"] == 2


async def test_rating_totals_returns_none_average_when_unrated() -> None:
    row = _engagement_row()
    row.count = 0
    row.avg_score = None
    row.help_avg = None
    row.sr_avg = None
    session = CompilingSession(row=row)

    result = await AnalyticsRepository(session).rating_totals(WINDOW)

    # None, not 0 — "nobody rated" and "everyone rated zero" differ.
    assert result["avg_rating"] is None
    assert result["by_source"]["help_answer"]["avg_rating"] is None


async def test_engagement_converts_seconds_to_minutes() -> None:
    session = CompilingSession(row=_engagement_row())
    result = await AnalyticsRepository(session).engagement(WINDOW)

    assert result["avg_session_seconds"] == 120
    assert result["avg_session_minutes"] == 2.0
    assert result["total_time_spent_minutes"] == 6.0
    assert result["avg_turns_per_session"] == 2.0


async def test_hourly_sessions_always_returns_24_buckets() -> None:
    rows = [SimpleNamespace(hour=9, count=4), SimpleNamespace(hour=14, count=7)]
    session = CompilingSession(rows=rows)

    buckets = await AnalyticsRepository(session).hourly_sessions(WINDOW)

    assert len(buckets) == 24
    assert buckets[9] == 4
    assert buckets[14] == 7
    assert buckets[0] == 0


async def test_hourly_sessions_ignores_out_of_range_hours() -> None:
    session = CompilingSession(rows=[SimpleNamespace(hour=99, count=3)])
    buckets = await AnalyticsRepository(session).hourly_sessions(WINDOW)
    assert sum(buckets) == 0


async def test_agent_breakdown_labels_unrouted_turns() -> None:
    rows = [
        SimpleNamespace(agent=None, turns=4, answered=2, failed=0, avg_latency_ms=500.0),
        SimpleNamespace(
            agent="help_agent", turns=10, answered=8, failed=1, avg_latency_ms=None
        ),
    ]
    session = CompilingSession(rows=rows)

    breakdown = await AnalyticsRepository(session).agent_breakdown(WINDOW)

    assert breakdown[0]["agent"] == "unrouted"
    assert breakdown[0]["answered_rate"] == 0.5
    assert breakdown[1]["answered_rate"] == 0.8
    assert breakdown[1]["avg_latency_ms"] == 0


# ── silent-turn exclusion ──────────────────────────────────────────────────


@pytest.mark.parametrize("method", ["questions", "top_questions"])
async def test_question_queries_exclude_silent_turns(method: str) -> None:
    """The UI fires blank and "none" turns for background syncs.

    On live data those are about a third of all traces, and counting them
    buried the real top questions under an empty string. Both question
    queries must filter them out in SQL.
    """
    session = CompilingSession(row=_engagement_row())
    await getattr(AnalyticsRepository(session), method)(WINDOW)

    sql = " ".join(session.compiled).lower()
    assert "trim" in sql, "input is not trimmed before comparison"
    assert "'none'" in sql, "the 'none' placeholder is not excluded"


async def test_turn_totals_counts_questions_separately_from_turns() -> None:
    session = CompilingSession(row=_totals_row(total_turns=10, questions=8))
    result = await AnalyticsRepository(session).turn_totals(WINDOW)

    # Latency and cost cover every turn; "questions asked" must not.
    assert result["total_turns"] == 10
    assert result["questions"] == 8


# ── filters ────────────────────────────────────────────────────────────────


def test_filters_emit_only_the_clauses_that_were_set() -> None:
    assert AnalyticsFilters().trace_clauses() == []

    full = AnalyticsFilters(
        from_date=NOW - timedelta(days=1),
        to_date=NOW,
        agent="help_agent",
        user_id=uuid4(),
    )
    assert len(full.trace_clauses()) == 4


# ── session comments ───────────────────────────────────────────────────────


async def test_recent_sessions_surfaces_rating_comments() -> None:
    """The written feedback is the most useful cell in a conversation row.

    It has to come out of the same query as the rating — asking someone to
    open the trace to read one word defeats the point of the column.
    """
    session = CompilingSession(row=_engagement_row())
    await AnalyticsRepository(session).recent_sessions(WINDOW)

    sql = " ".join(session.compiled).lower()
    assert "string_agg" in sql, "comments are not aggregated per session"
    assert "comment" in sql


# ── transcript ─────────────────────────────────────────────────────────────


async def test_session_transcript_pairs_questions_with_answers() -> None:
    rows = [
        SimpleNamespace(
            id=uuid4(), created_at=NOW, user_id=uuid4(),
            input_message="How do I submit an SR?",
            output_message="Follow these steps...", active_agent=None,
            intent="ASK_HELP", status="SUCCESS", answer_status="ANSWERED",
            total_latency_ms=1200, error_message=None,
            score=5, comment="Nice", source="help_answer",
        ),
        SimpleNamespace(
            id=uuid4(), created_at=NOW, user_id=uuid4(), input_message="  ",
            output_message="ok", active_agent="rdd_handover_agent",
            intent=None, status="SUCCESS", answer_status="ANSWERED",
            total_latency_ms=90, error_message=None,
            score=None, comment=None, source=None,
        ),
    ]
    session = CompilingSession(rows=rows)

    turns = await AnalyticsRepository(session).session_transcript(uuid4())

    assert turns[0]["question"] == "How do I submit an SR?"
    assert turns[0]["answer"] == "Follow these steps..."
    assert turns[0]["rating"] == 5 and turns[0]["comment"] == "Nice"
    # A FAQ turn records no agent; the intent still names the handler.
    assert turns[0]["agent_label"] == "Help Agent"
    assert turns[0]["is_silent"] is False
    # A blank turn is a background sync, flagged so callers can hide it.
    assert turns[1]["is_silent"] is True
    assert turns[1]["question"] is None


async def test_outcome_counts_ignore_silent_turns() -> None:
    """Accuracy is about questions, so both sides of the ratio exclude
    background syncs — otherwise coverage can exceed 100%."""
    session = CompilingSession(row=_totals_row())
    await AnalyticsRepository(session).turn_totals(WINDOW)

    sql = " ".join(session.compiled).lower()
    # The answer_status filters carry the real-question predicate alongside.
    assert sql.count("'none'") >= 2, "outcome counts are not question-scoped"


# ── operational insights ───────────────────────────────────────────────────


async def test_node_latency_ranks_by_p95_not_average() -> None:
    """A node that is usually fast but occasionally stalls is the one users
    notice; ranking by mean hides it."""
    session = CompilingSession(rows=[])
    await AnalyticsRepository(session).node_latency(WINDOW)

    sql = " ".join(session.compiled).lower()
    assert "percentile_cont" in sql
    assert "order by" in sql


async def test_tool_health_reports_success_rate_per_endpoint() -> None:
    rows = [
        SimpleNamespace(tool="lease_tenant_api", calls=458, failed=13,
                        avg_ms=141.0, p95_ms=539.0),
        SimpleNamespace(tool="sr_api.create", calls=34, failed=2,
                        avg_ms=3605.0, p95_ms=30008.0),
    ]
    session = CompilingSession(rows=rows)

    health = await AnalyticsRepository(session).tool_health(WINDOW)

    assert health[0]["success_rate"] == round(445 / 458, 4)
    assert health[1]["failed"] == 2
    assert health[1]["p95_ms"] == 30008


async def test_tool_health_survives_an_endpoint_with_no_calls() -> None:
    session = CompilingSession(
        rows=[SimpleNamespace(tool="x", calls=0, failed=0, avg_ms=None, p95_ms=None)]
    )
    health = await AnalyticsRepository(session).tool_health(WINDOW)
    # No division by zero, and no None leaking out.
    assert health[0]["success_rate"] == 0.0
    assert health[0]["avg_ms"] == 0


async def test_llm_usage_shares_and_cost_coverage() -> None:
    rows = [
        SimpleNamespace(prompt="handover_extraction_prompt", model="gpt-4o-mini",
                        calls=1710, tokens=10947468, avg_tokens=6402.0,
                        avg_ms=1583.0, parse_failures=0, cost=0.0651, priced=64),
        SimpleNamespace(prompt="supervisor_prompt", model="gpt-4o-mini",
                        calls=336, tokens=1029418, avg_tokens=3064.0,
                        avg_ms=2558.0, parse_failures=4, cost=0.0255, priced=49),
    ]
    session = CompilingSession(rows=rows)

    usage = await AnalyticsRepository(session).llm_usage(WINDOW)

    assert usage["total_tokens"] == 10947468 + 1029418
    # The dominant prompt should be obvious without doing the arithmetic.
    assert usage["by_prompt"][0]["token_share"] > 0.9
    assert usage["by_prompt"][1]["parse_failure_rate"] == round(4 / 336, 4)
    # Coverage exposes that most calls carry no price — a bare total would
    # understate spend silently.
    assert usage["cost_coverage"] == round((64 + 49) / (1710 + 336), 4)


async def test_llm_usage_handles_no_calls() -> None:
    session = CompilingSession(rows=[])
    usage = await AnalyticsRepository(session).llm_usage(WINDOW)
    assert usage["total_tokens"] == 0
    assert usage["cost_coverage"] == 0.0
    assert usage["by_prompt"] == []


async def test_workflow_funnel_counts_sessions_per_stage() -> None:
    rows = [
        SimpleNamespace(stage="CREATE_SR", sessions=269, turns=2577),
        SimpleNamespace(stage="SR_CREATED", sessions=39, turns=42),
    ]
    session = CompilingSession(rows=rows)

    funnel = await AnalyticsRepository(session).workflow_funnel(WINDOW)

    assert funnel[0] == {"stage": "CREATE_SR", "sessions": 269, "turns": 2577}
    assert funnel[1]["sessions"] == 39
