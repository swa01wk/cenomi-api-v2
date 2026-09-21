"""Unit tests for analytics event emit, constants, and fail-safe wrappers."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

from app.db.models import AnalyticsEvent
from app.observability.analytics_constants import (
    ALLOWED_EVENT_NAMES,
    CREATE_SR_INTENTS,
    EVENT_FIELD_CORRECTED,
    EVENT_SESSION_STARTED,
    EVENT_SR_SUBMITTED,
    FEEDBACK_FLOW_USEFUL,
    FEEDBACK_TURN_THUMBS,
    NOT_USEFUL_REASONS,
)
from app.observability.analytics_emit import emit_analytics
from app.observability.repositories.analytics_event_repo import AnalyticsEventRepository


async def test_emit_inserts_allowed_event(mock_session: AsyncMock) -> None:
    repo = AnalyticsEventRepository(mock_session)
    row = await repo.emit(
        user_id=uuid4(),
        session_id=uuid4(),
        event_name=EVENT_SESSION_STARTED,
        properties={"source": "test"},
    )
    assert isinstance(row, AnalyticsEvent)
    assert row.event_name == EVENT_SESSION_STARTED
    assert row.properties == {"source": "test"}
    mock_session.add.assert_called_once_with(row)
    mock_session.flush.assert_awaited_once()


async def test_emit_skips_unknown_event_name(mock_session: AsyncMock) -> None:
    repo = AnalyticsEventRepository(mock_session)
    row = await repo.emit(
        user_id=uuid4(),
        session_id=uuid4(),
        event_name="not_a_real_event",
    )
    assert row is None
    mock_session.add.assert_not_called()


async def test_emit_once_skips_when_exists(
    mock_session: AsyncMock, make_execute_result: object
) -> None:
    mock_session.execute.return_value = make_execute_result(scalar=uuid4())  # type: ignore[operator]
    repo = AnalyticsEventRepository(mock_session)
    row = await repo.emit_once(
        user_id=uuid4(),
        session_id=uuid4(),
        event_name=EVENT_SR_SUBMITTED,
        properties={"sr_id": "SR-1"},
        dedupe_sr_id="SR-1",
    )
    assert row is None
    mock_session.add.assert_not_called()


async def test_emit_analytics_swallows_repo_errors(mock_session: AsyncMock) -> None:
    mock_session.flush.side_effect = RuntimeError("db down")
    await emit_analytics(
        mock_session,
        user_id=uuid4(),
        session_id=uuid4(),
        event_name=EVENT_FIELD_CORRECTED,
        properties={"keys": ["unit"]},
    )


async def test_allowed_event_names_are_the_product_set() -> None:
    assert ALLOWED_EVENT_NAMES == {
        "session_started",
        "session_ended",
        "sr_submitted",
        "sr_lifecycle_completed",
        "field_corrected",
        "flow_cancelled",
        "sr_approved",
        "sr_rejected",
        "document_uploaded",
    }


async def test_create_sr_intents_cover_known_agents() -> None:
    assert "CREATE_HANDED_OVER_SR" in CREATE_SR_INTENTS
    assert "CREATE_RDD_SERVICE_REQUEST" in CREATE_SR_INTENTS
    assert FEEDBACK_TURN_THUMBS == "TURN_THUMBS"
    assert FEEDBACK_FLOW_USEFUL == "FLOW_USEFUL"
    assert "TOO_MANY_STEPS" in NOT_USEFUL_REASONS
