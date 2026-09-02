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
        total_turns=10, answered=6, partial=1, unanswered=2, failed_answers=1,
        classified=10, failed_traces=1, sessions=4, users=2,
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
    assert result["success_rate"] == 0.6
    # Failure = unanswered + failed over the same denominator.
    assert result["failure_rate"] == 0.3
    assert result["avg_latency_ms"] == 900


async def test_turn_totals_handles_an_empty_window() -> None:
    session = CompilingSession(
        row=_totals_row(
            total_turns=0, answered=0, partial=0, unanswered=0, failed_answers=0,
            classified=0, sessions=0, users=0, avg_latency_ms=None, tokens=0, cost=0,
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
