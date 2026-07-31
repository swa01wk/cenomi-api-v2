"""Integration tests for the Path B (HANDED_OVER / Already Trading) SR lifecycle.

Covers the single-step MM flow:
  CREATE_HANDED_OVER_SR → payload built → SR created → HANDED_OVER_COMPLETED

All platform API calls are mocked.  LangGraph state transitions are exercised
through the node functions directly (no FastAPI HTTP layer needed).

Scenarios
---------
1. Payload builder produces correct shape for CREATE_HANDED_OVER_SR
2. api_submission_node with sub_category=HANDED_OVER → HANDED_OVER_COMPLETED immediately
3. sr_status_sync_node with HANDED_OVER + APPROVED → SR_COMPLETED (early exit)
4. sr_status_sync_node with HANDOVER (Path A) + APPROVED ops does NOT short-circuit
5. HANDED_OVER is not routed through FM or RDD stages
6. Sub-category HANDED_OVER is registered in the workflow registry
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.graph.nodes.handover.api_submission_node import api_submission_node
from app.agents.graph.nodes.handover.payload_builder_node import payload_builder_node
from app.agents.graph.nodes.shared.sr_status_sync_node import (
    sr_status_sync_node,
    _map_operations_to_stage,
)
from app.agents.registries.workflow_config import get_workflow_config
from app.agents.services.payload_builder_service import build_create_handed_over_payload
from app.agents.services.service_request_api_service import ServiceRequestCreationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_state(**kwargs: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "session_id": "handedover-sess-001",
        "user_id": "user-mm-001",
        "trace_id": None,
        "trace_manager": None,
        "validation_errors": [],
        "collected_data": {},
        "backend_refs": {},
        "action_override": None,
        "workflow_stage": "CREATE_HANDED_OVER_SR",
        "active_agent": "rdd_handover_agent",
        "sub_category": "HANDED_OVER",
        "confirmation_status": "CONFIRMED",
    }
    state.update(kwargs)
    return state


def _full_handed_over_collected_data() -> dict[str, Any]:
    return {
        "mall": "Dubai Mall",
        "brand": "BrandX",
        "lease_code": "LC-HO-001",
        "title": "Already Trading - LC-HO-001",
        "lease_id": 456,
        "contract_id": 456,
        "property_id": 10,
        "tenant_profile_id": 116,
        "brand_id": 789,
        "unit_codes": ["U-101"],
        "contracted_area": 150.0,
        "city": "Dubai",
        "lease_brand_mall": "BrandX@Dubai Mall",
        "description": "Already trading unit",
        "already_handovered": "already_trading",
        "actual_shop_handover_date": "2026-01-10",
        "actual_fitout_start_date": "2026-01-12",
        "actual_fitout_end_date": "2026-02-01",
        "actual_trading_date": "2026-02-05",
    }


def _mock_create_result(sr_id: str = "sr-ho-001") -> ServiceRequestCreationResult:
    return ServiceRequestCreationResult(
        sr_id=sr_id,
        endpoint="mock://service-requests",
        request_payload={},
        response_payload={"id": sr_id},
        latency_ms=10,
        status_code=201,
    )


# ---------------------------------------------------------------------------
# Payload shape
# ---------------------------------------------------------------------------


class TestHandedOverPayloadShape:
    def test_payload_builder_produces_correct_shape(self) -> None:
        cd = _full_handed_over_collected_data()
        result = build_create_handed_over_payload(cd, {})

        # sub_category is top-level (used by API router)
        assert result["sub_category"] == "HANDED_OVER"

        assert "payload" in result
        inner = result["payload"]

        # Path B-specific fields inside inner payload
        assert inner["already_handovered"] == "already_trading"
        assert inner["actual_shop_handover_date"] == "2026-01-10"
        assert inner["actual_fitout_start_date"] == "2026-01-12"
        assert inner["actual_fitout_end_date"] == "2026-02-01"
        assert inner["actual_trading_date"] == "2026-02-05"

        # Path A-specific fields must be absent from the inner payload
        assert "expected_handover_date" not in inner
        assert "trading_date" not in inner
        assert "fitout_start_date" not in inner
        assert "fitout_end_date" not in inner
        assert "actual_handover_date" not in inner

        # Structural required fields
        assert inner["title"] == "Already Trading - LC-HO-001"
        assert inner["property_id"] == 10
        assert inner["tenant_profile_id"] == 116

    def test_lease_id_is_top_level(self) -> None:
        cd = _full_handed_over_collected_data()
        result = build_create_handed_over_payload(cd, {})
        assert result["lease_id"] == 456

    def test_default_title_generated_when_missing(self) -> None:
        cd = {k: v for k, v in _full_handed_over_collected_data().items() if k != "title"}
        result = build_create_handed_over_payload(cd, {})
        assert "LC-HO-001" in result["payload"]["title"]


# ---------------------------------------------------------------------------
# payload_builder_node delegation
# ---------------------------------------------------------------------------


class TestPayloadBuilderNodeDelegation:
    @pytest.mark.asyncio
    async def test_delegates_to_handed_over_builder(self) -> None:
        state = _base_state(collected_data=_full_handed_over_collected_data())
        result = await payload_builder_node(state)

        assert "backend_refs" in result
        refs = result["backend_refs"]
        assert "create_payload" in refs
        # sub_category is at top level of the payload envelope
        assert refs["create_payload"]["sub_category"] == "HANDED_OVER"
        inner = refs["create_payload"]["payload"]
        assert inner["already_handovered"] == "already_trading"


# ---------------------------------------------------------------------------
# api_submission_node → HANDED_OVER_COMPLETED
# ---------------------------------------------------------------------------


class TestApiSubmissionHandedOver:
    @pytest.mark.asyncio
    async def test_handed_over_submission_goes_to_completed(self) -> None:
        cd = _full_handed_over_collected_data()
        state = _base_state(
            collected_data=cd,
            backend_refs={
                "create_payload": build_create_handed_over_payload(cd, {}),
            },
            sub_category="HANDED_OVER",
        )
        mock_result = _mock_create_result()
        with patch(
            "app.agents.graph.nodes.handover.api_submission_node.get_service_request_api_service"
        ) as mock_factory:
            mock_svc = AsyncMock()
            mock_svc.create_service_request = AsyncMock(return_value=mock_result)
            mock_factory.return_value = mock_svc
            result = await api_submission_node(state)

        # Path B immediately completes — no FM/RDD stages
        assert result["workflow_stage"] == "HANDED_OVER_COMPLETED"
        assert result["response_ui"]["workflow_complete"] is True
        assert result["backend_refs"]["sr_id"] == "sr-ho-001"

    @pytest.mark.asyncio
    async def test_path_a_submission_goes_to_sr_created(self) -> None:
        """Path A (HANDOVER) must NOT take the HANDED_OVER_COMPLETED shortcut."""
        from app.agents.services.payload_builder_service import build_create_handover_payload

        cd: dict[str, Any] = {
            "mall": "Dubai Mall",
            "brand": "BrandX",
            "lease_code": "LC-001",
            "title": "handover-LC-001",
            "lease_id": 456,
            "contract_id": 456,
            "property_id": 10,
            "tenant_profile_id": 116,
            "brand_id": 789,
            "unit_codes": ["U-101"],
            "contracted_area": 150.0,
            "city": "Dubai",
            "lease_brand_mall": "BrandX@Dubai Mall",
            "description": "test",
            "startDate": "2026-08-01",
            "endDate": "2026-09-01",
            "inspection_done_by": "FM_MANAGER",
            "comments": "",
        }
        state = _base_state(
            workflow_stage="CREATE_SR",
            sub_category="HANDOVER",
            collected_data=cd,
            backend_refs={"create_payload": build_create_handover_payload(cd, {})},
        )
        mock_result = _mock_create_result()
        with patch(
            "app.agents.graph.nodes.handover.api_submission_node.get_service_request_api_service"
        ) as mock_factory:
            mock_svc = AsyncMock()
            mock_svc.create_service_request = AsyncMock(return_value=mock_result)
            mock_factory.return_value = mock_svc
            result = await api_submission_node(state)

        # Path A should be in SR_CREATED (with MM upload offer), NOT HANDED_OVER_COMPLETED
        assert result["workflow_stage"] == "SR_CREATED"
        assert result["response_ui"].get("workflow_complete") is not True


# ---------------------------------------------------------------------------
# sr_status_sync_node Path B early exit
# ---------------------------------------------------------------------------


class TestSrStatusSyncPathB:
    @pytest.mark.asyncio
    async def test_handed_over_approved_short_circuits_to_sr_completed(self) -> None:
        """HANDED_OVER sub-category with APPROVED platform status → SR_COMPLETED immediately."""
        state = _base_state(
            workflow_stage="HANDED_OVER_COMPLETED",
            sub_category="HANDED_OVER",
            backend_refs={"sr_id": "sr-ho-001"},
        )
        mock_result = ServiceRequestCreationResult(
            sr_id="sr-ho-001",
            endpoint="mock://service-requests/sr-ho-001",
            request_payload={},
            response_payload={
                "_sr_status": "APPROVED",
                "_service_request_operations": [],
                "_sub_category": "HANDED_OVER",
            },
            latency_ms=5,
            status_code=200,
        )
        with patch(
            "app.agents.graph.nodes.shared.sr_status_sync_node.get_service_request_api_service"
        ) as mock_factory:
            mock_svc = AsyncMock()
            mock_svc.get_service_request = AsyncMock(return_value=mock_result)
            mock_factory.return_value = mock_svc
            result = await sr_status_sync_node(state)

        assert result["workflow_stage"] == "SR_COMPLETED"

    def test_map_operations_path_b_approved_returns_sr_completed(self) -> None:
        stage = _map_operations_to_stage(
            [],
            current_stage="HANDED_OVER_COMPLETED",
            sub_category="HANDED_OVER",
            platform_status="APPROVED",
        )
        assert stage == "SR_COMPLETED"

    def test_map_operations_path_a_in_progress_does_not_short_circuit(self) -> None:
        """Path A with an in-progress FM op must not hit the HANDED_OVER early exit."""
        ops = [{"role": "FM_MANAGER", "status": "IN_PROGRESS"}]
        stage = _map_operations_to_stage(
            ops,
            current_stage="FM_REVIEW",
            sub_category="HANDOVER",
            platform_status="IN_PROCESS",
        )
        assert stage == "FM_REVIEW"
        assert stage != "SR_COMPLETED"


# ---------------------------------------------------------------------------
# Workflow registry
# ---------------------------------------------------------------------------


class TestWorkflowRegistry:
    def test_handed_over_in_collection_stages(self) -> None:
        cfg = get_workflow_config("rdd_handover_agent")
        assert cfg is not None
        assert "CREATE_HANDED_OVER_SR" in cfg.collection_stages

    def test_handed_over_completed_in_terminal_stages(self) -> None:
        cfg = get_workflow_config("rdd_handover_agent")
        assert cfg is not None
        assert "HANDED_OVER_COMPLETED" in cfg.terminal_stages

    def test_handed_over_in_action_intents(self) -> None:
        cfg = get_workflow_config("rdd_handover_agent")
        assert cfg is not None
        assert "CREATE_HANDED_OVER_SR" in cfg.action_intents

    def test_confirmation_node_for_handed_over(self) -> None:
        cfg = get_workflow_config("rdd_handover_agent")
        assert cfg is not None
        assert cfg.confirmation_nodes.get("CREATE_HANDED_OVER_SR") == "confirmation"
