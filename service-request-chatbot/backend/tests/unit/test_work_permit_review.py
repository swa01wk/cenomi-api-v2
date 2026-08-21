"""Unit tests — Work Permit review hops (platform V1 workflow 5–8).

Covers stage mapping, PATCH payload, RBAC, graph routing, and sr_status_sync.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.graph.help_agent_graph import (
    _route_after_sync,
    _route_after_validation,
    _route_after_work_permit_entry,
    _SR_ACTION_INTENTS,
)
from app.agents.graph.nodes.shared.sr_status_sync_node import (
    _map_operations_to_stage,
    sr_status_sync_node,
)
from app.agents.graph.nodes.work_permit.work_permit_api_submission_node import (
    work_permit_api_submission_node,
)
from app.agents.graph.nodes.work_permit.work_permit_entry_node import (
    work_permit_entry_node,
)
from app.agents.graph.nodes.work_permit.work_permit_payload_builder_node import (
    build_create_work_permit_payload,
    build_review_work_permit_payload,
    work_permit_payload_builder_node,
)
from app.agents.registries.workflow_config import get_workflow_config
from app.agents.registry import lookup_agent
from app.agents.schemas.supervisor_schema import SupervisorDecision
from app.agents.schemas.work_permit_schema import (
    WP_APPROVED,
    WP_DD_REVIEW,
    WP_FM_REVIEW,
    WP_MM_OPS_REVIEW,
    WP_MM_REVIEW,
    WP_REJECTED,
    get_missing_fields,
    iso_to_ddmmyyyy,
    map_wp_operations_to_stage,
    normalize_wp_role,
)
from app.agents.services.permission_service import PermissionDeniedError, PermissionService
from app.agents.services.platform_api_client import ServiceRequestGetResult
from app.types.chat import AuthContext


def _auth(role: str) -> AuthContext:
    return AuthContext(subject_id=f"{role.lower()}-uuid", tenant_id=None, roles=frozenset({role}))


def _ops(*rows: tuple[str, str]) -> list[dict[str, Any]]:
    return [
        {"assigned_role": role, "workflow_level": i + 1, "status": status}
        for i, (role, status) in enumerate(rows)
    ]


# ---------------------------------------------------------------------------
# Stage mapping
# ---------------------------------------------------------------------------


class TestMapWpOperationsToStage:
    def test_hot_l1_dd(self) -> None:
        ops = _ops(
            ("DD_ENGINEER", "IN_PROGRESS"),
            ("FM_MANAGER", "YET_TO_START"),
            ("MALL_MANAGER", "YET_TO_START"),
        )
        assert map_wp_operations_to_stage(ops, sub_category="CONSTRUCTION_HOT") == WP_DD_REVIEW

    def test_hot_l1_dd_role_id_12(self) -> None:
        ops = [{"assigned_role": 12, "status": "IN_PROGRESS"}]
        assert map_wp_operations_to_stage(ops, sub_category="CONSTRUCTION_HOT") == WP_DD_REVIEW

    def test_hot_l2_fm(self) -> None:
        ops = _ops(
            ("DD_ENGINEER", "FINISHED"),
            ("FM_MANAGER", "IN_PROGRESS"),
            ("MALL_MANAGER", "YET_TO_START"),
        )
        assert map_wp_operations_to_stage(ops, sub_category="CONSTRUCTION_HOT") == WP_FM_REVIEW

    def test_hot_l3_mm(self) -> None:
        ops = _ops(
            ("DD_ENGINEER", "FINISHED"),
            ("FM_MANAGER", "FINISHED"),
            ("MALL_MANAGER", "IN_PROGRESS"),
        )
        assert map_wp_operations_to_stage(ops, sub_category="CONSTRUCTION_HOT") == WP_MM_REVIEW

    def test_cold_l2_multi_role(self) -> None:
        ops = [
            {"assigned_role": "DD_ENGINEER", "workflow_level": 1, "status": "FINISHED"},
            {"assigned_role": "MALL_MANAGER", "workflow_level": 2, "status": "IN_PROGRESS"},
            {"assigned_role": "OPERATIONS", "workflow_level": 2, "status": "IN_PROGRESS"},
            {"assigned_role": "OPERATION_EXECUTIVES", "workflow_level": 2, "status": "IN_PROGRESS"},
        ]
        assert map_wp_operations_to_stage(ops, sub_category="CONSTRUCTION_COLD") == WP_MM_OPS_REVIEW

    def test_maintenance_l1_fm(self) -> None:
        ops = _ops(("FM_MANAGER", "IN_PROGRESS"), ("MALL_MANAGER", "YET_TO_START"))
        assert map_wp_operations_to_stage(ops, sub_category="MAINTENANCE_HOT") == WP_FM_REVIEW

    def test_operations_l1_mm(self) -> None:
        ops = _ops(("MALL_MANAGER", "IN_PROGRESS"),)
        assert map_wp_operations_to_stage(ops, sub_category="OPERATIONS") == WP_MM_REVIEW

    def test_roof_access_shares_hot_hops(self) -> None:
        ops = _ops(
            ("DD_ENGINEER", "IN_PROGRESS"),
            ("FM_MANAGER", "YET_TO_START"),
            ("MALL_MANAGER", "YET_TO_START"),
        )
        assert (
            map_wp_operations_to_stage(ops, sub_category="CONSTRUCTION_ROOF_ACCESS")
            == WP_DD_REVIEW
        )
        assert (
            map_wp_operations_to_stage(ops, sub_category="MAINTENANCE_ROOF_ACCESS")
            == WP_DD_REVIEW
        )

    def test_approved_terminal(self) -> None:
        ops = _ops(("MALL_MANAGER", "FINISHED"),)
        assert (
            map_wp_operations_to_stage(
                ops, sub_category="OPERATIONS", platform_status="APPROVED"
            )
            == WP_APPROVED
        )

    def test_rejected_terminal(self) -> None:
        assert (
            map_wp_operations_to_stage(
                [], sub_category="CONSTRUCTION_HOT", platform_status="REJECTED"
            )
            == WP_REJECTED
        )

    def test_dd_does_not_map_to_handover_rdd_review(self) -> None:
        ops = [{"assigned_role": "DD_ENGINEER", "status": "IN_PROGRESS"}]
        stage = _map_operations_to_stage(
            ops,
            None,
            sub_category="CONSTRUCTION_HOT",
            platform_status="IN_PROCESS",
            category="WORK_PERMIT",
        )
        assert stage == WP_DD_REVIEW
        assert stage != "RDD_REVIEW"


class TestNormalizeWpRole:
    def test_role_id_11_is_mall_manager(self) -> None:
        assert normalize_wp_role(11) == "MALL_MANAGER"

    def test_role_id_12_is_dd(self) -> None:
        assert normalize_wp_role("12") == "DD_ENGINEER"

    def test_role_id_20_is_fm(self) -> None:
        assert normalize_wp_role(20) == "FM_MANAGER"

    def test_name_passthrough(self) -> None:
        assert normalize_wp_role("operation_executives") == "OPERATION_EXECUTIVES"


class TestWpIsoDate:
    def test_iso_to_ddmmyyyy(self) -> None:
        assert iso_to_ddmmyyyy("2026-08-20") == "20/08/2026"


class TestWpMissingFieldsReview:
    def test_review_stage_has_no_required_fields(self) -> None:
        assert get_missing_fields(WP_DD_REVIEW, {}) == []


# ---------------------------------------------------------------------------
# Registry / workflow config
# ---------------------------------------------------------------------------


class TestWpRegistryAndConfig:
    def test_subtypes_map_to_work_permit_agent(self) -> None:
        for sub in (
            "WORK_PERMIT",
            "CONSTRUCTION_HOT",
            "CONSTRUCTION_COLD",
            "CONSTRUCTION_ROOF_ACCESS",
            "MAINTENANCE_HOT",
            "MAINTENANCE_COLD",
            "MAINTENANCE_ROOF_ACCESS",
            "OPERATIONS",
        ):
            cfg = lookup_agent("WORK_PERMIT", sub)
            assert cfg is not None
            assert cfg["agent_name"] == "work_permit_agent"

    def test_workflow_config_review_sync_nodes(self) -> None:
        cfg = get_workflow_config("work_permit_agent")
        assert cfg is not None
        assert cfg.stage_sync_nodes[WP_DD_REVIEW] == "work_permit_entry"
        assert cfg.stage_sync_nodes[WP_MM_OPS_REVIEW] == "work_permit_entry"
        assert "APPROVE_WORK_PERMIT_SR" in cfg.action_intents
        assert "APPROVE_WORK_PERMIT_SR" in _SR_ACTION_INTENTS

    def test_supervisor_literal_accepts_wp_intents(self) -> None:
        for intent in ("CREATE_WORK_PERMIT_SR", "APPROVE_WORK_PERMIT_SR"):
            decision = SupervisorDecision(
                intent=intent,  # type: ignore[arg-type]
                confidence=0.9,
                reasoning="test",
            )
            assert decision.intent == intent


# ---------------------------------------------------------------------------
# Graph routing
# ---------------------------------------------------------------------------


class TestWpGraphRouting:
    def test_sync_wp_dd_review_routes_to_wp_entry(self) -> None:
        state = {
            "active_agent": "work_permit_agent",
            "workflow_stage": WP_DD_REVIEW,
            "backend_refs": {"sr_id": "10001"},
        }
        assert _route_after_sync(state) == "work_permit_entry"

    def test_confirm_routes_to_merge_not_waiting(self) -> None:
        state = {
            "confirmation_status": "CONFIRMED",
            "status": "WAITING_FOR_USER",
            "workflow_stage": WP_DD_REVIEW,
        }
        assert _route_after_work_permit_entry(state) == "merge_state"

    def test_review_open_skips_field_extraction(self) -> None:
        state = {
            "confirmation_status": None,
            "status": "IN_PROGRESS",
            "workflow_stage": WP_FM_REVIEW,
        }
        assert _route_after_work_permit_entry(state) == "merge_state"

    def test_validation_review_goes_to_wp_confirmation(self) -> None:
        state = {
            "active_agent": "work_permit_agent",
            "workflow_stage": WP_DD_REVIEW,
            "collected_data": {},
            "validation_errors": [],
            "confirmation_status": None,
        }
        assert _route_after_validation(state) == "work_permit_confirmation"


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


class TestWpPayload:
    def _create_data(self) -> dict[str, Any]:
        return {
            "lease_code": "t0105712",
            "lease_id": 456,
            "property_id": 789,
            "tenant_profile_id": 1,
            "unit_codes": ["FF050"],
            "mall": "Jawharat Jeddah",
            "brand": "Under Armour",
            "work_permit_type": "CONSTRUCTION_HOT",
            "description": "Hot work",
            "start_date": "2026-08-20",
            "end_date": "2026-08-20",
            "contractor_name": "ABC",
        }

    def test_create_payload_is_nested_platform_shape(self) -> None:
        payload = build_create_work_permit_payload(self._create_data())
        assert payload["service_category"] == "WORK_PERMIT"
        assert payload["sub_category"] == "CONSTRUCTION_HOT"
        assert payload["lease_id"] == 456
        assert "payload" in payload
        assert payload["payload"]["planneddate"]["worktimedate"] == "20/08/2026"
        assert "workPermitType" not in payload
        assert "serviceCategory" not in payload

    def test_approve_patch_requires_payload(self) -> None:
        body = build_review_work_permit_payload(
            sub_category="CONSTRUCTION_HOT",
            inner_payload={"contractor_name": "ABC"},
            approve=True,
            comment="OK",
        )
        assert body["status"] == "APPROVED"
        assert body["payload"] == {"contractor_name": "ABC"}
        assert body["sub_category"] == "CONSTRUCTION_HOT"

    def test_reject_patch_omits_payload(self) -> None:
        body = build_review_work_permit_payload(
            sub_category="CONSTRUCTION_HOT",
            inner_payload={"contractor_name": "ABC"},
            approve=False,
            comment="Unsafe",
        )
        assert body["status"] == "REJECTED"
        assert "payload" not in body
        assert body["comment"] == "Unsafe"


@pytest.mark.asyncio
class TestWpPayloadBuilderNode:
    async def test_review_sets_reject_body(self) -> None:
        state = {
            "workflow_stage": WP_DD_REVIEW,
            "sub_category": "CONSTRUCTION_HOT",
            "collected_data": {"comments": "no"},
            "backend_refs": {
                "wp_action": "reject",
                "create_payload": {"payload": {"lease": "t1"}, "sub_category": "CONSTRUCTION_HOT"},
            },
        }
        result = await work_permit_payload_builder_node(state)
        body = result["backend_refs"]["create_payload"]
        assert body["status"] == "REJECTED"


# ---------------------------------------------------------------------------
# Entry + submission
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestWpEntryReviewActions:
    async def test_confirm_approve_wp_sets_action(self) -> None:
        state = {
            "workflow_stage": WP_DD_REVIEW,
            "action_override": "confirm_approve_wp",
            "backend_refs": {},
        }
        result = await work_permit_entry_node(state)
        assert result["confirmation_status"] == "CONFIRMED"
        assert result["backend_refs"]["wp_action"] == "approve"
        assert result["status"] == "IN_PROGRESS"

    async def test_reject_wp_sets_action(self) -> None:
        state = {
            "workflow_stage": WP_FM_REVIEW,
            "action_override": "reject_wp",
            "backend_refs": {},
        }
        result = await work_permit_entry_node(state)
        assert result["backend_refs"]["wp_action"] == "reject"


class TestWpApprovePermission:
    def test_dd_can_approve_dd_hop(self) -> None:
        PermissionService().ensure_can_approve_work_permit(
            _auth("DD_ENGINEER"),
            workflow_stage=WP_DD_REVIEW,
            in_progress_roles={"DD_ENGINEER"},
        )

    def test_fm_cannot_approve_dd_hop(self) -> None:
        with pytest.raises(PermissionDeniedError):
            PermissionService().ensure_can_approve_work_permit(
                _auth("FM_MANAGER"),
                workflow_stage=WP_DD_REVIEW,
                in_progress_roles={"DD_ENGINEER"},
            )

    def test_ops_can_approve_cold_l2(self) -> None:
        PermissionService().ensure_can_approve_work_permit(
            _auth("OPERATIONS"),
            workflow_stage=WP_MM_OPS_REVIEW,
            in_progress_roles={"MALL_MANAGER", "OPERATIONS"},
        )


@pytest.mark.asyncio
class TestWpReviewSubmission:
    async def test_dd_patch_approve(self) -> None:
        mock_patch = AsyncMock()
        mock_patch.sr_id = "10001"
        mock_patch.error = None
        mock_patch.correlation_id = None
        mock_patch.status_code = 200

        mock_get = ServiceRequestGetResult(
            success=True,
            sr_id="10001",
            status="IN_PROCESS",
            service_request_operations=_ops(
                ("DD_ENGINEER", "FINISHED"),
                ("FM_MANAGER", "IN_PROGRESS"),
            ),
        )

        state = {
            "workflow_stage": WP_DD_REVIEW,
            "confirmation_status": "CONFIRMED",
            "validation_errors": [],
            "backend_refs": {
                "sr_id": "10001",
                "wp_action": "approve",
                "sr_operations": _ops(("DD_ENGINEER", "IN_PROGRESS")),
                "create_payload": {
                    "status": "APPROVED",
                    "sub_category": "CONSTRUCTION_HOT",
                    "payload": {},
                },
            },
            "auth_context": _auth("DD_ENGINEER"),
        }

        with patch(
            "app.agents.graph.nodes.work_permit.work_permit_api_submission_node.get_service_request_api_service"
        ) as factory:
            svc = AsyncMock()
            svc.patch_service_request.return_value = mock_patch
            svc.get_service_request.return_value = mock_get
            factory.return_value = svc
            result = await work_permit_api_submission_node(state)

        assert result["workflow_stage"] == WP_FM_REVIEW
        assert "permission" not in (result.get("response_message") or "").lower()

    async def test_fm_denied_on_dd_hop(self) -> None:
        state = {
            "workflow_stage": WP_DD_REVIEW,
            "confirmation_status": "CONFIRMED",
            "validation_errors": [],
            "backend_refs": {
                "sr_id": "10001",
                "sr_operations": _ops(("DD_ENGINEER", "IN_PROGRESS")),
                "create_payload": {"status": "APPROVED", "sub_category": "CONSTRUCTION_HOT", "payload": {}},
            },
            "auth_context": _auth("FM_MANAGER"),
        }
        result = await work_permit_api_submission_node(state)
        assert result["status"] == "FAILED"
        assert "permission" in result["response_message"].lower()


@pytest.mark.asyncio
class TestWpStatusSync:
    async def test_open_hot_sr_sets_dd_review_and_agent(self) -> None:
        result_dto = ServiceRequestGetResult(
            success=True,
            sr_id="10001",
            status="IN_PROCESS",
            payload={
                "category": "WORK_PERMIT",
                "sub_category": "CONSTRUCTION_HOT",
                "contractor_name": "ABC",
                "lease_code": "t0105712",
            },
            service_request_operations=_ops(
                ("DD_ENGINEER", "IN_PROGRESS"),
                ("FM_MANAGER", "YET_TO_START"),
            ),
        )
        state = {
            "backend_refs": {"sr_id": "10001"},
            "auth_context": _auth("DD_ENGINEER"),
            "collected_data": {},
        }
        with patch(
            "app.agents.graph.nodes.shared.sr_status_sync_node.get_service_request_api_service"
        ) as factory:
            svc = AsyncMock()
            svc.get_service_request.return_value = result_dto
            factory.return_value = svc
            updates = await sr_status_sync_node(state)

        assert updates["workflow_stage"] == WP_DD_REVIEW
        assert updates["active_agent"] == "work_permit_agent"
        assert updates["service_category"] == "WORK_PERMIT"
        assert updates["sub_category"] == "CONSTRUCTION_HOT"
        assert updates["backend_refs"]["create_payload"]["service_category"] == "WORK_PERMIT"


@pytest.mark.asyncio
class TestWpMockApiHops:
    async def test_hot_create_seeds_dd_in_progress(self) -> None:
        from app.agents.services.service_request_api_service import MockServiceRequestAPIService

        svc = MockServiceRequestAPIService(isolated=True)
        created = await svc.create_service_request(
            {"service_category": "WORK_PERMIT", "sub_category": "CONSTRUCTION_HOT", "payload": {}}
        )
        got = await svc.get_service_request(created.sr_id or "")
        assert got.status == "IN_PROCESS"
        assert got.payload and got.payload.get("category") == "WORK_PERMIT"
        roles = {
            (op["assigned_role"], op["status"], op["workflow_level"])
            for op in got.service_request_operations
        }
        assert ("DD_ENGINEER", "IN_PROGRESS", 1) in roles
        assert ("FM_MANAGER", "YET_TO_START", 2) in roles

    async def test_hot_approve_advances_to_fm_then_mm_then_approved(self) -> None:
        from app.agents.services.service_request_api_service import MockServiceRequestAPIService

        svc = MockServiceRequestAPIService(isolated=True)
        created = await svc.create_service_request(
            {"service_category": "WORK_PERMIT", "sub_category": "CONSTRUCTION_HOT", "payload": {}}
        )
        sr_id = created.sr_id or ""
        await svc.patch_service_request(sr_id, {"status": "APPROVED", "sub_category": "CONSTRUCTION_HOT"})
        got = await svc.get_service_request(sr_id)
        assert got.status == "IN_PROCESS"
        in_progress = [op["assigned_role"] for op in got.service_request_operations if op["status"] == "IN_PROGRESS"]
        assert in_progress == ["FM_MANAGER"]

        await svc.patch_service_request(sr_id, {"status": "APPROVED"})
        got = await svc.get_service_request(sr_id)
        in_progress = [op["assigned_role"] for op in got.service_request_operations if op["status"] == "IN_PROGRESS"]
        assert in_progress == ["MALL_MANAGER"]

        await svc.patch_service_request(sr_id, {"status": "APPROVED"})
        got = await svc.get_service_request(sr_id)
        assert got.status == "APPROVED"

    async def test_reject_sets_rejected(self) -> None:
        from app.agents.services.service_request_api_service import MockServiceRequestAPIService

        svc = MockServiceRequestAPIService(isolated=True)
        created = await svc.create_service_request(
            {"service_category": "WORK_PERMIT", "sub_category": "CONSTRUCTION_HOT", "payload": {}}
        )
        await svc.patch_service_request(created.sr_id or "", {"status": "REJECTED"})
        got = await svc.get_service_request(created.sr_id or "")
        assert got.status == "REJECTED"


def test_eval_tier9_scenario_ids_registered() -> None:
    from tests.eval.scenarios import TIER0_SCENARIOS, TIER9_SCENARIOS
    from tests.eval.lifecycle_scenarios import TIER9_LIFECYCLE_SCENARIOS

    assert any(s.id == 207 for s in TIER0_SCENARIOS)
    assert {s.id for s in TIER9_SCENARIOS} == {240, 241, 242, 243, 244}
    assert {s.id for s in TIER9_LIFECYCLE_SCENARIOS} == {240, 241, 242, 243, 244, 245}
