"""Integration tests for the full Handover SR lifecycle.

Covers the entire CREATE → FM_REVIEW → RDD_REVIEW path with mocked
platform API calls.  All DB and LangGraph state transitions are exercised
through the node functions directly (no FastAPI HTTP layer needed).

Scenarios
---------
Path A (HANDOVER)
1. CREATE_SR happy path: payload built → SR created → sr_id in backend_refs
2. FM_REVIEW save progress: sr_id present → FM payload built → PATCH success
3. FM_REVIEW approve: PATCH APPROVED → fm_status=APPROVED
4. RDD_REVIEW submit report: POST REPORT_SUBMITTED
5. Unauthorized role blocked: wrong role cannot perform FM or RDD actions
6. already_handovered field set to "initiate_handover" in create payload (Fix 1)
7. FM dates in YYYY-MM-DD in review payload (Fix 5)
8. FM approve payload is minimal PATCH — no service_category or service_request_id (Fix 3)
9. RDD report payload includes sr_operations and current_sr_status (Fix 6)
10. RDD approve payload is minimal PATCH — no lease_code/lease_id at top level (Fix 4)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.graph.nodes.handover.api_submission_node import api_submission_node
from app.agents.graph.nodes.handover.fm_api_submission_node import fm_api_submission_node
from app.agents.graph.nodes.handover.fm_payload_builder_node import fm_payload_builder_node
from app.agents.graph.nodes.handover.fm_review_entry_node import fm_review_entry_node
from app.agents.graph.nodes.handover.payload_builder_node import payload_builder_node
from app.agents.graph.nodes.handover.rdd_api_submission_node import rdd_api_submission_node
from app.agents.graph.nodes.handover.rdd_payload_builder_node import rdd_payload_builder_node
from app.agents.graph.nodes.handover.rdd_review_entry_node import rdd_review_entry_node
from app.agents.graph.nodes.shared.sr_status_sync_node import sr_status_sync_node
from app.agents.services.service_request_api_service import ServiceRequestCreationResult
from app.agents.services.payload_builder_service import (
    build_create_handover_payload,
    build_fm_review_payload,
    build_fm_approve_payload,
    build_rdd_report_payload,
    build_rdd_approve_payload,
    build_create_handed_over_payload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_state(**kwargs: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "session_id": "lifecycle-sess-001",
        "user_id": "user-001",
        "trace_id": None,
        "trace_manager": None,
        "validation_errors": [],
        "collected_data": {},
        "backend_refs": {},
        "action_override": None,
        "workflow_stage": "CREATE_SR",
        "active_agent": "rdd_handover_agent",
        "confirmation_status": "CONFIRMED",
    }
    state.update(kwargs)
    return state


def _full_collected_data() -> dict[str, Any]:
    return {
        "mall": "Dubai Mall",
        "brand": "BrandX",
        "lease_code": "LC-001",
        "title": "handover-LC-001-test",
        "endDate": "2026-09-01",
        "startDate": "2026-08-01",
        "description": "Test SR",
        "inspection_done_by": "FM_MANAGER",
        "lease_brand_mall": "BrandX@Dubai Mall",
        "unit_codes": ["U-101"],
        "contracted_area": 150.0,
        "city": "Dubai",
        "brand_id": 789,
        "tenant_profile_id": 116,
        "contract_id": 456,
        "property_id": 10,
        "lease_id": 456,
        "comments": "",
    }


def _mock_create_result(sr_id: str = "sr-lifecycle-001") -> ServiceRequestCreationResult:
    return ServiceRequestCreationResult(
        sr_id=sr_id,
        endpoint="mock://service-requests",
        request_payload={},
        response_payload={"id": sr_id},
        latency_ms=10,
        status_code=201,
    )


def _mock_patch_result(sr_id: str = "sr-lifecycle-001") -> ServiceRequestCreationResult:
    return ServiceRequestCreationResult(
        sr_id=sr_id,
        endpoint=f"mock://service-requests/{sr_id}",
        request_payload={},
        response_payload={"success": True, "id": sr_id},
        latency_ms=8,
        status_code=200,
    )


def _mock_submit_result(sr_id: str = "sr-lifecycle-001") -> ServiceRequestCreationResult:
    return ServiceRequestCreationResult(
        sr_id=sr_id,
        endpoint="mock://service-requests",
        request_payload={},
        response_payload={"id": sr_id, "status": "REPORT_SUBMITTED"},
        latency_ms=12,
        status_code=201,
    )


# ---------------------------------------------------------------------------
# CREATE_SR happy path
# ---------------------------------------------------------------------------


class TestCreateSRLifecycle:
    @pytest.mark.asyncio
    async def test_payload_builder_then_api_submission(self) -> None:
        state = _base_state(
            collected_data=_full_collected_data(),
            backend_refs={},
        )

        # Build payload
        pb_result = await payload_builder_node(state)
        assert "backend_refs" in pb_result
        assert "create_payload" in pb_result["backend_refs"]

        # Merge payload result into state
        state["backend_refs"] = pb_result["backend_refs"]

        # Submit
        mock_result = _mock_create_result()
        with patch(
            "app.agents.graph.nodes.handover.api_submission_node.get_service_request_api_service"
        ) as mock_factory:
            mock_svc = AsyncMock()
            mock_svc.create_service_request = AsyncMock(return_value=mock_result)
            mock_factory.return_value = mock_svc
            sub_result = await api_submission_node(state)

        # After SR creation, the MM upload offer is seeded → WAITING_FOR_USER
        assert sub_result["status"] == "WAITING_FOR_USER"
        assert sub_result["workflow_stage"] == "SR_CREATED"
        assert sub_result["backend_refs"]["sr_id"] == "sr-lifecycle-001"


# ---------------------------------------------------------------------------
# SR status sync
# ---------------------------------------------------------------------------


class TestSRStatusSync:
    @pytest.mark.asyncio
    async def test_sync_sets_fm_review_stage(self) -> None:
        fm_operations = [{"role": "FM_MANAGER", "status": "IN_PROGRESS"}]
        state = _base_state(
            backend_refs={"sr_id": "sr-lifecycle-001"},
            workflow_stage="CREATE_SR",
        )

        mock_result = ServiceRequestCreationResult(
            sr_id="sr-lifecycle-001",
            endpoint="mock://service-requests/sr-lifecycle-001",
            request_payload={"sr_id": "sr-lifecycle-001"},
            response_payload={
                "_sr_status": "IN_PROCESS",
                "_service_request_operations": fm_operations,
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

        assert result["workflow_stage"] == "FM_REVIEW"

    @pytest.mark.asyncio
    async def test_sync_skipped_when_no_sr_id(self) -> None:
        state = _base_state(backend_refs={})
        result = await sr_status_sync_node(state)
        assert result == {}

    @pytest.mark.asyncio
    async def test_sync_sets_rdd_stage(self) -> None:
        rdd_operations = [{"role": "DD_ENGINEER", "status": "IN_PROGRESS"}]
        state = _base_state(
            backend_refs={"sr_id": "sr-lifecycle-001"},
            workflow_stage="FM_REVIEW",
        )

        mock_result = ServiceRequestCreationResult(
            sr_id="sr-lifecycle-001",
            endpoint="mock://service-requests/sr-lifecycle-001",
            request_payload={"sr_id": "sr-lifecycle-001"},
            response_payload={
                "_sr_status": "IN_PROCESS",
                "_service_request_operations": rdd_operations,
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

        assert result["workflow_stage"] == "RDD_REVIEW"


# ---------------------------------------------------------------------------
# FM_REVIEW lifecycle
# ---------------------------------------------------------------------------


class TestFMReviewLifecycle:
    def _fm_state(self, fm_action: str = "save_progress") -> dict[str, Any]:
        return _base_state(
            workflow_stage="FM_REVIEW",
            collected_data={
                "unit_readiness_date": "2026-05-01",
                "expected_handover_date": "2026-06-01",
            },
            backend_refs={
                "sr_id": "sr-lifecycle-001",
                "fm_action": fm_action,
                "create_payload": {
                    "payload": {
                        "mall": "Dubai Mall",
                        "brand": "BrandX",
                        "lease": "LC-001",
                        "title": "Test SR",
                        "tenant_profile_id": 116,
                        "property_id": 10,
                    },
                    "lease_id": 456,
                },
                "uploaded_documents": ["doc-fm-001", "doc-fm-002"],
                "sr_operations": [],
            },
        )

    @pytest.mark.asyncio
    async def test_fm_save_progress_full_path(self) -> None:
        from app.agents.services.platform_api_client import ServiceRequestGetResult

        state = self._fm_state("save_progress")

        # Build payload
        pb_result = await fm_payload_builder_node(state)
        state["backend_refs"] = pb_result["backend_refs"]

        assert state["backend_refs"]["fm_payload"]["status"] == "IN_PROCESS"

        # Submit
        mock_result = _mock_patch_result()
        with patch(
            "app.agents.graph.nodes.handover.fm_api_submission_node.get_service_request_api_service"
        ) as mock_factory:
            mock_svc = AsyncMock()
            mock_svc.patch_service_request = AsyncMock(return_value=mock_result)
            mock_svc.get_service_request = AsyncMock(
                return_value=ServiceRequestGetResult(
                    success=True,
                    sr_id="sr-lifecycle-001",
                    status="IN_PROCESS",
                    service_request_operations=[
                        {"assigned_role": "FM_MANAGER", "status": "IN_PROGRESS"},
                    ],
                    payload={"workflow_version": 1},
                )
            )
            mock_factory.return_value = mock_svc
            result = await fm_api_submission_node(state)

        assert result["status"] == "SUBMITTED"
        assert result["backend_refs"]["fm_status"] == "IN_PROCESS"

    @pytest.mark.asyncio
    async def test_fm_approve_full_path(self) -> None:
        from app.agents.services.platform_api_client import ServiceRequestGetResult

        state = self._fm_state("approve")

        pb_result = await fm_payload_builder_node(state)
        state["backend_refs"] = pb_result["backend_refs"]
        assert state["backend_refs"]["fm_payload"]["status"] == "APPROVED"

        mock_result = _mock_patch_result()
        mock_get = ServiceRequestGetResult(
            success=True,
            sr_id="sr-lifecycle-001",
            status="PENDING",
            service_request_operations=[
                {"assigned_role": "FM_MANAGER", "status": "FINISHED"},
                {"assigned_role": "DD_ENGINEER", "status": "IN_PROGRESS"},
            ],
        )
        with patch(
            "app.agents.graph.nodes.handover.fm_api_submission_node.get_service_request_api_service"
        ) as mock_factory:
            mock_svc = AsyncMock()
            mock_svc.patch_service_request = AsyncMock(return_value=mock_result)
            mock_svc.get_service_request = AsyncMock(return_value=mock_get)
            mock_factory.return_value = mock_svc
            result = await fm_api_submission_node(state)

        assert result["status"] == "SUBMITTED"
        assert result["backend_refs"]["fm_status"] == "APPROVED"

    @pytest.mark.asyncio
    async def test_unauthorized_role_blocked(self) -> None:
        state = _base_state(
            workflow_stage="FM_REVIEW",
            backend_refs={"user_role": "DD_ENGINEER"},
            action_override="save_fm_progress",
        )
        result = await fm_review_entry_node(state)
        assert result["status"] == "WAITING_FOR_USER"


# ---------------------------------------------------------------------------
# RDD_REVIEW lifecycle
# ---------------------------------------------------------------------------


class TestRDDReviewLifecycle:
    def _rdd_state(self) -> dict[str, Any]:
        return _base_state(
            workflow_stage="RDD_REVIEW",
            collected_data={
                "guideLineLink": "https://cenomi.com/guide",
                "actual_handover_date": "2026-05-12",
                "fitout_start_date": "2026-05-14",
                "fitout_end_date": "2026-05-20",
                "trading_date": "2026-05-25",
            },
            backend_refs={
                "sr_id": "sr-lifecycle-001",
                "rdd_action": "submit",
                "create_payload": {
                    "payload": {
                        "mall": "Dubai Mall",
                        "brand": "BrandX",
                        "lease": "LC-001",
                        "title": "Test SR",
                        "tenant_profile_id": 116,
                        "property_id": 10,
                    },
                    "lease_id": 456,
                },
                "uploaded_documents": ["doc-fm-001"],
                "rdd_document_id": "doc-rdd-001",
            },
        )

    @pytest.mark.asyncio
    async def test_rdd_submit_full_path(self) -> None:
        state = self._rdd_state()

        # Build payload
        pb_result = await rdd_payload_builder_node(state)
        state["backend_refs"] = pb_result["backend_refs"]
        assert state["backend_refs"]["rdd_payload"]["status"] == "REPORT_SUBMITTED"

        # Submit
        mock_result = _mock_submit_result()
        with patch(
            "app.agents.graph.nodes.handover.rdd_api_submission_node.get_service_request_api_service"
        ) as mock_factory:
            mock_svc = AsyncMock()
            mock_svc.submit_report = AsyncMock(return_value=mock_result)
            mock_factory.return_value = mock_svc
            result = await rdd_api_submission_node(state)

        assert result["status"] == "SUBMITTED"
        # Phase 3a (submit_report) does NOT advance to SR_COMPLETED — only final_approve does

    @pytest.mark.asyncio
    async def test_unauthorized_role_blocked(self) -> None:
        state = _base_state(
            workflow_stage="RDD_REVIEW",
            backend_refs={"user_role": "MALL_MANAGER"},
            action_override="submit_rdd_report",
        )
        result = await rdd_review_entry_node(state)
        assert result["status"] == "WAITING_FOR_USER"


# ---------------------------------------------------------------------------
# Path A payload contract fixes (Fixes 1, 3, 4, 5, 6)
# ---------------------------------------------------------------------------


class TestPathAPayloadFixes:
    """Verify that all six contract-critical fixes are present in the payloads
    built by the Path A (HANDOVER) payload builders."""

    def _base_cd(self) -> dict[str, Any]:
        return {
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

    def _base_refs(self) -> dict[str, Any]:
        return {
            "sr_id": "sr-001",
            "create_payload": {
                "payload": {
                    "mall": "Dubai Mall",
                    "brand": "BrandX",
                    "title": "handover-LC-001",
                    "tenant_profile_id": 116,
                    "property_id": 10,
                },
                "lease_id": 456,
            },
            "uploaded_documents": ["doc-fm-001"],
            "rdd_document_id": "doc-rdd-001",
            "sr_operations": [{"role": "FM_MANAGER", "status": "APPROVED"}],
            "current_sr_status": "IN_PROCESS",
        }

    def test_fix1_already_handovered_is_initiate_handover(self) -> None:
        """Fix 1: create payload must include already_handovered=initiate_handover."""
        result = build_create_handover_payload(self._base_cd(), {})
        assert result["payload"]["already_handovered"] == "initiate_handover"

    def test_fix5_fm_dates_are_iso_format(self) -> None:
        """Fix 5: FM review payload dates must be YYYY-MM-DD."""
        cd = {**self._base_cd(), "unit_readiness_date": "01/05/2026", "expected_handover_date": "01/06/2026"}
        result = build_fm_review_payload(cd, self._base_refs())
        inner = result["payload"]
        assert inner["unit_readiness_date"] == "2026-05-01"
        assert inner["expected_handover_date"] == "2026-06-01"

    def test_fix3_fm_approve_minimal_patch(self) -> None:
        """Fix 3: FM approve payload must NOT include service_category or service_request_id at top level."""
        cd = {**self._base_cd(), "unit_readiness_date": "2026-05-01", "expected_handover_date": "2026-06-01"}
        refs = {**self._base_refs(), "fm_action": "approve"}
        result = build_fm_approve_payload(cd, refs)
        assert "service_category" not in result
        assert "service_request_id" not in result
        # sub_category is at the top level (not nested inside payload)
        assert result.get("sub_category") == "HANDOVER"

    def test_fix6_rdd_report_has_sr_operations_and_status(self) -> None:
        """Fix 6: RDD report payload must include sr_operations and current_sr_status."""
        cd = {
            **self._base_cd(),
            "guideLineLink": "https://cenomi.com/guide",
            "actual_handover_date": "2026-05-12",
            "fitout_start_date": "2026-05-14",
            "fitout_end_date": "2026-05-20",
            "trading_date": "2026-05-25",
        }
        result = build_rdd_report_payload(cd, self._base_refs())
        assert "sr_operations" in result["payload"]
        assert "current_sr_status" in result["payload"]

    def test_fix4_rdd_approve_minimal_patch(self) -> None:
        """Fix 4: RDD approve payload must NOT include lease_code, lease_id, or service_category at top level."""
        refs = {**self._base_refs(), "rdd_action": "final_approve"}
        result = build_rdd_approve_payload(refs)
        assert "lease_code" not in result
        assert "lease_id" not in result
        assert "service_category" not in result
        # sub_category is at the top level (not nested inside payload)
        assert result.get("sub_category") == "HANDOVER"


# ---------------------------------------------------------------------------
# Fix 2 — FM document policy correction (COP Checklist replaces SR_HANDOVER_OTHER)
# ---------------------------------------------------------------------------


class TestFMDocumentPolicyCorrection:
    """Verify Fix 2: FM Manager (role 20) must accept SR_COP_CHECKLIST_OTHER and
    must reject SR_HANDOVER_OTHER (which belongs to Leasing Admin only)."""

    @pytest.mark.asyncio
    async def test_cop_checklist_accepted_for_fm_manager_role(self) -> None:
        """Uploading SR_COP_CHECKLIST_OTHER must pass FM document validation and be
        stored in backend_refs["uploaded_documents"]."""
        from app.agents.graph.nodes.document_upload_node import document_upload_node

        state = _base_state(
            documents=[
                {
                    "document_id": "doc-cop-001",
                    "document_type_id": "SR_COP_CHECKLIST_OTHER",
                }
            ],
            backend_refs={},
            workflow_stage="FM_REVIEW",
        )
        result = await document_upload_node(state)
        uploaded = result.get("backend_refs", {}).get("uploaded_documents", [])
        assert "doc-cop-001" in uploaded, (
            "SR_COP_CHECKLIST_OTHER must be accepted as a valid FM document type "
            "and stored in backend_refs['uploaded_documents']"
        )

    @pytest.mark.asyncio
    async def test_sr_handover_other_rejected_for_fm_role_20(self) -> None:
        """Uploading SR_HANDOVER_OTHER must NOT be stored in the FM bucket — that
        doc type belongs to Leasing Admin (role 11), not FM Manager (role 20)."""
        from app.agents.graph.nodes.document_upload_node import document_upload_node

        state = _base_state(
            documents=[
                {
                    "document_id": "doc-other-001",
                    "document_type_id": "SR_HANDOVER_OTHER",
                }
            ],
            backend_refs={},
            workflow_stage="FM_REVIEW",
        )
        result = await document_upload_node(state)
        uploaded = result.get("backend_refs", {}).get("uploaded_documents", [])
        rdd_id = result.get("backend_refs", {}).get("rdd_document_id")
        assert "doc-other-001" not in uploaded, (
            "SR_HANDOVER_OTHER must NOT be accepted as an FM document "
            "(it belongs to Leasing Admin only)"
        )
        assert rdd_id != "doc-other-001", (
            "SR_HANDOVER_OTHER must NOT be placed in the RDD bucket either"
        )

    @pytest.mark.asyncio
    async def test_cop_checklist_and_sr_handover_checklist_both_accepted(self) -> None:
        """Both SR_COP_CHECKLIST_OTHER and SR_HANDOVER_CHECKLIST must be accepted
        as FM documents and stored in the FM bucket."""
        from app.agents.graph.nodes.document_upload_node import document_upload_node

        state = _base_state(
            documents=[
                {
                    "document_id": "doc-checklist-001",
                    "document_type_id": "SR_HANDOVER_CHECKLIST",
                },
                {
                    "document_id": "doc-cop-002",
                    "document_type_id": "SR_COP_CHECKLIST_OTHER",
                },
            ],
            backend_refs={},
            workflow_stage="FM_REVIEW",
        )
        result = await document_upload_node(state)
        uploaded = result.get("backend_refs", {}).get("uploaded_documents", [])
        assert "doc-checklist-001" in uploaded
        assert "doc-cop-002" in uploaded
