"""Integration tests for session analytics HTTP routes (ticket 3713).

ASGI + patched repositories — no real database.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.chat_orchestration_service import user_id_to_uuid

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
_USER = "demo_user"
_USER_UUID = user_id_to_uuid(_USER)


def _stub_db():
    return AsyncMock()


def _make_app_client() -> AsyncClient:
    from app.db.session import _get_db

    app.dependency_overrides[_get_db] = lambda: (yield _stub_db())
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _restore() -> None:
    app.dependency_overrides.clear()


def _session(*, user_id=_USER_UUID, ended=False) -> MagicMock:
    row = MagicMock()
    row.id = uuid4()
    row.user_id = user_id
    row.client_active_ms = 1_000
    row.last_activity_at = _NOW
    row.ended_at = _NOW if ended else None
    row.end_reason = "CLIENT_ENDED" if ended else None
    return row


def _feedback_row(*, session_id, label="USEFUL"):
    row = MagicMock()
    row.id = uuid4()
    row.trace_id = None
    row.session_id = session_id
    row.sr_id = "SR-1"
    row.run_id = None
    row.user_id = _USER_UUID
    row.feedback_type = "FLOW_USEFUL"
    row.score = None
    row.label = label
    row.comment = None
    row.metadata_ = {}
    row.created_at = _NOW
    return row


@pytest.fixture()
async def client():
    c = _make_app_client()
    try:
        yield c
    finally:
        await c.aclose()
        _restore()


class TestSessionHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_404_unknown_session(self, client: AsyncClient) -> None:
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=None)
        repo.expire_idle = AsyncMock(return_value=[])
        with patch(
            "app.api.routes.session_analytics.ChatSessionRepository",
            return_value=repo,
        ):
            res = await client.post(
                f"/api/chat/sessions/{uuid4()}/heartbeat",
                json={"client_active_delta_ms": 1000, "user_id": _USER},
            )
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_heartbeat_403_wrong_owner(self, client: AsyncClient) -> None:
        session = _session(user_id=uuid4())
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=session)
        repo.expire_idle = AsyncMock(return_value=[])
        with patch(
            "app.api.routes.session_analytics.ChatSessionRepository",
            return_value=repo,
        ):
            res = await client.post(
                f"/api/chat/sessions/{session.id}/heartbeat",
                json={"client_active_delta_ms": 1000, "user_id": _USER},
            )
        assert res.status_code == 403

    @pytest.mark.asyncio
    async def test_heartbeat_accumulates_active_ms(self, client: AsyncClient) -> None:
        session = _session()
        updated = _session()
        updated.client_active_ms = 31_000
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=session)
        repo.expire_idle = AsyncMock(return_value=[])
        repo.add_active_ms = AsyncMock(return_value=updated)
        with patch(
            "app.api.routes.session_analytics.ChatSessionRepository",
            return_value=repo,
        ):
            res = await client.post(
                f"/api/chat/sessions/{session.id}/heartbeat",
                json={"client_active_delta_ms": 30000, "user_id": _USER},
            )
        assert res.status_code == 200
        body = res.json()
        assert body["client_active_ms"] == 31_000
        repo.add_active_ms.assert_awaited_once()


class TestSessionEnd:
    @pytest.mark.asyncio
    async def test_end_emits_once(self, client: AsyncClient) -> None:
        session = _session()
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=session)
        repo.mark_ended = AsyncMock(return_value=True)
        with (
            patch(
                "app.api.routes.session_analytics.ChatSessionRepository",
                return_value=repo,
            ),
            patch(
                "app.api.routes.session_analytics.emit_analytics",
                new_callable=AsyncMock,
            ) as emit,
        ):
            res = await client.post(
                f"/api/chat/sessions/{session.id}/end",
                json={"reason": "CLIENT_ENDED", "user_id": _USER},
            )
        assert res.status_code == 200
        assert res.json()["already_ended"] is False
        emit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_end_idempotent(self, client: AsyncClient) -> None:
        session = _session(ended=True)
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=session)
        repo.mark_ended = AsyncMock(return_value=False)
        with (
            patch(
                "app.api.routes.session_analytics.ChatSessionRepository",
                return_value=repo,
            ),
            patch(
                "app.api.routes.session_analytics.emit_analytics",
                new_callable=AsyncMock,
            ) as emit,
        ):
            res = await client.post(
                f"/api/chat/sessions/{session.id}/end",
                json={"reason": "CLIENT_ENDED", "user_id": _USER},
            )
        assert res.status_code == 200
        assert res.json()["already_ended"] is True
        emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_end_rejects_idle_reason_from_client(self, client: AsyncClient) -> None:
        res = await client.post(
            f"/api/chat/sessions/{uuid4()}/end",
            json={"reason": "IDLE_TIMEOUT", "user_id": _USER},
        )
        assert res.status_code == 422


class TestSessionFeedback:
    @pytest.mark.asyncio
    async def test_flow_useful_201(self, client: AsyncClient) -> None:
        session = _session()
        fb = _feedback_row(session_id=session.id)
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=session)
        fb_repo = AsyncMock()
        fb_repo.upsert_flow_useful = AsyncMock(return_value=fb)
        with (
            patch(
                "app.api.routes.session_analytics.ChatSessionRepository",
                return_value=repo,
            ),
            patch(
                "app.api.routes.session_analytics.FeedbackRepository",
                return_value=fb_repo,
            ),
        ):
            res = await client.post(
                f"/api/chat/sessions/{session.id}/feedback",
                json={
                    "feedback_type": "FLOW_USEFUL",
                    "label": "USEFUL",
                    "user_id": _USER,
                },
            )
        assert res.status_code == 201
        assert res.json()["feedback_type"] == "FLOW_USEFUL"
        assert res.json()["label"] == "USEFUL"

    @pytest.mark.asyncio
    async def test_flow_useful_rejects_bad_label(self, client: AsyncClient) -> None:
        session = _session()
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=session)
        with patch(
            "app.api.routes.session_analytics.ChatSessionRepository",
            return_value=repo,
        ):
            res = await client.post(
                f"/api/chat/sessions/{session.id}/feedback",
                json={
                    "feedback_type": "FLOW_USEFUL",
                    "label": "THUMBS_UP",
                    "user_id": _USER,
                },
            )
        assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_unknown_feedback_type_rejected(self, client: AsyncClient) -> None:
        session = _session()
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=session)
        with patch(
            "app.api.routes.session_analytics.ChatSessionRepository",
            return_value=repo,
        ):
            res = await client.post(
                f"/api/chat/sessions/{session.id}/feedback",
                json={"feedback_type": "TURN_THUMBS", "user_id": _USER},
            )
        assert res.status_code == 422


class TestMetricsEndpoints:
    @pytest.mark.asyncio
    async def test_summary_returns_product_kpis(self, client: AsyncClient) -> None:
        payload = {
            "total_traces": 10,
            "success_rate": 0.9,
            "failed_traces": 1,
            "avg_latency_ms": 100.0,
            "total_tokens": 50,
            "total_cost": 0.01,
            "sessions": 4,
            "sr_submitted": 2,
            "sr_completion_rate": 0.5,
            "usefulness_rate": 0.8,
            "feedback_coverage": 0.4,
            "cost_per_successful_sr": 0.005,
            "p95_latency_ms": 200.0,
            "idle_abandoned": 1,
        }
        with (
            patch(
                "app.observability.api.metrics.close_idle_sessions",
                new_callable=AsyncMock,
            ),
            patch(
                "app.observability.api.metrics.compute_summary",
                new_callable=AsyncMock,
                return_value=payload,
            ),
        ):
            res = await client.get("/api/v1/observability/metrics/summary")
        assert res.status_code == 200
        body = res.json()
        assert body["sr_completion_rate"] == 0.5
        assert body["sessions"] == 4

    @pytest.mark.asyncio
    async def test_funnel_has_six_steps(self, client: AsyncClient) -> None:
        payload = {
            "steps": [
                {"key": "sessions_started", "count": 10},
                {"key": "create_intent", "count": 8},
                {"key": "draft_started", "count": 6},
                {"key": "preview_or_confirm", "count": 5},
                {"key": "sr_submitted", "count": 3},
                {"key": "lifecycle_completed", "count": 1},
            ],
            "drop_off_by_stage": [{"workflow_stage": "CREATE_SR", "last_sessions": 2}],
        }
        with (
            patch(
                "app.observability.api.metrics.close_idle_sessions",
                new_callable=AsyncMock,
            ),
            patch(
                "app.observability.api.metrics.compute_funnel",
                new_callable=AsyncMock,
                return_value=payload,
            ),
        ):
            res = await client.get("/api/v1/observability/metrics/funnel")
        assert res.status_code == 200
        assert [s["key"] for s in res.json()["steps"]] == [
            "sessions_started",
            "create_intent",
            "draft_started",
            "preview_or_confirm",
            "sr_submitted",
            "lifecycle_completed",
        ]

    @pytest.mark.asyncio
    async def test_users_and_feedback_ok(self, client: AsyncClient) -> None:
        users = {"items": [], "total": 0, "page": 1, "page_size": 25}
        feedback = {
            "thumbs_up": 1,
            "thumbs_down": 0,
            "thumbs_up_rate": 1.0,
            "useful": 1,
            "not_useful": 0,
            "usefulness_rate": 1.0,
            "csat_avg": None,
            "csat_n": 0,
            "not_useful_reasons": [],
        }
        with (
            patch(
                "app.observability.api.metrics.close_idle_sessions",
                new_callable=AsyncMock,
            ),
            patch(
                "app.observability.api.metrics.compute_users",
                new_callable=AsyncMock,
                return_value=users,
            ),
            patch(
                "app.observability.api.metrics.compute_feedback",
                new_callable=AsyncMock,
                return_value=feedback,
            ),
        ):
            u = await client.get("/api/v1/observability/metrics/users")
            f = await client.get("/api/v1/observability/metrics/feedback")
        assert u.status_code == 200
        assert f.status_code == 200
        assert f.json()["usefulness_rate"] == 1.0
