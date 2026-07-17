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
async def test_no_sr_id_returns_empty_dict() -> None:
    state = {
        "session_id": "sess-empty",
        "backend_refs": {},
        "workflow_stage": None,
    }
    result = await sr_status_sync_node(state)
    assert result == {}
