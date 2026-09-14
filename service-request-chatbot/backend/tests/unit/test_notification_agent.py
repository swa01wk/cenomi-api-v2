"""Unit tests — notification agent entry node and graph routing."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.graph.help_agent_graph import (
    _route_after_load,
    _route_after_open_notification,
    _route_after_supervisor,
)
from app.agents.graph.nodes.notification.notification_entry_node import (
    notification_entry_node,
)
from app.agents.graph.nodes.supervisor_node import supervisor_node
from app.agents.services.notification_inbox_service import (
    MockNotificationInboxService,
    NotificationInboxResult,
)
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


# List fetching lives in list_notifications_node (reconciled-spec split).
_NODE_MODULE = "app.agents.graph.nodes.notification.list_notifications_node"


def _mock_inbox_service() -> MockNotificationInboxService:
    return MockNotificationInboxService()


@pytest.mark.asyncio
async def test_list_without_auth_asks_to_sign_in() -> None:
    result = await notification_entry_node(
        _state(user_message="Show my notifications")
    )
    assert result["intent"] == "LIST_NOTIFICATIONS"
    assert result["response_ui"]["type"] == "message"
    assert "sign" in result["response_message"].lower()


@pytest.mark.asyncio
async def test_list_emits_grouped_notification_list() -> None:
    with patch(
        f"{_NODE_MODULE}.get_notification_inbox_service",
        return_value=_mock_inbox_service(),
    ):
        result = await notification_entry_node(
            _state(
                user_message="Show my notifications",
                auth_context=AuthContext(
                    subject_id="123", roles=frozenset({"DD_ENGINEER"})
                ),
            )
        )
    ui = result["response_ui"]
    assert result["intent"] == "LIST_NOTIFICATIONS"
    assert ui["type"] == "notification_list"
    subs = [g["sub_category"] for g in ui["groups"]]
    # DD Engineer audience: everything except Trading (MM/Operations only);
    # FIT_OUT_CONFIRM_HO is decommissioned.
    assert subs == [
        "HANDOVER",
        "FIT_OUT_CONFIRM_FOS",
        "FIT_OUT_CONFIRM_END",
    ]
    handover = ui["groups"][0]
    assert handover["total_count"] == 2
    assert handover["notifications"][0]["sr_id"] == "11711"
    assert "action-required" in result["response_message"]


@pytest.mark.asyncio
async def test_list_respects_role_audience() -> None:
    """Mall Manager only has the HANDOVER tool audience."""
    with patch(
        f"{_NODE_MODULE}.get_notification_inbox_service",
        return_value=_mock_inbox_service(),
    ):
        result = await notification_entry_node(
            _state(
                user_message="Show my notifications",
                auth_context=AuthContext(
                    subject_id="55", roles=frozenset({"MALL_MANAGER"})
                ),
            )
        )
    subs = [g["sub_category"] for g in result["response_ui"]["groups"]]
    assert subs == ["HANDOVER", "FIT_OUT_CONFIRM_TRADING"]


@pytest.mark.asyncio
async def test_list_tenant_sees_sales_data_group() -> None:
    with patch(
        f"{_NODE_MODULE}.get_notification_inbox_service",
        return_value=_mock_inbox_service(),
    ):
        result = await notification_entry_node(
            _state(
                user_message="Show my notifications",
                auth_context=AuthContext(
                    subject_id="101", roles=frozenset({"TENANT_CSV_UPLOADER"})
                ),
            )
        )
    groups = result["response_ui"]["groups"]
    assert [g["sub_category"] for g in groups] == ["SALES_DATA"]
    row = groups[0]["notifications"][0]
    assert row["kind"] == "sales_data"
    assert row["lease_code"] == "LC-1001"
    assert row["sr_id"] is None


@pytest.mark.asyncio
async def test_list_applies_unread_and_category_filters() -> None:
    with patch(
        f"{_NODE_MODULE}.get_notification_inbox_service",
        return_value=_mock_inbox_service(),
    ):
        result = await notification_entry_node(
            _state(
                user_message="show unread handover notifications",
                auth_context=AuthContext(
                    subject_id="55", roles=frozenset({"MALL_MANAGER"})
                ),
            )
        )
    ui = result["response_ui"]
    assert ui["filters"] == {"unread": True, "sub_categories": ["HANDOVER"]}
    assert len(ui["groups"]) == 1
    # The read HANDOVER fixture (11712) is filtered out.
    sr_ids = [n["sr_id"] for n in ui["groups"][0]["notifications"]]
    assert sr_ids == ["11711"]


@pytest.mark.asyncio
async def test_list_total_failure_returns_graceful_message() -> None:
    failed = AsyncMock()
    failed.fetch_inbox = AsyncMock(
        return_value=NotificationInboxResult(success=False, error="HTTP 502")
    )
    with patch(
        f"{_NODE_MODULE}.get_notification_inbox_service",
        return_value=failed,
    ):
        result = await notification_entry_node(
            _state(
                user_message="Show my notifications",
                auth_context=AuthContext(
                    subject_id="123", roles=frozenset({"DD_ENGINEER"})
                ),
            )
        )
    assert result["response_ui"]["type"] == "message"
    assert "couldn't load" in result["response_message"]


@pytest.mark.asyncio
async def test_list_empty_inbox_message() -> None:
    empty = AsyncMock()
    empty.fetch_inbox = AsyncMock(
        return_value=NotificationInboxResult(success=True, groups=[])
    )
    with patch(
        f"{_NODE_MODULE}.get_notification_inbox_service",
        return_value=empty,
    ):
        result = await notification_entry_node(
            _state(
                user_message="Show my notifications",
                auth_context=AuthContext(
                    subject_id="123", roles=frozenset({"DD_ENGINEER"})
                ),
            )
        )
    assert result["response_ui"]["type"] == "notification_list"
    assert result["response_ui"]["groups"] == []
    assert "caught up" in result["response_message"]


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
    assert _route_after_supervisor(state) == "list_notifications"


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


# ---------------------------------------------------------------------------
# OPEN_NOTIFICATION — seeded handoff routing
# ---------------------------------------------------------------------------


def _seeded_open_state(**kwargs: Any) -> dict[str, Any]:
    base = _state(
        user_message="",
        intent="OPEN_NOTIFICATION",
        action_override="open_notification",
        backend_refs={"sr_id": "11711", "opened_from_notification": True},
    )
    base.update(kwargs)
    return base


def test_seeded_open_turn_routes_to_sr_status_sync() -> None:
    assert _route_after_load(_seeded_open_state()) == "sr_status_sync"


def test_seeded_open_turn_bypasses_notification_node_even_with_viewing_context() -> None:
    state = _seeded_open_state(viewing_notification_id="NTF-000012")
    assert _route_after_load(state) == "sr_status_sync"


def test_legacy_open_action_without_seed_still_hits_notification_node() -> None:
    state = _state(
        user_message="",
        action_override="open_notification",
        viewing_notification_id="NTF-000012",
    )
    assert _route_after_load(state) == "notification"


def test_free_text_answer_not_hijacked_by_list_pattern() -> None:
    """An SR description mentioning alerts must stay with the open field."""
    state = _state(
        user_message="check the fire alerts on level 2",
        active_agent="rdd_handover_agent",
        active_field="description",
    )
    assert _route_after_load(state) == "handover_entry"


@pytest.mark.asyncio
async def test_notification_turns_clear_seeded_decorations() -> None:
    """Orchestration pre-seeds the reviewer 'View Service Request' picker;
    notification turns must clear it so finalize can't attach it (TC-3662)."""
    failed = AsyncMock()
    failed.fetch_inbox = AsyncMock(
        return_value=NotificationInboxResult(success=False, error="HTTP 500")
    )
    with patch(
        f"{_NODE_MODULE}.get_notification_inbox_service",
        return_value=failed,
    ):
        result = await notification_entry_node(
            _state(
                user_message="Show my notifications",
                auth_context=AuthContext(subject_id="6", roles=frozenset({"FM_MANAGER"})),
                response_decorations={"suggestions": {"kind": "pending_handover_srs"}},
            )
        )
    assert result["response_decorations"] == {}


