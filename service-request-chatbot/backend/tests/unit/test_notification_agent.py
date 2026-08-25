"""Unit tests — notification agent entry node and graph routing."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.graph.help_agent_graph import (
    _route_after_load,
    _route_after_supervisor,
)
from app.agents.graph.nodes.notification.notification_entry_node import (
    notification_entry_node,
)
from app.agents.graph.nodes.supervisor_node import supervisor_node
from app.types.chat import AuthContext


def _state(**kwargs: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": "sess-001",
        "user_id": "user-001",
        "user_message": "",
        "backend_refs": {},
        "status": "IN_PROGRESS",
    }
    base.update(kwargs)
    return base


@pytest.mark.asyncio
async def test_detail_uses_viewing_snapshot() -> None:
    result = await notification_entry_node(
        _state(
            user_message="Can you explain this notification?",
            viewing_notification_id="NTF-000012",
            viewing_notification={
                "id": "NTF-000012",
                "title": "Handover awaiting FM review",
                "body": "SR 11711 is ready for FM review.",
                "related_sr_id": "11711",
            },
        )
    )
    assert result["intent"] == "GET_NOTIFICATION_DETAIL"
    assert result["response_ui"]["type"] == "notification_detail_card"
    assert result["response_ui"]["found"] is True
    assert result["response_ui"]["related_sr_id"] == "11711"
    assert "Handover awaiting FM review" in result["response_message"]
    assert "active_agent" not in result


@pytest.mark.asyncio
async def test_detail_without_snapshot_still_resolves_id() -> None:
    result = await notification_entry_node(
        _state(
            user_message="explain this",
            viewing_notification_id="NTF-000012",
        )
    )
    assert result["response_ui"]["type"] == "notification_detail_card"
    assert result["response_ui"]["found"] is False
    assert result["response_ui"]["notification_id"] == "NTF-000012"
    assert "NTF-000012" in result["response_message"]


@pytest.mark.asyncio
async def test_list_emits_notification_list_card() -> None:
    result = await notification_entry_node(
        _state(user_message="Show my notifications")
    )
    assert result["intent"] == "LIST_NOTIFICATIONS"
    assert result["response_ui"]["type"] == "notification_list_card"


@pytest.mark.asyncio
async def test_mark_read_emits_structured_action() -> None:
    result = await notification_entry_node(
        _state(
            user_message="mark this as read",
            viewing_notification_id="NTF-000012",
        )
    )
    assert result["intent"] == "MARK_NOTIFICATION_READ"
    assert result["response_ui"]["type"] == "notification_action"
    assert result["response_ui"]["action"] == "mark_read"
    assert result["response_ui"]["notification_id"] == "NTF-000012"


def test_route_after_load_goes_to_notification_for_viewing_context() -> None:
    state = _state(
        active_agent="rdd_handover_agent",
        workflow_stage="CREATE_SR",
        user_message="explain this notification",
        viewing_notification_id="NTF-000012",
    )
    assert _route_after_load(state) == "notification"


def test_route_after_load_keeps_handover_for_ordinary_followup() -> None:
    state = _state(
        active_agent="rdd_handover_agent",
        workflow_stage="CREATE_SR",
        user_message="What's next?",
        intent="CREATE_RDD_SERVICE_REQUEST",
    )
    assert _route_after_load(state) == "handover_entry"


def test_route_after_supervisor_notification_intent() -> None:
    state = _state(intent="GET_NOTIFICATION_DETAIL", status="IN_PROGRESS")
    assert _route_after_supervisor(state) == "notification"


def test_route_after_supervisor_notification_beats_existing_sr_id() -> None:
    state = _state(
        intent="LIST_NOTIFICATIONS",
        status="IN_PROGRESS",
        backend_refs={"sr_id": "11711"},
    )
    assert _route_after_supervisor(state) == "notification"


@pytest.mark.asyncio
async def test_supervisor_deterministic_skips_llm() -> None:
    state = _state(
        user_message="Can you explain this notification?",
        viewing_notification_id="NTF-000012",
        active_agent="rdd_handover_agent",
    )
    with patch(
        "app.agents.graph.nodes.supervisor_node._call_supervisor_llm",
        new_callable=AsyncMock,
    ) as mock_llm:
        result = await supervisor_node(state)
    mock_llm.assert_not_called()
    assert result["intent"] == "GET_NOTIFICATION_DETAIL"
    assert "active_agent" not in result


@pytest.mark.asyncio
async def test_supervisor_viewing_sr_status_skips_llm() -> None:
    state = _state(
        user_message="What's the status?",
        viewing_service_request_id="11711",
        auth_context=AuthContext(subject_id="u1", roles=frozenset({"MALL_MANAGER"})),
    )
    with patch(
        "app.agents.graph.nodes.supervisor_node._call_supervisor_llm",
        new_callable=AsyncMock,
    ) as mock_llm:
        result = await supervisor_node(state)
    mock_llm.assert_not_called()
    assert result["intent"] == "CHECK_SERVICE_REQUEST_STATUS"
    assert result["backend_refs"]["sr_id"] == "11711"
