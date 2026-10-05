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
    WP_CREATED,
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
    def _create_hot_data(self) -> dict[str, Any]:
        return {
            "lease_code": "t0105712",
            "lease_id": 456,
            "property_id": 789,
            "tenant_profile_id": 1,
            "unit_codes": ["FF050"],
            "mall": "Jawharat Jeddah",
            "brand": "Under Armour",
            "unit_type": "Retail",
            "work_permit_type": "CONSTRUCTION_HOT",
            "contractor_name": "ABC",
            "phone": "501234567",
            "planneddate": {
                "worktimedate": "20/08/2026",
                "completionworkdate": "20/08/2026",
                "worktimefrom": "09:00",
                "worktimeto": "17:00",
                "completionfrom": "",
                "completionto": "",
            },
            "workers": {
                "instructor_name": "Ali",
                "worker": [
                    {
                        "name": "Omar",
                        "designation": "Welder",
                        "iqama_number": "123",
                        "document_id": "",
                        "worker_qualification": "",
                    }
                ],
            },
            "measureofsafety": {
                "fire_extinguisher": "yes",
                "fire_hoses": "",
                "sprinker_on": "",
                "volatile_remove": "",
                "fire_proofcoating": "",
                "work_hour_organized": "",
                "equipment_for_carryingout": "",
                "all_Holes_in_floorandWalls": "",
            },
        }

    def _create_cold_data(self) -> dict[str, Any]:
        return {
            "lease_code": "t0105712",
            "lease_id": 456,
            "property_id": 789,
            "tenant_profile_id": 1,
            "unit_codes": ["FF050"],
            "mall": "Jawharat Jeddah",
            "brand": "Under Armour",
            "unit_type": "Retail",
            "work_permit_type": "CONSTRUCTION_COLD",
            "department": "Fit-out",
            "owner": "Owner Co",
            "trade_name": "UA Trade",
            "responsible_person": "Sara",
            "phone": "501234567",
            "type_work": "Partition install",
            "list_equipment": "Drill, ladder",
            "work_info": "Night work",
            "planneddate": {
                "worktimedate": "01/08/2026",
                "completionworkdate": "05/08/2026",
                "worktimefrom": "08:00",
                "worktimeto": "16:00",
                "completionfrom": "",
                "completionto": "",
            },
            "workers": [
                {
                    "full_name": "Omar",
                    "designation": "Tech",
                    "company_name": "ABC",
                    "phone": "501111111",
                    "country_code": "966",
                }
            ],
        }

    def test_create_payload_is_nested_platform_shape(self) -> None:
        payload = build_create_work_permit_payload(self._create_hot_data())
        assert payload["service_category"] == "WORK_PERMIT"
        assert payload["sub_category"] == "CONSTRUCTION_HOT"
        assert payload["lease_id"] == 456
        assert "payload" in payload
        inner = payload["payload"]
        assert inner["planneddate"]["worktimedate"] == "20/08/2026"
        assert inner["contractor_name"] == "ABC"
        assert inner["workers"]["worker"][0]["name"] == "Omar"
        assert inner["measureofsafety"]["fire_extinguisher"] == "yes"
        assert "workPermitType" not in payload
        assert "serviceCategory" not in payload

    def test_create_cold_payload_matches_form_keys(self) -> None:
        payload = build_create_work_permit_payload(self._create_cold_data())
        inner = payload["payload"]
        assert payload["sub_category"] == "CONSTRUCTION_COLD"
        assert inner["type_work"] == "Partition install"
        assert inner["list_equipment"] == "Drill, ladder"
        assert isinstance(inner["workers"], list)
        assert inner["workers"][0]["full_name"] == "Omar"
        assert "measureofsafety" not in inner

    def _create_ops_data(self) -> dict[str, Any]:
        return {
            "lease_code": "t0030610",
            "lease_id": 456,
            "property_id": 789,
            "tenant_profile_id": 1038,
            "unit_codes": ["GF149"],
            "mall": "Juri Mall",
            "brand": "Brand Gap",
            "work_permit_type": "OPERATIONS",
            "unit_type": "Store",
            "department": "operations",
            "phone": "74395749393",
            "permission_given_to": "Kishan",
            "typeofwork": {"value": ["construction"], "otheroption": ""},
            "effective_starting": {
                "datefrom": "24/09/2026",
                "dateto": "24/09/2026",
            },
            "firstperiod": {"from": "16:00", "to": "17:00"},
        }

    def test_ops_company_name_is_int_tenant_profile_id(self) -> None:
        data = self._create_ops_data()
        assert "company_name" not in data
        inner = build_create_work_permit_payload(data)["payload"]
        assert inner["company_name"] == 1038
        assert isinstance(inner["company_name"], int)

    def test_ops_firstperiod_hhmm_expanded_to_iso(self) -> None:
        inner = build_create_work_permit_payload(self._create_ops_data())["payload"]
        period = inner["firstperiod"]
        assert period["from"] == "2026-09-24T16:00:00.000Z"
        assert period["to"] == "2026-09-24T17:00:00.000Z"
        assert period["fromLT"] == "4:00 PM"
        assert period["toLT"] == "5:00 PM"
        # to is after from (Go is_not_before_time)
        assert period["to"] > period["from"]

    def test_ops_empty_secondperiod_stays_empty(self) -> None:
        data = self._create_ops_data()
        data["secondperiod"] = {"from": "", "to": ""}
        inner = build_create_work_permit_payload(data)["payload"]
        assert inner["secondperiod"] == {"from": "", "to": ""}

    def test_ops_already_iso_period_left_alone(self) -> None:
        data = self._create_ops_data()
        data["firstperiod"] = {
            "from": "2026-09-24T16:00:00.000Z",
            "to": "2026-09-24T17:00:00.000Z",
            "fromLT": "4:00 PM",
            "toLT": "5:00 PM",
        }
        inner = build_create_work_permit_payload(data)["payload"]
        assert inner["firstperiod"]["from"] == "2026-09-24T16:00:00.000Z"
        assert inner["firstperiod"]["to"] == "2026-09-24T17:00:00.000Z"
        assert inner["firstperiod"]["fromLT"] == "4:00 PM"

    def test_ops_inner_title_max_50(self) -> None:
        data = self._create_ops_data()
        data["title"] = "X" * 80
        payload = build_create_work_permit_payload(data)
        # Outer title truncates the chat-collected value.
        assert len(payload["title"]) == 50
        assert payload["title"].endswith("…")
        # Ops form common_keys omit title → lease-code-only fallback (short).
        assert payload["payload"]["title"] == "Operations – t0030610"
        assert len(payload["payload"]["title"]) <= 50

    def test_hot_inner_title_truncates_long_user_value(self) -> None:
        data = self._create_hot_data()
        data["title"] = "X" * 80
        payload = build_create_work_permit_payload(data)
        assert len(payload["title"]) == 50
        assert payload["title"].endswith("…")
        assert len(payload["payload"]["title"]) == 50
        assert payload["payload"]["title"].endswith("…")

    def test_ops_empty_company_name_still_uses_tenant_profile_id(self) -> None:
        data = self._create_ops_data()
        data["company_name"] = ""
        inner = build_create_work_permit_payload(data)["payload"]
        assert inner["company_name"] == 1038
        assert isinstance(inner["company_name"], int)

    def test_ops_omits_company_name_without_tenant_profile_id(self) -> None:
        data = self._create_ops_data()
        del data["tenant_profile_id"]
        data["company_name"] = ""
        inner = build_create_work_permit_payload(data)["payload"]
        assert "company_name" not in inner

    def test_hot_company_name_is_int_tenant_profile_id(self) -> None:
        inner = build_create_work_permit_payload(self._create_hot_data())["payload"]
        assert inner["company_name"] == 1
        assert isinstance(inner["company_name"], int)

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

    def test_approve_patch_strips_inner_envelope_keys(self) -> None:
        body = build_review_work_permit_payload(
            sub_category="OPERATIONS",
            inner_payload={
                "category": "WORK_PERMIT",
                "service_category": "WORK_PERMIT",
                "lease_id": 999,
                "lease_code": "L-1",
                "tenant_profile_id": 1,
                "property_id": 2,
                "sub_category": "OPERATIONS",
                "workflow_version": "v1",
                "contract_id": "c1",
                "contractor_name": "ABC",
                "title": "Ops work",
            },
            approve=True,
            comment="",
        )
        assert body["status"] == "APPROVED"
        assert body["payload"] == {
            "contractor_name": "ABC",
            "title": "Ops work",
        }
        for key in (
            "category",
            "service_category",
            "lease_id",
            "lease_code",
            "tenant_profile_id",
            "property_id",
            "sub_category",
            "workflow_version",
            "contract_id",
        ):
            assert key not in body["payload"]

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
        assert result.get("active_agent") == "work_permit_agent"
        ui = result.get("response_ui") or {}
        assert ui.get("type") == "message"
        assert ui.get("stage_complete") is True
        assert ui.get("action") == "wp_hop_approved"
        assert ui.get("workflow_stage") == WP_FM_REVIEW
        assert result.get("confirmation_status") is None
        assert result.get("confirmation_required") is False

    async def test_fm_patch_approve_advances_to_mm_ops_with_stage_complete(self) -> None:
        """FM after approve must not leave a confirmation_card — message + stage_complete."""
        mock_patch = AsyncMock()
        mock_patch.sr_id = "341510"
        mock_patch.error = None
        mock_patch.correlation_id = None
        mock_patch.status_code = 200

        mock_get = ServiceRequestGetResult(
            success=True,
            sr_id="341510",
            status="IN_PROCESS",
            service_request_operations=_ops(
                ("FM_MANAGER", "FINISHED"),
                ("MALL_MANAGER", "IN_PROGRESS"),
                ("OPERATIONS", "IN_PROGRESS"),
            ),
        )

        state = {
            "workflow_stage": WP_FM_REVIEW,
            "confirmation_status": "CONFIRMED",
            "validation_errors": [],
            "sub_category": "CONSTRUCTION_COLD",
            "backend_refs": {
                "sr_id": "341510",
                "wp_action": "approve",
                "sr_operations": _ops(("FM_MANAGER", "IN_PROGRESS")),
                "create_payload": {
                    "status": "APPROVED",
                    "sub_category": "CONSTRUCTION_COLD",
                    "payload": {},
                },
            },
            "auth_context": _auth("FM_MANAGER"),
        }

        with patch(
            "app.agents.graph.nodes.work_permit.work_permit_api_submission_node.get_service_request_api_service"
        ) as factory:
            svc = AsyncMock()
            svc.patch_service_request.return_value = mock_patch
            svc.get_service_request.return_value = mock_get
            factory.return_value = svc
            result = await work_permit_api_submission_node(state)

        assert result["workflow_stage"] == WP_MM_OPS_REVIEW
        assert result.get("active_agent") == "work_permit_agent"
        ui = result.get("response_ui") or {}
        assert ui.get("type") == "message"
        assert ui.get("stage_complete") is True
        assert ui.get("action") == "wp_hop_approved"
        assert "confirmAction" not in ui
        assert "Mall Manager" in (result.get("response_message") or "")

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

    async def test_operations_create_auto_approve_maps_to_wp_approved(self) -> None:
        mock_create = AsyncMock()
        mock_create.sr_id = "10099"
        mock_create.error = None
        mock_create.correlation_id = None
        mock_create.status_code = 201

        mock_get = ServiceRequestGetResult(
            success=True,
            sr_id="10099",
            status="APPROVED",
            service_request_operations=_ops(("MALL_MANAGER", "FINISHED")),
        )

        state = {
            "workflow_stage": "CREATE_WORK_PERMIT",
            "confirmation_status": "CONFIRMED",
            "validation_errors": [],
            "backend_refs": {
                "create_payload": {
                    "service_category": "WORK_PERMIT",
                    "sub_category": "OPERATIONS",
                    "payload": {},
                }
            },
            "auth_context": _auth("MALL_MANAGER"),
        }

        with patch(
            "app.agents.graph.nodes.work_permit.work_permit_api_submission_node.get_service_request_api_service"
        ) as factory:
            svc = AsyncMock()
            svc.create_service_request.return_value = mock_create
            svc.get_service_request.return_value = mock_get
            factory.return_value = svc
            result = await work_permit_api_submission_node(state)

        assert result["status"] == "SUBMITTED"
        assert result["workflow_stage"] == WP_APPROVED
        assert "auto-approved" in result["response_message"].lower()
        ui = result.get("response_ui") or {}
        assert ui.get("type") == "message"
        assert ui.get("message") == result["response_message"]
        assert ui.get("workflow_complete") is True
        assert ui.get("workflow_stage") == WP_APPROVED

    async def test_create_stays_wp_created_when_post_create_get_fails(self) -> None:
        mock_create = AsyncMock()
        mock_create.sr_id = "10100"
        mock_create.error = None
        mock_create.correlation_id = None
        mock_create.status_code = 201

        state = {
            "workflow_stage": "CREATE_WORK_PERMIT",
            "confirmation_status": "CONFIRMED",
            "validation_errors": [],
            "backend_refs": {
                "create_payload": {
                    "service_category": "WORK_PERMIT",
                    "sub_category": "OPERATIONS",
                    "payload": {},
                }
            },
            "auth_context": _auth("MALL_MANAGER"),
        }

        with patch(
            "app.agents.graph.nodes.work_permit.work_permit_api_submission_node.get_service_request_api_service"
        ) as factory:
            svc = AsyncMock()
            svc.create_service_request.return_value = mock_create
            svc.get_service_request.side_effect = RuntimeError("GET failed")
            factory.return_value = svc
            result = await work_permit_api_submission_node(state)

        assert result["status"] == "SUBMITTED"
        assert result["workflow_stage"] == WP_CREATED
        assert "auto-approved" not in result["response_message"].lower()
        ui = result.get("response_ui") or {}
        assert ui.get("type") == "message"
        assert ui.get("message") == result["response_message"]
        assert "10100" in ui["message"]
        assert ui.get("workflow_complete") is True
        assert ui.get("workflow_stage") == WP_CREATED

    async def test_review_approve_500_mentions_permit_pdf(self) -> None:
        mock_patch = AsyncMock()
        mock_patch.sr_id = None
        mock_patch.error = "HTTP 500: an internal error occurred"
        mock_patch.correlation_id = None
        mock_patch.status_code = 500
        mock_patch.response_payload = {
            "success": False,
            "errors": [{"code": 500, "message": "an internal error occurred"}],
        }

        state = {
            "workflow_stage": WP_MM_OPS_REVIEW,
            "confirmation_status": "CONFIRMED",
            "validation_errors": [],
            "backend_refs": {
                "sr_id": "341518",
                "wp_action": "approve",
                "create_payload": {
                    "status": "APPROVED",
                    "sub_category": "OPERATIONS",
                    "payload": {"contractor_name": "ABC"},
                },
            },
            "auth_context": _auth("MALL_MANAGER"),
        }

        with patch(
            "app.agents.graph.nodes.work_permit.work_permit_api_submission_node.get_service_request_api_service"
        ) as factory:
            svc = AsyncMock()
            svc.patch_service_request.return_value = mock_patch
            factory.return_value = svc
            result = await work_permit_api_submission_node(state)

        assert result["status"] == "FAILED"
        msg = result.get("response_message") or ""
        assert "permit PDF" in msg
        assert "HTTP 500" in msg
        assert "Unable to submit the work permit request due to an API error" not in msg

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


