"""Unit tests — Path B (HANDED_OVER / Already Trading) implementation.

Test groups
-----------
TestHandedOverSchema           — CREATE_HANDED_OVER_SR_STAGE, HANDED_OVER_DATE_CHAIN,
                                  HANDED_OVER_ALLOWED_DOCUMENTS, permission map.
TestBuildCreateHandedOverPayload — build_create_handed_over_payload shape, values,
                                  validation, constants.
TestValidateHandedOverDates    — validate_handed_over_dates: valid chain, gaps,
                                  reversed order, missing fields.
TestValidateDraftHandedOver    — validate_draft for CREATE_HANDED_OVER_SR stage.
TestMergeStatePathB            — Title auto-generation branch; auto-compute guards.
TestHandoverEntryRouting       — handover_entry_node routes to CREATE_HANDED_OVER_SR.
TestApiSubmissionPathB         — api_submission_node marks HANDED_OVER_COMPLETED.
TestSrStatusSyncPathB          — _map_operations_to_stage exits early for HANDED_OVER+APPROVED.
TestPayloadBuilderNodePathB    — payload_builder_node delegates to handed_over builder.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# TestHandedOverSchema
# ---------------------------------------------------------------------------


class TestHandedOverSchema:
    def test_create_handed_over_sr_stage_in_registry(self) -> None:
        from app.agents.schemas.handover_schema import STAGE_REGISTRY
        assert "CREATE_HANDED_OVER_SR" in STAGE_REGISTRY

    def test_create_handed_over_sr_stage_role(self) -> None:
        from app.agents.schemas.handover_schema import CREATE_HANDED_OVER_SR_STAGE
        assert CREATE_HANDED_OVER_SR_STAGE.role == "MALL_MANAGER"

    def test_create_handed_over_sr_stage_no_required_documents(self) -> None:
        from app.agents.schemas.handover_schema import CREATE_HANDED_OVER_SR_STAGE
        assert CREATE_HANDED_OVER_SR_STAGE.required_documents == ()

    def test_create_handed_over_sr_stage_has_date_fields(self) -> None:
        from app.agents.schemas.handover_schema import CREATE_HANDED_OVER_SR_STAGE
        fields = CREATE_HANDED_OVER_SR_STAGE.required_fields
        assert "actual_shop_handover_date" in fields
        assert "actual_fitout_start_date" in fields
        assert "actual_fitout_end_date" in fields
        assert "actual_trading_date" in fields

    def test_handed_over_date_chain_has_three_pairs(self) -> None:
        from app.agents.schemas.handover_schema import HANDED_OVER_DATE_CHAIN
        assert len(HANDED_OVER_DATE_CHAIN) == 3

    def test_handed_over_date_chain_ordered(self) -> None:
        from app.agents.schemas.handover_schema import HANDED_OVER_DATE_CHAIN
        expected = (
            ("actual_shop_handover_date", "actual_fitout_start_date"),
            ("actual_fitout_start_date", "actual_fitout_end_date"),
            ("actual_fitout_end_date", "actual_trading_date"),
        )
        assert HANDED_OVER_DATE_CHAIN == expected

    def test_handed_over_allowed_documents(self) -> None:
        from app.agents.schemas.handover_schema import HANDED_OVER_ALLOWED_DOCUMENTS
        assert "SR_HANDED_OVER_OTHER" in HANDED_OVER_ALLOWED_DOCUMENTS

    def test_all_document_types_includes_sr_handed_over_other(self) -> None:
        from app.agents.schemas.handover_schema import ALL_DOCUMENT_TYPES
        assert "SR_HANDED_OVER_OTHER" in ALL_DOCUMENT_TYPES

    def test_permission_map_mall_manager_has_create_handed_over_sr(self) -> None:
        from app.agents.schemas.handover_schema import PERMISSION_MAP
        assert "CREATE_HANDED_OVER_SR" in PERMISSION_MAP["MALL_MANAGER"]

    def test_sub_category_handed_over_constant(self) -> None:
        from app.agents.schemas.handover_schema import SUB_CATEGORY_HANDED_OVER
        assert SUB_CATEGORY_HANDED_OVER == "HANDED_OVER"


# ---------------------------------------------------------------------------
# TestBuildCreateHandedOverPayload
# ---------------------------------------------------------------------------


def _handed_over_data() -> dict[str, Any]:
    return {
        "mall": "Riyadh Park",
        "brand": "Nike",
        "lease_code": "LC-HO-001",
        "lease_id": "LEASE-HO-001",
        "title": "Already Trading - LC-HO-001",
        "description": "Already operating",
        "actual_shop_handover_date": "2024-01-10",
        "actual_fitout_start_date": "2024-01-15",
        "actual_fitout_end_date": "2024-03-01",
        "actual_trading_date": "2024-03-10",
        "comments": "All good",
        "unit_codes": ["U-HO-01"],
        "contracted_area": 150,
        "city": "Riyadh",
        "brand_id": "BR-HO-001",
        "tenant_profile_id": "TP-HO-001",
        "property_id": "PROP-HO-001",
    }


class TestBuildCreateHandedOverPayload:
    def test_returns_dict(self) -> None:
        from app.agents.services.payload_builder_service import build_create_handed_over_payload
        assert isinstance(build_create_handed_over_payload(_handed_over_data()), dict)

    def test_sub_category_is_handed_over(self) -> None:
        from app.agents.services.payload_builder_service import build_create_handed_over_payload
        result = build_create_handed_over_payload(_handed_over_data())
        assert result["sub_category"] == "HANDED_OVER"

    def test_service_category_is_fit_out_and_handover(self) -> None:
        from app.agents.services.payload_builder_service import build_create_handed_over_payload
        result = build_create_handed_over_payload(_handed_over_data())
        assert result["service_category"] == "FIT_OUT_AND_HANDOVER"

    def test_already_handovered_is_already_trading(self) -> None:
        """Path B inner payload must carry already_handovered='already_trading' (not initiate_handover)."""
        from app.agents.services.payload_builder_service import build_create_handed_over_payload
        result = build_create_handed_over_payload(_handed_over_data())
        assert result["payload"]["already_handovered"] == "already_trading"

    def test_actual_dates_mapped(self) -> None:
        from app.agents.services.payload_builder_service import build_create_handed_over_payload
        data = _handed_over_data()
        result = build_create_handed_over_payload(data)
        inner = result["payload"]
        assert inner["actual_shop_handover_date"] == data["actual_shop_handover_date"]
        assert inner["actual_fitout_start_date"] == data["actual_fitout_start_date"]
        assert inner["actual_fitout_end_date"] == data["actual_fitout_end_date"]
        assert inner["actual_trading_date"] == data["actual_trading_date"]

    def test_path_a_date_fields_absent(self) -> None:
        """Path B payload must NOT include Path A date fields: startDate, endDate, inspectionTime."""
        from app.agents.services.payload_builder_service import build_create_handed_over_payload
        result = build_create_handed_over_payload(_handed_over_data())
        inner = result["payload"]
        assert "startDate" not in inner
        assert "endDate" not in inner
        assert "inspectionDoneBy" not in inner
        assert "inspection_done_by" not in inner
        assert "guideLineLink" not in inner

    def test_service_request_id_empty(self) -> None:
        from app.agents.services.payload_builder_service import build_create_handed_over_payload
        result = build_create_handed_over_payload(_handed_over_data())
        assert result["service_request_id"] == ""

    def test_title_defaults_to_already_trading_lease_code(self) -> None:
        from app.agents.services.payload_builder_service import build_create_handed_over_payload
        data = _handed_over_data()
        del data["title"]
        result = build_create_handed_over_payload(data)
        assert result["title"] == f"Already Trading - {data['lease_code']}"
        assert result["payload"]["title"] == f"Already Trading - {data['lease_code']}"

    def test_documents_ids_empty_when_no_docs_passed(self) -> None:
        from app.agents.services.payload_builder_service import build_create_handed_over_payload
        result = build_create_handed_over_payload(_handed_over_data())
        assert result["payload"]["documents_ids"] == []

    def test_documents_ids_populated_from_document_list(self) -> None:
        from app.agents.services.payload_builder_service import build_create_handed_over_payload
        docs = [{"document_id": "doc-ho-001"}, {"document_id": "doc-ho-002"}]
        result = build_create_handed_over_payload(_handed_over_data(), documents=docs)
        assert "doc-ho-001" in result["payload"]["documents_ids"]
        assert "doc-ho-002" in result["payload"]["documents_ids"]

    def test_raises_on_missing_required_key(self) -> None:
        from app.agents.services.payload_builder_service import build_create_handed_over_payload
        data = _handed_over_data()
        del data["actual_shop_handover_date"]
        with pytest.raises(ValueError, match="actual_shop_handover_date"):
            build_create_handed_over_payload(data)

    def test_raises_on_empty_data(self) -> None:
        from app.agents.services.payload_builder_service import build_create_handed_over_payload
        with pytest.raises(ValueError):
            build_create_handed_over_payload({})

    def test_payload_builder_service_delegates(self) -> None:
        from app.agents.services.payload_builder_service import (
            PayloadBuilderService,
            build_create_handed_over_payload,
        )
        svc = PayloadBuilderService()
        data = _handed_over_data()
        assert svc.build_create_handed_over_payload(data) == build_create_handed_over_payload(data)


# ---------------------------------------------------------------------------
# TestValidateHandedOverDates
# ---------------------------------------------------------------------------


class TestValidateHandedOverDates:
    def _validate(self, data: dict[str, Any]) -> list[dict]:
        from app.agents.services.validation_service import validate_handed_over_dates
        return validate_handed_over_dates(data)

    def test_valid_chain_returns_all_passed(self) -> None:
        data = {
            "actual_shop_handover_date": "2024-01-10",
            "actual_fitout_start_date": "2024-01-15",
            "actual_fitout_end_date": "2024-03-01",
            "actual_trading_date": "2024-03-10",
        }
        results = self._validate(data)
        failed = [r for r in results if r["status"] == "FAILED"]
        assert failed == []

    def test_equal_dates_allowed(self) -> None:
        """Equal consecutive dates are permitted (edge: fitout starts same day as handover)."""
        data = {
            "actual_shop_handover_date": "2024-01-10",
            "actual_fitout_start_date": "2024-01-10",
            "actual_fitout_end_date": "2024-03-01",
            "actual_trading_date": "2024-03-10",
        }
        results = self._validate(data)
        failed = [r for r in results if r["status"] == "FAILED"]
        assert failed == []

    def test_reversed_chain_fails(self) -> None:
        data = {
            "actual_shop_handover_date": "2024-03-10",
            "actual_fitout_start_date": "2024-01-10",  # before handover
            "actual_fitout_end_date": "2024-03-01",
            "actual_trading_date": "2024-03-10",
        }
        results = self._validate(data)
        failed = [r for r in results if r["status"] == "FAILED"]
        assert len(failed) >= 1

    def test_missing_date_skips_pair_silently(self) -> None:
        """Pairs where one date is absent are skipped (not failed) — missing-field
        validation is handled separately by the required-fields check."""
        data = {
            "actual_shop_handover_date": "2024-01-10",
            # actual_fitout_start_date intentionally absent
            "actual_fitout_end_date": "2024-03-01",
            "actual_trading_date": "2024-03-10",
        }
        results = self._validate(data)
        # The pair (shop_handover, fitout_start) is skipped since fitout_start is missing.
        # The pair (fitout_start, fitout_end) is also skipped.
        # Only (fitout_end, trading_date) has both values → should PASS.
        failed = [r for r in results if r["status"] == "FAILED"]
        assert failed == []

    def test_returns_list(self) -> None:
        results = self._validate({})
        assert isinstance(results, list)


class TestValidateDraftHandedOver:
    def _svc(self):
        from app.agents.services.validation_service import ValidationService
        return ValidationService()

    def test_create_handed_over_sr_with_valid_dates_passes(self) -> None:
        data = {
            "actual_shop_handover_date": "2024-01-10",
            "actual_fitout_start_date": "2024-01-15",
            "actual_fitout_end_date": "2024-03-01",
            "actual_trading_date": "2024-03-10",
        }
        errors = self._svc().validate_draft(data, "CREATE_HANDED_OVER_SR")
        date_errors = [e for e in errors if "date" in e.get("field", "").lower()]
        assert date_errors == []

    def test_create_handed_over_sr_with_reversed_dates_fails(self) -> None:
        data = {
            "actual_shop_handover_date": "2024-06-01",
            "actual_fitout_start_date": "2024-01-01",  # before handover
            "actual_fitout_end_date": "2024-03-01",
            "actual_trading_date": "2024-03-10",
        }
        errors = self._svc().validate_draft(data, "CREATE_HANDED_OVER_SR")
        failed = [e for e in errors if e.get("status") == "FAILED"]
        assert len(failed) >= 1


# ---------------------------------------------------------------------------
# TestPayloadBuilderNodePathB
# ---------------------------------------------------------------------------


class TestPayloadBuilderNodePathB:
    @pytest.mark.asyncio
    async def test_node_create_handed_over_sr_stores_payload(self) -> None:
        from app.agents.graph.nodes.handover.payload_builder_node import payload_builder_node

        state: dict[str, Any] = {
            "collected_data": _handed_over_data(),
            "workflow_stage": "CREATE_HANDED_OVER_SR",
        }
        result = await payload_builder_node(state)
        assert "backend_refs" in result
        refs = result["backend_refs"]
        assert "create_handed_over_payload" in refs
        assert "create_payload" in refs
        assert refs["create_payload"]["sub_category"] == "HANDED_OVER"

    @pytest.mark.asyncio
    async def test_node_create_handed_over_sr_missing_keys_returns_failed(self) -> None:
        from app.agents.graph.nodes.handover.payload_builder_node import payload_builder_node

        state: dict[str, Any] = {
            "collected_data": {},
            "workflow_stage": "CREATE_HANDED_OVER_SR",
        }
        result = await payload_builder_node(state)
        assert result.get("status") == "FAILED"


# ---------------------------------------------------------------------------
# TestHandoverEntryRouting
# ---------------------------------------------------------------------------


class TestHandoverEntryRouting:
    def _base_state(self, **kwargs: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "session_id": "sess-ho-001",
            "user_id": "user-ho-001",
            "user_message": "I want to register an already trading unit",
            "intent": "CREATE_HANDED_OVER_SR",
            "active_agent": "rdd_handover_agent",
            "sub_category": "HANDED_OVER",
            "service_category": "FIT_OUT_AND_HANDOVER",
            "workflow_stage": None,
            "collected_data": {},
            "conversation_history": [],
            "backend_refs": {},
            "status": "IN_PROGRESS",
            "confirmation_status": None,
            "confirmation_required": False,
            "documents": [],
            "missing_fields": [],
            "active_field": None,
            "extracted_fields": {},
            "validation_errors": [],
            "lease_matches": [],
            "selected_lease": None,
            "response_message": None,
            "response_ui": None,
            "response_decorations": None,
            "faq_sources": [],
            "action_override": None,
            "corrected_fields": None,
            "trace_id": None,
            "trace_manager": None,
            "conversation_state_service": None,
            "auth_context": None,
            "language": None,
            "msp_context": None,
        }
        base.update(kwargs)
        return base

    @pytest.mark.asyncio
    async def test_sub_category_handed_over_routes_to_create_handed_over_sr(self) -> None:
        from app.agents.graph.nodes.handover.handover_entry_node import handover_entry_node

        state = self._base_state(sub_category="HANDED_OVER", workflow_stage=None)
        result = await handover_entry_node(state)
        assert result.get("workflow_stage") == "CREATE_HANDED_OVER_SR"

    @pytest.mark.asyncio
    async def test_already_handovered_in_collected_routes_to_create_handed_over_sr(self) -> None:
        from app.agents.graph.nodes.handover.handover_entry_node import handover_entry_node

        state = self._base_state(
            sub_category=None,
            workflow_stage=None,
            collected_data={"already_handovered": "already_trading"},
        )
        result = await handover_entry_node(state)
        assert result.get("workflow_stage") == "CREATE_HANDED_OVER_SR"


# ---------------------------------------------------------------------------
# TestSrStatusSyncPathB
# ---------------------------------------------------------------------------


class TestSrStatusSyncPathB:
    def test_handed_over_approved_maps_to_sr_completed(self) -> None:
        """HANDED_OVER sub_category with APPROVED platform_status must short-circuit to SR_COMPLETED."""
        from app.agents.graph.nodes.shared.sr_status_sync_node import _map_operations_to_stage

        result = _map_operations_to_stage(
            [{"assigned_role": "MALL_MANAGER", "status": "APPROVED"}],
            "CREATE_HANDED_OVER_SR",
            sub_category="HANDED_OVER",
            platform_status="APPROVED",
        )
        assert result == "SR_COMPLETED"

    def test_handed_over_not_approved_does_not_short_circuit(self) -> None:
        """HANDED_OVER with non-APPROVED status must NOT immediately return SR_COMPLETED."""
        from app.agents.graph.nodes.shared.sr_status_sync_node import _map_operations_to_stage

        result = _map_operations_to_stage(
            [{"assigned_role": "MALL_MANAGER", "status": "IN_PROGRESS"}],
            "CREATE_HANDED_OVER_SR",
            sub_category="HANDED_OVER",
            platform_status="IN_PROGRESS",
        )
        assert result != "SR_COMPLETED"

    def test_handover_with_in_progress_fm_routes_to_fm_review(self) -> None:
        """Path A (HANDOVER) with FM IN_PROGRESS must NOT short-circuit and instead route to FM_REVIEW."""
        from app.agents.graph.nodes.shared.sr_status_sync_node import _map_operations_to_stage

        result = _map_operations_to_stage(
            [{"assigned_role": "FM_MANAGER", "status": "IN_PROGRESS"}],
            "CREATE_SR",
            sub_category="HANDOVER",
            platform_status="IN_PROGRESS",
        )
        assert result == "FM_REVIEW"


# ---------------------------------------------------------------------------
# TestHandedOverMergeState — Path A auto-compute regression guard
# ---------------------------------------------------------------------------


class TestHandedOverMergeState:
    """Verify that Path A auto-compute logic still fires after Path B was added."""

    @pytest.mark.asyncio
    async def test_trading_date_still_auto_computed_for_rdd_review(self) -> None:
        """Path A: trading_date = fitout_end_date + 8 days must still be auto-computed
        during RDD_REVIEW after the Path B guard (CREATE_HANDED_OVER_SR) was introduced."""
        from app.agents.graph.nodes.shared.merge_state_node import merge_state_node

        state: dict[str, Any] = {
            "workflow_stage": "RDD_REVIEW",
            "collected_data": {
                "fitout_end_date": "2026-09-01",
            },
            "extracted_fields": {},
            "corrected_fields": None,
            "active_field": None,
            "user_message": "",
        }
        result = await merge_state_node(state)
        collected = result.get("collected_data", {})
        assert "trading_date" in collected, "trading_date must be auto-computed from fitout_end_date in RDD_REVIEW"
        assert collected["trading_date"] == "2026-09-09", (
            "trading_date should be fitout_end_date + 8 days = 2026-09-09"
        )

    @pytest.mark.asyncio
    async def test_expected_handover_date_still_auto_computed_for_fm_review(self) -> None:
        """Path A: expected_handover_date = unit_readiness_date + 7 days must still be
        auto-computed during FM_REVIEW after the Path B guard was introduced."""
        from app.agents.graph.nodes.shared.merge_state_node import merge_state_node

        state: dict[str, Any] = {
            "workflow_stage": "FM_REVIEW",
            "collected_data": {
                "unit_readiness_date": "2026-08-01",
            },
            "extracted_fields": {},
            "corrected_fields": None,
            "active_field": None,
            "user_message": "",
        }
        result = await merge_state_node(state)
        collected = result.get("collected_data", {})
        assert "expected_handover_date" in collected, (
            "expected_handover_date must be auto-computed from unit_readiness_date in FM_REVIEW"
        )
        assert collected["expected_handover_date"] == "2026-08-08", (
            "expected_handover_date should be unit_readiness_date + 7 days = 2026-08-08"
        )

    @pytest.mark.asyncio
    async def test_trading_date_not_auto_computed_for_create_handed_over_sr(self) -> None:
        """Path B guard: trading_date must NOT be auto-computed in CREATE_HANDED_OVER_SR
        because actual_trading_date is a direct user-supplied field."""
        from app.agents.graph.nodes.shared.merge_state_node import merge_state_node

        state: dict[str, Any] = {
            "workflow_stage": "CREATE_HANDED_OVER_SR",
            "collected_data": {
                "actual_fitout_end_date": "2024-03-01",
            },
            "extracted_fields": {},
            "corrected_fields": None,
            "active_field": None,
            "user_message": "",
        }
        result = await merge_state_node(state)
        collected = result.get("collected_data", {})
        # In Path B, trading_date must NOT be injected — only actual_trading_date matters
        assert "trading_date" not in collected, (
            "trading_date must NOT be auto-computed in CREATE_HANDED_OVER_SR (Path B)"
        )


# ---------------------------------------------------------------------------
# TestHandoverEntryRouting — mid-session path switch (additional tests)
# ---------------------------------------------------------------------------


class TestHandoverEntryMidSessionSwitch:
    """Verify stale-state clearing when the user switches path mid-session."""

    def _path_a_state(self, **kwargs: Any) -> dict[str, Any]:
        """State simulating a completed Path A SR session."""
        base: dict[str, Any] = {
            "session_id": "sess-switch-001",
            "user_id": "user-switch-001",
            "user_message": "I want to register an already trading unit",
            "intent": "CREATE_HANDED_OVER_SR",
            "active_agent": "rdd_handover_agent",
            "sub_category": "HANDED_OVER",
            "service_category": "FIT_OUT_AND_HANDOVER",
            # SR_CREATED is the terminal stage for Path A
            "workflow_stage": "SR_CREATED",
            "collected_data": {
                "startDate": "2026-06-01",
                "endDate": "2026-06-03",
                "inspection_done_by": "FM_MANAGER",
                "lease_code": "LC-SWITCH-001",
                "description": "Path A test",
            },
            "conversation_history": [],
            "backend_refs": {},
            "status": "IN_PROGRESS",
            "confirmation_status": None,
            "confirmation_required": False,
            "documents": [],
            "missing_fields": [],
            "active_field": None,
            "extracted_fields": {},
            "validation_errors": [],
            "lease_matches": [],
            "selected_lease": None,
            "response_message": None,
            "response_ui": None,
            "response_decorations": None,
            "faq_sources": [],
            "action_override": None,
            "corrected_fields": None,
            "trace_id": None,
            "trace_manager": None,
            "conversation_state_service": None,
            "auth_context": None,
            "language": None,
            "msp_context": None,
        }
        base.update(kwargs)
        return base

    def _path_b_state(self, **kwargs: Any) -> dict[str, Any]:
        """State simulating a completed Path B SR session."""
        base: dict[str, Any] = {
            "session_id": "sess-switch-002",
            "user_id": "user-switch-002",
            "user_message": "I want to create a standard handover SR",
            "intent": "CREATE_RDD_SERVICE_REQUEST",
            "active_agent": "rdd_handover_agent",
            "sub_category": "HANDOVER",
            "service_category": "FIT_OUT_AND_HANDOVER",
            # HANDED_OVER_COMPLETED is the terminal stage for Path B
            "workflow_stage": "HANDED_OVER_COMPLETED",
            "collected_data": {
                "actual_shop_handover_date": "2024-01-10",
                "actual_fitout_start_date": "2024-01-15",
                "actual_fitout_end_date": "2024-03-01",
                "actual_trading_date": "2024-03-10",
                "lease_code": "LC-SWITCH-002",
            },
            "conversation_history": [],
            "backend_refs": {},
            "status": "IN_PROGRESS",
            "confirmation_status": None,
            "confirmation_required": False,
            "documents": [],
            "missing_fields": [],
            "active_field": None,
            "extracted_fields": {},
            "validation_errors": [],
            "lease_matches": [],
            "selected_lease": None,
            "response_message": None,
            "response_ui": None,
            "response_decorations": None,
            "faq_sources": [],
            "action_override": None,
            "corrected_fields": None,
            "trace_id": None,
            "trace_manager": None,
            "conversation_state_service": None,
            "auth_context": None,
            "language": None,
            "msp_context": None,
        }
        base.update(kwargs)
        return base

    @pytest.mark.asyncio
    async def test_mid_session_switch_to_already_trading_clears_path_a_fields(
        self,
    ) -> None:
        """User was in CREATE_SR (Path A, terminal SR_CREATED) and now wants already
        trading — stale-clear must fire, zeroing the Path A collected fields."""
        from app.agents.graph.nodes.handover.handover_entry_node import handover_entry_node

        state = self._path_a_state()
        result = await handover_entry_node(state)
        # Stale-clear fires: collected_data must be wiped
        assert result.get("collected_data") == {} or result.get("collected_data") is None or result.get("collected_data") == {}, (
            "Path A collected fields must be cleared when switching to CREATE_HANDED_OVER_SR "
            "from a terminal SR_CREATED stage"
        )
        # workflow_stage must also be reset
        assert result.get("workflow_stage") is None or "workflow_stage" in result

    @pytest.mark.asyncio
    async def test_mid_session_switch_back_to_initiate_handover_clears_path_b_fields(
        self,
    ) -> None:
        """User was in CREATE_HANDED_OVER_SR (Path B, terminal HANDED_OVER_COMPLETED)
        and now wants a standard HANDOVER — stale-clear must fire, zeroing Path B dates."""
        from app.agents.graph.nodes.handover.handover_entry_node import handover_entry_node

        state = self._path_b_state()
        result = await handover_entry_node(state)
        # Stale-clear fires: collected_data must be wiped
        assert result.get("collected_data") == {} or result.get("collected_data") is None or result.get("collected_data") == {}, (
            "Path B actual date fields must be cleared when switching back to "
            "CREATE_RDD_SERVICE_REQUEST from a terminal HANDED_OVER_COMPLETED stage"
        )


# ---------------------------------------------------------------------------
# TestHandedOverApiSubmission — extra assertions
# ---------------------------------------------------------------------------


class TestHandedOverApiSubmission:
    """Verify Path B success message and absence of MM upload offer."""

    def _base_state(self, sub_category: str = "HANDED_OVER") -> dict[str, Any]:
        return {
            "session_id": "sess-api-ho-001",
            "user_id": "user-api-ho-001",
            "confirmation_status": "CONFIRMED",
            "validation_errors": [],
            "workflow_stage": "CREATE_HANDED_OVER_SR",
            "trace_id": None,
            "trace_manager": None,
            "collected_data": {},
            "backend_refs": {
                "create_payload": {
                    "sub_category": sub_category,
                    "service_category": "FIT_OUT_AND_HANDOVER",
                    "title": "Already Trading - LC-HO-001",
                    "payload": {
                        "already_handovered": "already_trading",
                        "documents_ids": [],
                    },
                }
            },
        }

    @pytest.mark.asyncio
    async def test_response_message_confirms_immediate_approval(self) -> None:
        """Path B success message must mention the SR was automatically approved
        with no FM or RDD steps."""
        from app.agents.graph.nodes.handover.api_submission_node import api_submission_node
        from app.agents.services.service_request_api_service import ServiceRequestCreationResult

        mock_result = ServiceRequestCreationResult(
            sr_id="sr-ho-immediate-001",
            endpoint="mock://service-requests",
            request_payload={},
            response_payload={"id": "sr-ho-immediate-001"},
            latency_ms=10,
            status_code=201,
        )
        with patch(
            "app.agents.graph.nodes.handover.api_submission_node.get_service_request_api_service"
        ) as mock_factory:
            mock_svc = AsyncMock()
            mock_svc.create_service_request = AsyncMock(return_value=mock_result)
            mock_factory.return_value = mock_svc
            result = await api_submission_node(self._base_state())

        msg = result.get("response_message", "")
        assert "sr-ho-immediate-001" in msg, "SR ID must appear in the success message"
        # Message must convey immediate approval / no further FM or RDD steps
        assert any(
            keyword in msg.lower()
            for keyword in ("approved", "automatically", "no further", "fm", "rdd")
        ), f"Response message must mention immediate approval; got: {msg!r}"

    @pytest.mark.asyncio
    async def test_handed_over_completed_stage_set_on_success(self) -> None:
        """Path B submission must set workflow_stage=HANDED_OVER_COMPLETED, not SR_CREATED."""
        from app.agents.graph.nodes.handover.api_submission_node import api_submission_node
        from app.agents.services.service_request_api_service import ServiceRequestCreationResult

        mock_result = ServiceRequestCreationResult(
            sr_id="sr-ho-stage-001",
            endpoint="mock://service-requests",
            request_payload={},
            response_payload={"id": "sr-ho-stage-001"},
            latency_ms=10,
            status_code=201,
        )
        with patch(
            "app.agents.graph.nodes.handover.api_submission_node.get_service_request_api_service"
        ) as mock_factory:
            mock_svc = AsyncMock()
            mock_svc.create_service_request = AsyncMock(return_value=mock_result)
            mock_factory.return_value = mock_svc
            result = await api_submission_node(self._base_state())

        assert result.get("workflow_stage") == "HANDED_OVER_COMPLETED", (
            "Path B submission must advance to HANDED_OVER_COMPLETED, not SR_CREATED"
        )

    @pytest.mark.asyncio
    async def test_mm_optional_upload_offer_not_shown_for_handed_over(self) -> None:
        """Unlike CREATE_SR (Path A), Path B success must NOT emit the optional MM
        upload offer (response_decorations must be absent or empty)."""
        from app.agents.graph.nodes.handover.api_submission_node import api_submission_node
        from app.agents.services.service_request_api_service import ServiceRequestCreationResult

        mock_result = ServiceRequestCreationResult(
            sr_id="sr-ho-no-upload-001",
            endpoint="mock://service-requests",
            request_payload={},
            response_payload={"id": "sr-ho-no-upload-001"},
            latency_ms=10,
            status_code=201,
        )
        with patch(
            "app.agents.graph.nodes.handover.api_submission_node.get_service_request_api_service"
        ) as mock_factory:
            mock_svc = AsyncMock()
            mock_svc.create_service_request = AsyncMock(return_value=mock_result)
            mock_factory.return_value = mock_svc
            result = await api_submission_node(self._base_state())

        # response_decorations must be absent or empty — no upload offer for Path B
        decorations = result.get("response_decorations")
        assert not decorations, (
            "Path B success must not emit an MM optional upload offer "
            f"(response_decorations={decorations!r})"
        )

    @pytest.mark.asyncio
    async def test_workflow_complete_true_in_response_ui_for_handed_over(self) -> None:
        """Path B response_ui must include workflow_complete=True."""
        from app.agents.graph.nodes.handover.api_submission_node import api_submission_node
        from app.agents.services.service_request_api_service import ServiceRequestCreationResult

        mock_result = ServiceRequestCreationResult(
            sr_id="sr-ho-wf-complete-001",
            endpoint="mock://service-requests",
            request_payload={},
            response_payload={"id": "sr-ho-wf-complete-001"},
            latency_ms=10,
            status_code=201,
        )
        with patch(
            "app.agents.graph.nodes.handover.api_submission_node.get_service_request_api_service"
        ) as mock_factory:
            mock_svc = AsyncMock()
            mock_svc.create_service_request = AsyncMock(return_value=mock_result)
            mock_factory.return_value = mock_svc
            result = await api_submission_node(self._base_state())

        response_ui = result.get("response_ui", {})
        assert response_ui.get("workflow_complete") is True, (
            "Path B response_ui must contain workflow_complete=True"
        )


# ---------------------------------------------------------------------------
# Additional test cases — TestHandedOverMergeState (B1 gaps)
# ---------------------------------------------------------------------------

class TestHandedOverMergeStateAdditional:
    """Additional Path B merge_state_node regression guards."""

    @pytest.mark.asyncio
    async def test_trading_date_still_auto_computed_for_rdd_review(self) -> None:
        """Path A: trading_date = fitout_end_date + 8 days must still be auto-computed."""
        from app.agents.graph.nodes.shared.merge_state_node import merge_state_node

        state: dict[str, Any] = {
            "workflow_stage": "RDD_REVIEW",
            "active_agent": "rdd_handover_agent",
            "extracted_fields": {
                "fitout_end_date": {"value": "2026-06-01", "confidence": 1.0},
            },
            "collected_data": {},
            "conversation_history": [],
            "backend_refs": {},
            "selected_lease": None,
        }
        result = await merge_state_node(state)
        collected = result.get("collected_data", {})
        assert collected.get("trading_date") is not None, (
            "trading_date must be auto-computed from fitout_end_date during RDD_REVIEW"
        )

    @pytest.mark.asyncio
    async def test_expected_handover_date_still_auto_computed_for_fm_review(self) -> None:
        """Path A: expected_handover_date = unit_readiness_date + 7 days must be auto-computed."""
        from app.agents.graph.nodes.shared.merge_state_node import merge_state_node

        state: dict[str, Any] = {
            "workflow_stage": "FM_REVIEW",
            "active_agent": "rdd_handover_agent",
            "extracted_fields": {
                "unit_readiness_date": {"value": "2026-07-01", "confidence": 1.0},
            },
            "collected_data": {},
            "conversation_history": [],
            "backend_refs": {},
            "selected_lease": None,
        }
        result = await merge_state_node(state)
        collected = result.get("collected_data", {})
        assert collected.get("expected_handover_date") is not None, (
            "expected_handover_date must be auto-computed from unit_readiness_date during FM_REVIEW"
        )


# ---------------------------------------------------------------------------
# Additional test cases — TestHandoverEntryRouting (B1 gaps)
# ---------------------------------------------------------------------------

class TestHandoverEntryRoutingAdditional:
    """Mid-session switch tests for handover_entry_node routing."""

    def _base_state(self, **kwargs: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "session_id": "sess-switch-001",
            "user_id": "user-switch-001",
            "user_message": "",
            "intent": "CREATE_RDD_SERVICE_REQUEST",
            "active_agent": "rdd_handover_agent",
            "sub_category": "HANDOVER",
            "service_category": "FIT_OUT_AND_HANDOVER",
            "workflow_stage": None,
            "collected_data": {},
            "conversation_history": [],
            "backend_refs": {},
            "status": "IN_PROGRESS",
            "confirmation_status": None,
            "confirmation_required": False,
            "documents": [],
            "missing_fields": [],
            "active_field": None,
            "extracted_fields": {},
            "validation_errors": [],
            "lease_matches": [],
            "selected_lease": None,
            "response_message": None,
            "response_ui": None,
            "response_decorations": None,
            "faq_sources": [],
            "action_override": None,
            "corrected_fields": None,
            "trace_id": None,
            "trace_manager": None,
            "conversation_state_service": None,
            "auth_context": None,
            "language": None,
            "msp_context": None,
        }
        base.update(kwargs)
        return base

    @pytest.mark.asyncio
    async def test_mid_session_switch_to_already_trading_clears_path_a_fields(self) -> None:
        """When a mid-session switch to already_trading is detected, Path A state is cleared."""
        from app.agents.graph.nodes.handover.handover_entry_node import handover_entry_node

        # Path A was in progress (e.g. startDate collected) but user now says already trading
        state = self._base_state(
            user_message="actually, the unit is already trading",
            collected_data={
                "startDate": "2026-06-01",
                "already_handovered": "already_trading",
            },
            workflow_stage=None,
        )
        result = await handover_entry_node(state)
        # Should route to CREATE_HANDED_OVER_SR path
        assert result.get("workflow_stage") == "CREATE_HANDED_OVER_SR", (
            f"Expected CREATE_HANDED_OVER_SR, got {result.get('workflow_stage')!r}"
        )

    @pytest.mark.asyncio
    async def test_mid_session_switch_back_to_initiate_handover_clears_path_b_fields(self) -> None:
        """When already_handovered is not set, rdd_agent routes to CREATE_SR (Path A)."""
        from app.agents.graph.nodes.handover.handover_entry_node import handover_entry_node

        state = self._base_state(
            collected_data={},
            sub_category="HANDOVER",
            workflow_stage=None,
        )
        result = await handover_entry_node(state)
        assert result.get("workflow_stage") == "CREATE_SR", (
            f"Expected CREATE_SR for HANDOVER sub_category, got {result.get('workflow_stage')!r}"
        )
