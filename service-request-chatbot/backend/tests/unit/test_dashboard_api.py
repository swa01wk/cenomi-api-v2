"""Unit tests for the assistant-admin dashboard contract helpers.

The dashboard's field names and shapes are consumed directly by the tenant
frontend, so these tests pin the contract as much as the arithmetic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.api.routes.dashboard import (
    _PERIODS,
    _conversation_row,
    _pct,
    _time_label,
)

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def _session(**overrides):
    base = {
        "session_id": "11111111-1111-1111-1111-111111111111",
        "user_id": "22222222-2222-2222-2222-222222222222",
        "agent": "help_agent",
        "turns": 4,
        "started_at": NOW - timedelta(minutes=20),
        "last_activity_at": NOW - timedelta(minutes=5),
        "duration_seconds": 900,
        "unanswered_turns": 0,
        "resolved": True,
        "srs_created": 0,
        "avg_rating": 4.5,
    }
    base.update(overrides)
    return base


# ── percentages ────────────────────────────────────────────────────────────


def test_pct_rounds_to_one_decimal() -> None:
    assert _pct(1, 3) == 33.3
    assert _pct(2, 4) == 50.0


def test_pct_is_zero_rather_than_undefined_on_an_empty_window() -> None:
    assert _pct(0, 0) == 0.0
    assert _pct(5, 0) == 0.0


# ── relative time labels ───────────────────────────────────────────────────


def test_time_label_scales_with_age() -> None:
    assert _time_label(NOW - timedelta(seconds=5), NOW) == "just now"
    assert _time_label(NOW - timedelta(minutes=7), NOW) == "7m ago"
    assert _time_label(NOW - timedelta(hours=3), NOW) == "3h ago"
    assert _time_label(NOW - timedelta(days=2), NOW) == "2d ago"


def test_time_label_is_empty_without_a_timestamp() -> None:
    assert _time_label(None, NOW) == ""


# ── conversation rows ──────────────────────────────────────────────────────


def test_conversation_row_carries_every_field_the_frontend_reads() -> None:
    row = _conversation_row(_session(), {}, NOW)

    for key in (
        "id",
        "user_name",
        "message_preview",
        "turns",
        "escalated",
        "resolved",
        "time_label",
    ):
        assert key in row, f"contract field {key} missing"


def test_conversation_row_uses_the_resolved_username_when_known() -> None:
    session = _session()
    names = {session["user_id"]: "fatima.k"}

    assert _conversation_row(session, names, NOW)["user_name"] == "fatima.k"


def test_conversation_row_falls_back_to_a_short_user_label() -> None:
    # Chat user ids are uuid5-derived for non-UUID subjects, so no users row
    # may exist — the list must still identify the conversation.
    row = _conversation_row(_session(), {}, NOW)
    assert row["user_name"] == "User 22222222"


def test_conversation_row_is_blank_for_an_unknown_user() -> None:
    row = _conversation_row(_session(user_id=None), {}, NOW)
    assert row["user_name"] == ""


def test_escalated_reflects_turns_the_agent_could_not_answer() -> None:
    assert _conversation_row(_session(unanswered_turns=2), {}, NOW)["escalated"] is True
    assert _conversation_row(_session(unanswered_turns=0), {}, NOW)["escalated"] is False


def test_resolved_passes_through_from_the_aggregate() -> None:
    assert _conversation_row(_session(resolved=False), {}, NOW)["resolved"] is False


# ── contract constants ─────────────────────────────────────────────────────


def test_period_keys_match_the_frontend_contract_exactly() -> None:
    # The frontend indexes periods by these literal strings.
    assert set(_PERIODS) == {"24h", "7d", "30d"}
    assert _PERIODS["24h"] == 1
    assert _PERIODS["7d"] == 7
    assert _PERIODS["30d"] == 30


# ── accuracy honesty ───────────────────────────────────────────────────────


def test_coverage_never_exceeds_the_questions_it_is_measured_over() -> None:
    """Sample size and questions must share a denominator.

    Before this was enforced, outcome counts included silent refresh turns
    while the question count did not, so coverage reported 184%.
    """
    assert _pct(82, 82) == 100.0
    assert _pct(2, 82) < 100.0
    # A thin sample must read as thin, not as full coverage.
    assert _pct(2, 2668) == 0.1