@pytest.mark.asyncio
async def test_lease_activation_rows_marked_and_not_sr(monkeypatch) -> None:
    """ACTIVATED_LEASE_NOTIFICATION carries a LEASE id in text_content.id —
    it must surface as kind=lease_activation with lease_code, never as sr_id."""
    from app.agents.services.notification_inbox_service import (
        NotificationGroup,
        NotificationItem,
    )

    svc = AsyncMock()
    svc.fetch_inbox = AsyncMock(
        return_value=NotificationInboxResult(
            success=True,
            groups=[
                NotificationGroup(
                    "HANDOVER",
                    2,
                    [
                        NotificationItem(
                            notification_id="ntf-act-1",
                            sr_id="271716",  # actually the lease id
                            sub_category="HANDOVER",
                            message=(
                                "Contract DUMMY-CUST-001 is now active. Please "
                                "initiate the handover of the lease by creating a "
                                "Fit-Out & Handover Service Request."
                            ),
                            notification_type="ACTIVATED_LEASE_NOTIFICATION",
                        ),
                        NotificationItem(
                            notification_id="ntf-sr-1",
                            sr_id="320971",
                            sub_category="HANDOVER",
                            message="The Mall Manager has submitted SR 320971.",
                            notification_type="SERVICE_REQUEST",
                        ),
                    ],
                )
            ],
        )
    )
    with patch(f"{_NODE_MODULE}.get_notification_inbox_service", return_value=svc):
        result = await notification_entry_node(
            _state(
                user_message="show my notifications",
                auth_context=AuthContext(
                    subject_id="11", roles=frozenset({"MALL_MANAGER"})
                ),
            )
        )
    rows = result["response_ui"]["groups"][0]["notifications"]
    activation, sr_row = rows[0], rows[1]
    assert activation["kind"] == "lease_activation"
    assert activation["sr_id"] is None
    assert activation["lease_code"] == "DUMMY-CUST-001"
    assert activation["lease_id"] == "271716"
    assert sr_row["kind"] == "service_request"
    assert sr_row["sr_id"] == "320971"


def test_open_notification_sales_data_routes_to_sales_data_entry() -> None:
    state = {
        "active_agent": "sales_data_agent",
        "backend_refs": {
            "opened_from_notification": True,
            "notification_context": {
                "lease_code": "LC-1001",
                "period_hint": "month August",
                "source": "notification",
            },
        },
    }
    assert _route_after_open_notification(state) == "sales_data_entry"


def test_open_notification_handover_still_routes_to_sync() -> None:
    state = {
        "active_agent": "rdd_handover_agent",
        "backend_refs": {"sr_id": "11711", "opened_from_notification": True},
    }
    assert _route_after_open_notification(state) == "sr_status_sync"


def test_open_notification_missing_id_routes_to_response() -> None:
    assert _route_after_open_notification({"backend_refs": {}}) == "response_generation"

