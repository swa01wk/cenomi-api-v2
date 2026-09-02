"""Every dashboard route must at least execute.

The contract tests below cover the pure helpers, and the analytics tests cover
the SQL — but neither runs the handler bodies, so a name that exists in one
handler and not another sails through both. That is exactly how a NameError
reached the Recent Conversations page: an edit meant for the dashboard handler
also landed in the conversations handler, where the variable did not exist.

These tests call each handler with the repository stubbed, so the body runs end
to end and any undefined name, bad key or wrong signature fails here.
Handlers are called directly, so FastAPI's ``Query`` defaults are not
resolved for us — every such parameter is passed explicitly below.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.api.routes import dashboard

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def _session_row():
    return {
        "session_id": "11111111-1111-1111-1111-111111111111",
        "user_id": "22222222-2222-2222-2222-222222222222",
        "agent": "rdd_handover_agent",
        "agent_label": "RDD Handover Agent",
        "first_question": "How do I submit a service request?",
        "turns": 4,
        "started_at": NOW,
        "last_activity_at": NOW,
        "duration_seconds": 900,
        "unanswered_turns": 0,
        "resolved": True,
        "srs_created": 1,
        "avg_rating": 4.5,
        "ratings_count": 2,
        "comment": "Nice",
    }


def _totals():
    return {
        "total_turns": 10, "questions": 8, "answered": 6, "partial": 1,
        "unanswered": 1, "failed": 0, "classified": 8, "failed_traces": 0,
        "success_rate": 0.75, "failure_rate": 0.125, "sessions": 4,
        "unique_users": 2, "avg_latency_ms": 900, "total_tokens": 1000,
        "total_cost": 0.5,
    }


def _stub_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.turn_totals = AsyncMock(return_value=_totals())
    repo.daily_series = AsyncMock(return_value=[
        {"date": "2026-09-01", "turns": 4, "answered": 3, "not_answered": 1,
         "answered_rate": 0.75, "sessions": 2, "avg_latency_ms": 800,
         "ratings": 1, "avg_rating": 4.0}
    ])
    repo.engagement = AsyncMock(return_value={
        "sessions": 4, "avg_session_seconds": 120, "avg_session_minutes": 2.0,
        "median_session_seconds": 100, "total_time_spent_minutes": 8.0,
        "avg_turns_per_session": 2.0, "total_questions": 8,
    })
    repo.hourly_sessions = AsyncMock(return_value=[0] * 24)
    repo.rating_totals = AsyncMock(return_value={
        "ratings_count": 2, "avg_rating": 4.0, "comments_count": 1,
        "distribution": {"1": 0, "2": 0, "3": 0, "4": 1, "5": 1},
        "by_source": {"help_answer": {"count": 2, "avg_rating": 4.0},
                      "sr_created": {"count": 0, "avg_rating": None}},
    })
    repo.top_questions = AsyncMock(return_value=[
        {"question": "how do i submit a service request?", "count": 5, "not_answered": 1}
    ])
    repo.recent_sessions = AsyncMock(return_value=([_session_row()], 1))
    repo.top_users = AsyncMock(return_value=[{
        "user_id": "22222222-2222-2222-2222-222222222222", "questions": 8,
        "sessions": 4, "unanswered": 1, "last_active_at": NOW,
        "avg_rating": 4.0, "ratings_count": 2,
    }])
    repo.node_latency = AsyncMock(return_value=[])
    repo.tool_health = AsyncMock(return_value=[])
    repo.llm_usage = AsyncMock(return_value={
        "by_prompt": [], "total_calls": 0, "total_tokens": 0, "cost_coverage": 0.0
    })
    repo.workflow_funnel = AsyncMock(return_value=[])
    repo.session_transcript = AsyncMock(return_value=[{
        "trace_id": "t1", "at": NOW, "user_id": "22222222-2222-2222-2222-222222222222",
        "question": "How do I submit a service request?", "is_silent": False,
        "answer": "Follow these steps.", "agent": None, "agent_label": "Help Agent",
        "intent": "ASK_HELP", "status": "SUCCESS", "answer_status": "ANSWERED",
        "latency_ms": 900, "error": None, "rating": 5, "comment": "Nice",
        "rating_source": "help_answer",
    }])
    return repo


@pytest.fixture()
def stubbed():
    repo = _stub_repo()
    with (
        patch.object(dashboard, "AnalyticsRepository", return_value=repo),
        patch.object(dashboard, "resolve_names", AsyncMock(return_value={})),
        patch.object(dashboard, "find_user_ids", AsyncMock(return_value=[])),
    ):
        yield repo


async def test_dashboard_handler_runs(stubbed, mock_session) -> None:
    body = await dashboard.assistant_admin_dashboard(mock_session)

    assert body["success"] is True
    assert set(body["data"]["periods"]) == {"24h", "7d", "30d"}
    assert body["data"]["top_users"][0]["questions"] == 8


async def test_conversations_handler_runs(stubbed, mock_session) -> None:
    # The regression: this handler referenced a name from a sibling handler.
    body = await dashboard.assistant_admin_conversations(
        mock_session, page=1, limit=10, q=None, days=30
    )

    assert body["success"] is True
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["turns"] == 4


async def test_conversations_handler_runs_with_a_search(stubbed, mock_session) -> None:
    body = await dashboard.assistant_admin_conversations(
        mock_session, page=1, limit=10, q="qa super", days=30
    )
    assert body["success"] is True


async def test_conversation_detail_handler_runs(stubbed, mock_session) -> None:
    from uuid import uuid4

    body = await dashboard.assistant_admin_conversation_detail(
        uuid4(), mock_session, include_silent=False
    )

    assert body["success"] is True
    assert body["data"]["turns"][0]["question"]


async def test_insights_handler_runs(stubbed, mock_session) -> None:
    body = await dashboard.assistant_admin_insights(mock_session, days=30)

    assert body["success"] is True
    assert "node_latency" in body["data"]
