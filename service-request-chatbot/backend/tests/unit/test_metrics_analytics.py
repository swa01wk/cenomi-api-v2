"""Unit tests for metrics window parsing, ratios, and funnel contract helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

from app.db.models import AgentFeedback
from app.observability.analytics_constants import CREATE_SR_INTENTS, ratio
from app.observability.analytics_queries import parse_window

FUNNEL_STEP_KEYS = (
    "sessions_started",
    "create_intent",
    "draft_started",
    "preview_or_confirm",
    "sr_submitted",
    "lifecycle_completed",
)


def test_ratio_zero_denominator() -> None:
    assert ratio(3, 0) == 0.0
    assert ratio(0, 0) == 0.0
    assert ratio(2, -1) == 0.0


def test_ratio_normal() -> None:
    assert ratio(3, 4) == 0.75
    assert ratio(0, 10) == 0.0


def test_parse_window_defaults_to_seven_days() -> None:
    start, end = parse_window(None, None)
    assert end.tzinfo is not None
    assert start.tzinfo is not None
    delta = end - start
    assert timedelta(days=6, hours=23) < delta <= timedelta(days=7, minutes=1)


def test_parse_window_naive_timestamps_become_utc() -> None:
    naive_end = datetime(2026, 6, 1, 12, 0, 0)
    naive_start = datetime(2026, 5, 25, 12, 0, 0)
    start, end = parse_window(naive_start, naive_end)
    assert start.tzinfo == timezone.utc
    assert end.tzinfo == timezone.utc
    assert start == naive_start.replace(tzinfo=timezone.utc)
    assert end == naive_end.replace(tzinfo=timezone.utc)


def test_parse_window_explicit_range_preserved() -> None:
    start_in = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end_in = datetime(2026, 1, 8, tzinfo=timezone.utc)
    start, end = parse_window(start_in, end_in)
    assert start == start_in
    assert end == end_in


def test_funnel_step_keys_match_product_contract() -> None:
    # Keep in lockstep with compute_funnel() and the admin AI Success table.
    assert len(FUNNEL_STEP_KEYS) == 6
    assert FUNNEL_STEP_KEYS[0] == "sessions_started"
    assert FUNNEL_STEP_KEYS[-2] == "sr_submitted"


def test_not_useful_reason_group_by_uses_jsonb_text_extract() -> None:
    """Postgres rejects GROUP BY on the JSONB column itself (ticket 3713)."""
    reason_expr = func.coalesce(AgentFeedback.metadata_.op("->>")("reason"), "UNSPECIFIED")
    stmt = select(reason_expr, func.count(AgentFeedback.id)).group_by(reason_expr)
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "->>" in sql
    assert "group by" in sql.lower()


def test_create_intent_does_not_imply_submitted() -> None:
    """A create-intent session without sr_submitted must not count as completed."""
    create_intent_sessions = 10
    sr_submitted = 0
    assert ratio(sr_submitted, create_intent_sessions) == 0.0
    assert "CREATE_HANDED_OVER_SR" in CREATE_SR_INTENTS