# ---------------------------------------------------------------------------
# Confirmation / entry role gates (Handover parity)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestWpConfirmationRoleGate:
    async def test_fm_on_mm_ops_gets_status_message_not_card(self) -> None:
        from app.agents.graph.nodes.work_permit.work_permit_confirmation_node import (
            work_permit_confirmation_node,
        )

        state = {
            "workflow_stage": WP_MM_OPS_REVIEW,
            "collected_data": {"lease_code": "t1", "description": "x"},
            "backend_refs": {
                "sr_id": "341510",
                "sr_operations": _ops(
                    ("FM_MANAGER", "FINISHED"),
                    ("MALL_MANAGER", "IN_PROGRESS"),
                    ("OPERATIONS", "IN_PROGRESS"),
                ),
            },
            "auth_context": _auth("FM_MANAGER"),
        }
        result = await work_permit_confirmation_node(state)
        ui = result.get("response_ui") or {}
        assert ui.get("type") == "message"
        assert "confirmAction" not in ui
        assert "rejectAction" not in ui
        assert result.get("confirmation_status") is None
        assert result.get("confirmation_required") is False
        assert "Mall Manager" in (result.get("response_message") or "")

    async def test_mall_manager_on_mm_ops_gets_approve_card(self) -> None:
        from app.agents.graph.nodes.work_permit.work_permit_confirmation_node import (
            work_permit_confirmation_node,
        )

        state = {
            "workflow_stage": WP_MM_OPS_REVIEW,
            "collected_data": {"lease_code": "t1", "description": "x"},
            "backend_refs": {
                "sr_id": "341510",
                "sr_operations": _ops(("MALL_MANAGER", "IN_PROGRESS")),
            },
            "auth_context": _auth("MALL_MANAGER"),
        }
        result = await work_permit_confirmation_node(state)
        ui = result.get("response_ui") or {}
        assert ui.get("type") == "confirmation_card"
        assert ui.get("confirmAction") == "confirm_approve_wp"
        assert ui.get("rejectAction") == "reject_wp"
        assert result.get("confirmation_status") == "PENDING"

    async def test_operations_on_mm_ops_gets_approve_card(self) -> None:
        from app.agents.graph.nodes.work_permit.work_permit_confirmation_node import (
            work_permit_confirmation_node,
        )

        state = {
            "workflow_stage": WP_MM_OPS_REVIEW,
            "collected_data": {"lease_code": "t1"},
            "backend_refs": {
                "sr_id": "1",
                "sr_operations": _ops(("OPERATIONS", "IN_PROGRESS")),
            },
            "auth_context": _auth("OPERATIONS"),
        }
        result = await work_permit_confirmation_node(state)
        assert result["response_ui"]["confirmAction"] == "confirm_approve_wp"


