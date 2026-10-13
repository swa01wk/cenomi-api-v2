"""Unit tests — sr_status_sync_node numeric role IDs and RDD date-ready messages.

Covers changes from staging branch:
- _normalize_operation_role()  converts None/int/string to uppercase string
- _map_operations_to_stage()  accepts FM role IDs (20, 81) and RDD role ID (12)
- sr_status_sync_node()  emits two distinct messages for RDD_REVIEW depending
  on whether the three date fields are already in collected_data.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.graph.nodes.shared.sr_status_sync_node import (
    _map_operations_to_stage,
    _normalize_operation_role,
    sr_status_sync_node,
)
from app.agents.services.platform_api_client import ServiceRequestGetResult


# ---------------------------------------------------------------------------
# _normalize_operation_role
# ---------------------------------------------------------------------------


class TestNormalizeOperationRole:
    def test_none_returns_empty_string(self) -> None:
        assert _normalize_operation_role(None) == ""

    def test_string_name_uppercased(self) -> None:
        assert _normalize_operation_role("fm_manager") == "FM_MANAGER"

    def test_numeric_int_becomes_string(self) -> None:
        assert _normalize_operation_role(20) == "20"

    def test_numeric_string_stays_string(self) -> None:
        assert _normalize_operation_role("12") == "12"

    def test_whitespace_stripped(self) -> None:
        assert _normalize_operation_role("  DD_ENGINEER  ") == "DD_ENGINEER"

    def test_zero_int(self) -> None:
        assert _normalize_operation_role(0) == "0"


# ---------------------------------------------------------------------------
# _map_operations_to_stage — numeric role IDs
# ---------------------------------------------------------------------------


class TestMapOperationsToStageNumericIds:
    def test_fm_role_id_20_maps_to_fm_review(self) -> None:
        ops = [{"assigned_role": "20", "status": "IN_PROGRESS"}]
        assert _map_operations_to_stage(ops, None) == "FM_REVIEW"

    def test_fm_role_id_81_maps_to_fm_review(self) -> None:
        ops = [{"assigned_role": "81", "status": "IN_PROGRESS"}]
        assert _map_operations_to_stage(ops, None) == "FM_REVIEW"

    def test_rdd_role_id_12_maps_to_rdd_review(self) -> None:
        ops = [{"assigned_role": "12", "status": "IN_PROGRESS"}]
        assert _map_operations_to_stage(ops, None) == "RDD_REVIEW"

    def test_rdd_takes_priority_over_fm_numeric(self) -> None:
        ops = [
            {"assigned_role": "12", "status": "IN_PROGRESS"},
            {"assigned_role": "20", "status": "IN_PROGRESS"},
        ]
        assert _map_operations_to_stage(ops, None) == "RDD_REVIEW"

    def test_fm_string_name_still_works(self) -> None:
        ops = [{"assigned_role": "FM_MANAGER", "status": "IN_PROGRESS"}]
        assert _map_operations_to_stage(ops, None) == "FM_REVIEW"

    def test_rdd_string_name_still_works(self) -> None:
        ops = [{"assigned_role": "DD_ENGINEER", "status": "IN_PROGRESS"}]
        assert _map_operations_to_stage(ops, None) == "RDD_REVIEW"

    def test_terminal_all_approved_returns_completed(self) -> None:
        ops = [
            {"assigned_role": "20", "status": "APPROVED"},
            {"assigned_role": "12", "status": "APPROVED"},
        ]
        assert _map_operations_to_stage(ops, None) == "SR_COMPLETED"

    def test_empty_operations_returns_current_stage(self) -> None:
        assert _map_operations_to_stage([], "FM_REVIEW") == "FM_REVIEW"
        assert _map_operations_to_stage([], None) is None

    def test_integer_role_id_20(self) -> None:
        """Platform may return the role as an actual integer, not string."""
        ops = [{"assigned_role": 20, "status": "IN_PROGRESS"}]
        assert _map_operations_to_stage(ops, None) == "FM_REVIEW"

    def test_integer_role_id_12(self) -> None:
        ops = [{"assigned_role": 12, "status": "IN_PROGRESS"}]
        assert _map_operations_to_stage(ops, None) == "RDD_REVIEW"

    def test_work_permit_dd_does_not_map_to_rdd_review(self) -> None:
        ops = [{"assigned_role": "DD_ENGINEER", "status": "IN_PROGRESS"}]
        stage = _map_operations_to_stage(
            ops,
            None,
            sub_category="CONSTRUCTION_HOT",
            platform_status="IN_PROCESS",
            category="WORK_PERMIT",
        )
        assert stage == "WP_DD_REVIEW"

    def test_handover_dd_still_maps_to_rdd_review_with_category(self) -> None:
        ops = [{"assigned_role": "DD_ENGINEER", "status": "IN_PROGRESS"}]
        stage = _map_operations_to_stage(
            ops,
            None,
            sub_category="HANDOVER",
            platform_status="IN_PROCESS",
            category="FIT_OUT_AND_HANDOVER",
        )
        assert stage == "RDD_REVIEW"


# ---------------------------------------------------------------------------
# sr_status_sync_node — numeric role ID routing
# ---------------------------------------------------------------------------


def _make_result(
    operations: list[dict[str, Any]],
    payload: dict[str, Any] | None = None,
    collected_data: dict[str, Any] | None = None,
) -> ServiceRequestGetResult:
    return ServiceRequestGetResult(
        success=True,
        sr_id="SR-9999",
        status="PENDING",
        service_request_operations=operations,
        payload=payload or {
            "mall": "Test Mall",
            "brand": "Test Brand",
            "lease": "T0000001",
            "lease_code": "T0000001",
            "lease_id": 1,
            "tenant_profile_id": 10,
            "property_id": 20,
        },
    )


def _base_state(
    sr_id: str = "SR-9999",
    workflow_stage: str | None = None,
    collected_data: dict[str, Any] | None = None,
    response_message: str | None = None,
) -> dict[str, Any]:
    return {
        "session_id": "sess-test",
        "user_id": "user-test",
        "trace_manager": None,
        "trace_id": None,
        "workflow_stage": workflow_stage,
        "backend_refs": {"sr_id": sr_id},
        "auth_context": None,
        "collected_data": collected_data or {},
        "response_message": response_message,
    }


@pytest.mark.asyncio
async def test_numeric_fm_role_id_routes_to_fm_review() -> None:
    ops = [{"assigned_role": "20", "status": "IN_PROGRESS"}]
    mock_result = _make_result(ops)
    with patch(
        "app.agents.graph.nodes.shared.sr_status_sync_node.get_service_request_api_service",
        return_value=AsyncMock(get_service_request=AsyncMock(return_value=mock_result)),
    ):
        result = await sr_status_sync_node(_base_state())
    assert result["workflow_stage"] == "FM_REVIEW"


@pytest.mark.asyncio
async def test_numeric_rdd_role_id_routes_to_rdd_review() -> None:
    ops = [{"assigned_role": "12", "status": "IN_PROGRESS"}]
    mock_result = _make_result(ops)
    with patch(
        "app.agents.graph.nodes.shared.sr_status_sync_node.get_service_request_api_service",
        return_value=AsyncMock(get_service_request=AsyncMock(return_value=mock_result)),
    ):
        result = await sr_status_sync_node(_base_state())
    assert result["workflow_stage"] == "RDD_REVIEW"


@pytest.mark.asyncio
async def test_integer_role_id_20_routes_to_fm_review() -> None:
    """assigned_role returned as integer 20 must still map to FM_REVIEW."""
    ops = [{"assigned_role": 20, "status": "IN_PROGRESS"}]
    mock_result = _make_result(ops)
    with patch(
        "app.agents.graph.nodes.shared.sr_status_sync_node.get_service_request_api_service",
        return_value=AsyncMock(get_service_request=AsyncMock(return_value=mock_result)),
    ):
        result = await sr_status_sync_node(_base_state())
    assert result["workflow_stage"] == "FM_REVIEW"


# ---------------------------------------------------------------------------
# sr_status_sync_node — RDD date-ready message differentiation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rdd_review_with_dates_ready_emits_action_message() -> None:
    """All three RDD dates present → action-oriented message (no date prompt)."""
    ops = [{"assigned_role": "DD_ENGINEER", "status": "IN_PROGRESS"}]
    collected = {
        "actual_handover_date": "2026-08-01",
        "fitout_start_date": "2026-08-05",
        "fitout_end_date": "2026-08-15",
    }
    mock_result = _make_result(ops)
    with patch(
        "app.agents.graph.nodes.shared.sr_status_sync_node.get_service_request_api_service",
        return_value=AsyncMock(get_service_request=AsyncMock(return_value=mock_result)),
    ):
        result = await sr_status_sync_node(
            _base_state(workflow_stage="RDD_REVIEW", collected_data=collected)
        )
    msg = result.get("response_message") or ""
    assert "upload any required documents" in msg.lower()
    assert "please provide" not in msg.lower()


@pytest.mark.asyncio
async def test_rdd_review_missing_dates_emits_date_prompt_message() -> None:
    """One or more RDD dates absent → prompt message asking for dates."""
    ops = [{"assigned_role": "DD_ENGINEER", "status": "IN_PROGRESS"}]
    mock_result = _make_result(ops)
    with patch(
        "app.agents.graph.nodes.shared.sr_status_sync_node.get_service_request_api_service",
        return_value=AsyncMock(get_service_request=AsyncMock(return_value=mock_result)),
    ):
        result = await sr_status_sync_node(
            _base_state(workflow_stage="RDD_REVIEW", collected_data={})
        )
    msg = result.get("response_message") or ""
    assert "please provide" in msg.lower()
    assert "actual handover" in msg.lower()


@pytest.mark.asyncio
async def test_rdd_review_partial_dates_treated_as_missing() -> None:
    """Partial dates (only one set) still triggers the date-missing prompt."""
    ops = [{"assigned_role": "DD_ENGINEER", "status": "IN_PROGRESS"}]
    collected = {"actual_handover_date": "2026-08-01"}  # fitout dates missing
    mock_result = _make_result(ops)
    with patch(
        "app.agents.graph.nodes.shared.sr_status_sync_node.get_service_request_api_service",
        return_value=AsyncMock(get_service_request=AsyncMock(return_value=mock_result)),
    ):
        result = await sr_status_sync_node(
            _base_state(workflow_stage="RDD_REVIEW", collected_data=collected)
        )
    msg = result.get("response_message") or ""
    assert "please provide" in msg.lower()


@pytest.mark.asyncio
async def test_fm_review_message_not_affected_by_rdd_logic() -> None:
    """FM_REVIEW still gets the standard review message (no date logic)."""
    ops = [{"assigned_role": "FM_MANAGER", "status": "IN_PROGRESS"}]
    mock_result = _make_result(ops)
    with patch(
        "app.agents.graph.nodes.shared.sr_status_sync_node.get_service_request_api_service",
        return_value=AsyncMock(get_service_request=AsyncMock(return_value=mock_result)),
    ):
        result = await sr_status_sync_node(_base_state())
    msg = result.get("response_message") or ""
    assert "Fm Review" in msg or "fm review" in msg.lower()


@pytest.mark.asyncio
async def test_existing_response_message_not_overwritten() -> None:
    """When response_message already set in state, sync must not overwrite it."""
    ops = [{"assigned_role": "DD_ENGINEER", "status": "IN_PROGRESS"}]
    mock_result = _make_result(ops)
    with patch(
        "app.agents.graph.nodes.shared.sr_status_sync_node.get_service_request_api_service",
        return_value=AsyncMock(get_service_request=AsyncMock(return_value=mock_result)),
    ):
        result = await sr_status_sync_node(
            _base_state(
                workflow_stage="RDD_REVIEW",
                response_message="Existing message — do not overwrite.",
            )
        )
    assert "response_message" not in result


@pytest.mark.asyncio
async def test_general_inquiry_open_stays_on_crt_review() -> None:
    """Mall Manager is still the stored L1 role; opening INQUIRY must not become FM review."""
    ops = [{"assigned_role": 11, "status": "IN_PROGRESS"}]
    mock_result = _make_result(
        ops,
        payload={
            "category": "INQUIRY",
            "sub_category": "GENERAL",
            "title": "Store hours",
            "description": "What are the mall opening hours?",
            "lease": "t0031320",
            "brand": "Brand Moog Phone",
            "mall": "Salaam Mall - JEDDAH",
            "city": "Jeddah",
            "unit_codes": ["GFK19"],
            "notes": "No additional notes provided for lease t0031320.",
            "lease_brand_mall": (
                "t0031320-Brand Moog Phone-Salaam Mall - JEDDAH-"
                "Arkan Art Trading CO--arabic"
            ),
        },
    )
    mock_result.status = "IN_PROCESS"
    with patch(
        "app.agents.graph.nodes.shared.sr_status_sync_node.get_service_request_api_service",
        return_value=AsyncMock(get_service_request=AsyncMock(return_value=mock_result)),
    ):
        result = await sr_status_sync_node(_base_state(sr_id="8898"))

    assert result["workflow_stage"] == "GI_CRT_REVIEW"
    assert result["active_agent"] == "general_inquiry_agent"
    assert result["service_category"] == "INQUIRY"
    assert result["sub_category"] == "GENERAL"
    message = result["response_message"]
    assert "8898" in message
    assert "Customer Relations Team" in message
    assert "Store hours" in message
    assert "Brand Moog Phone" in message
    assert "handover" not in message.lower()
    card = result["response_ui"]
    assert card["type"] == "confirmation_card"
    assert card["verbatim"] is True
    assert card["readOnly"] is True
    assert card["requestType"] == "General Inquiry"
    assert [item["label"] for item in card["actions"]] == [
        "Approve",
        "Reject",
        "Ask for more info",
        "Reassign",
    ]
    by_key = {field["key"]: field["value"] for field in card["fields"]}
    assert by_key["company"] == "Arkan Art Trading CO"
    assert by_key["brand"] == "Brand Moog Phone"
    assert by_key["mall"] == "Salaam Mall - JEDDAH"
    assert by_key["lease_code"] == "t0031320"
    assert by_key["unit"] == "GFK19"
    assert by_key["title"] == "Store hours"
    assert by_key["description"] == "What are the mall opening hours?"
    assert by_key["notes"] == "No additional notes provided for lease t0031320."
    assert result["backend_refs"]["create_payload"]["service_category"] == "INQUIRY"


@pytest.mark.asyncio
async def test_history_and_creator_stored_in_backend_refs() -> None:
    ops = [{"assigned_role": "FM_MANAGER", "status": "IN_PROGRESS"}]
    mock_result = _make_result(ops)
    mock_result.service_request_history = [
        {"status": "CREATED", "operator_name": "Ali Hassan"}
    ]
    mock_result.created_by = "Ali Hassan"
    mock_result.created_user_role = "PRIMARY_TENANT_CONTACT"
    with patch(
        "app.agents.graph.nodes.shared.sr_status_sync_node.get_service_request_api_service",
        return_value=AsyncMock(get_service_request=AsyncMock(return_value=mock_result)),
    ):
        result = await sr_status_sync_node(_base_state())
    refs = result["backend_refs"]
    assert refs["sr_history"] == [{"status": "CREATED", "operator_name": "Ali Hassan"}]
    assert refs["sr_creator"] == {"name": "Ali Hassan", "role": "PRIMARY_TENANT_CONTACT"}


@pytest.mark.asyncio
async def test_no_creator_fields_leave_no_sr_creator() -> None:
    ops = [{"assigned_role": "FM_MANAGER", "status": "IN_PROGRESS"}]
    with patch(
        "app.agents.graph.nodes.shared.sr_status_sync_node.get_service_request_api_service",
        return_value=AsyncMock(get_service_request=AsyncMock(return_value=_make_result(ops))),
    ):
        result = await sr_status_sync_node(_base_state())
    assert "sr_creator" not in result["backend_refs"]


@pytest.mark.asyncio
async def test_no_sr_id_returns_empty_dict() -> None:
    state = {
        "session_id": "sess-empty",
        "backend_refs": {},
        "workflow_stage": None,
    }
    result = await sr_status_sync_node(state)
    assert result == {}