@pytest.mark.asyncio
class TestWpEntryRoleGate:
    async def test_fm_denied_confirm_approve_on_mm_ops(self) -> None:
        state = {
            "workflow_stage": WP_MM_OPS_REVIEW,
            "action_override": "confirm_approve_wp",
            "backend_refs": {
                "sr_operations": _ops(("MALL_MANAGER", "IN_PROGRESS")),
            },
            "auth_context": _auth("FM_MANAGER"),
        }
        result = await work_permit_entry_node(state)
        assert result.get("confirmation_status") != "CONFIRMED"
        assert result.get("status") == "WAITING_FOR_USER"
        assert "permission" in (result.get("response_message") or "").lower()

    async def test_mall_manager_confirm_approve_allowed(self) -> None:
        state = {
            "workflow_stage": WP_MM_OPS_REVIEW,
            "action_override": "confirm_approve_wp",
            "backend_refs": {
                "sr_operations": _ops(("MALL_MANAGER", "IN_PROGRESS")),
            },
            "auth_context": _auth("MALL_MANAGER"),
        }
        result = await work_permit_entry_node(state)
        assert result["confirmation_status"] == "CONFIRMED"
        assert result["backend_refs"]["wp_action"] == "approve"


class TestWpReviewDecorationsNonActor:
    def test_view_sr_suggestion_for_fm_on_mm_ops(self) -> None:
        from app.agents.services.review_decorations_service import build_review_decorations

        deco = build_review_decorations(
            {
                "workflow_stage": WP_MM_OPS_REVIEW,
                "backend_refs": {"sr_id": "341510", "user_role": "FM_MANAGER"},
                "auth_context": _auth("FM_MANAGER"),
            }
        )
        assert deco.get("promote_preview") is True
        suggestions = deco.get("suggestions") or {}
        options = suggestions.get("options") or []
        assert any(o.get("action") == "view_service_request" for o in options)

    def test_actor_gets_no_view_sr_forced(self) -> None:
        from app.agents.services.review_decorations_service import build_review_decorations

        deco = build_review_decorations(
            {
                "workflow_stage": WP_MM_OPS_REVIEW,
                "backend_refs": {"sr_id": "341510", "user_role": "MALL_MANAGER"},
                "auth_context": _auth("MALL_MANAGER"),
            }
        )
        assert deco.get("promote_preview") is True
        assert "suggestions" not in deco


def test_eval_tier9_scenario_ids_registered() -> None:
    from tests.eval.scenarios import TIER0_SCENARIOS, TIER9_SCENARIOS
    from tests.eval.lifecycle_scenarios import TIER9_LIFECYCLE_SCENARIOS

    assert any(s.id == 207 for s in TIER0_SCENARIOS)
    assert {s.id for s in TIER9_SCENARIOS} == {240, 241, 242, 243, 244}
    assert {s.id for s in TIER9_LIFECYCLE_SCENARIOS} == {240, 241, 242, 243, 244, 245}
