"""Unit tests — General Inquiry agent (INQUIRY / GENERAL).

Covers registry, workflow config, RBAC, schema, graph routing, entry,
confirmation, payload builder, and API submission.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.agent_display import agent_display_name
from app.agents.graph.help_agent_graph import (
    _AGENT_ENTRY_NODES,
    _CREATE_NEW_SR_INTENTS,
    _SR_ACTION_INTENTS,
    _route_after_general_inquiry_confirmation,
    _route_after_general_inquiry_entry,
    _route_after_validation,
)
from app.agents.graph.nodes.general_inquiry.general_inquiry_api_submission_node import (
    general_inquiry_api_submission_node,
)
from app.agents.graph.nodes.general_inquiry.general_inquiry_confirmation_node import (
    general_inquiry_confirmation_node,
)
from app.agents.graph.nodes.general_inquiry.general_inquiry_entry_node import (
    general_inquiry_entry_node,
)
from app.agents.graph.nodes.general_inquiry.general_inquiry_payload_builder_node import (
    general_inquiry_payload_builder_node,
)
from app.agents.registry import lookup_agent
from app.agents.registries.workflow_config import get_workflow_config
from app.agents.schemas.general_inquiry_schema import (
    OPTIONAL_FIELDS,
    SERVICE_CATEGORY,
    SUB_CATEGORY,
    get_missing_fields,
)
from app.agents.schemas.help_agent_schema import (
    ALL_INTENTS,
    ROLE_PERMITTED_INTENTS,
    intents_for_roles,
)
from app.agents.schemas.supervisor_schema import SupervisorDecision
from app.agents.services.service_request_api_service import ServiceRequestCreationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _complete_collected() -> dict[str, Any]:
    return {
        "title": "Query about service charge",
        "description": "Please clarify the Q3 service charge breakdown.",
        "notes": "",
        "lease_code": "t0105712",
        "lease_id": 95404,
        "brand_id": 267,
        "property_id": 3041,
        "tenant_profile_id": 116,
        "mall": "Jawharat Jeddah",
        "brand": "Brand Under Armour",
        "lease": "t0105712",
        "lease_brand_mall": "t0105712 - Brand Under Armour - Jawharat Jeddah",
        "contract_id": 95404,
        "unit_codes": ["FF050"],
        "city": "Jeddah",
    }


def _state(**kwargs: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": "gi-test-session",
        "user_id": "gi-test-user",
        "active_agent": "general_inquiry_agent",
        "workflow_stage": "CREATE_SR",
        "collected_data": {},
        "validation_errors": [],
        "status": "IN_PROGRESS",
        "intent": "CREATE_GENERAL_INQUIRY",
        "backend_refs": {},
        "user_message": "",
        "confirmation_status": None,
        "action_override": None,
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# Registry / config / display
# ---------------------------------------------------------------------------


class TestGeneralInquiryRegistry:
    def test_lookup_maps_inquiry_general(self) -> None:
        cfg = lookup_agent("INQUIRY", "GENERAL")
        assert cfg is not None
        assert cfg["agent_name"] == "general_inquiry_agent"
        assert cfg["display_name"] == "General Inquiry Agent"

    def test_lookup_missing_subcategory(self) -> None:
        assert lookup_agent("INQUIRY", "UNKNOWN") is None

    def test_display_name(self) -> None:
        assert agent_display_name("general_inquiry_agent") == "General Inquiry Agent"

    def test_workflow_config(self) -> None:
        wc = get_workflow_config("general_inquiry_agent")
        assert wc is not None
        assert wc.agent_name == "general_inquiry_agent"
        assert wc.collection_stages == frozenset({"CREATE_SR"})
        assert wc.confirmation_nodes == {"CREATE_SR": "general_inquiry_confirmation"}
        assert wc.terminal_stages == frozenset({"SR_CREATED"})
        assert "CREATE_GENERAL_INQUIRY" in wc.action_intents
        assert "lease_code" in wc.lease_trigger_fields

    def test_schema_constants(self) -> None:
        assert SERVICE_CATEGORY == "INQUIRY"
        assert SUB_CATEGORY == "GENERAL"
        assert OPTIONAL_FIELDS == frozenset({"notes"})


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestGeneralInquirySchema:
    def test_empty_draft_missing_required(self) -> None:
        missing = get_missing_fields("CREATE_SR", {})
        assert "title" in missing
        assert "description" in missing
        assert "lease_code" in missing
        assert "lease_id" in missing

    def test_complete_draft_no_missing(self) -> None:
        assert get_missing_fields("CREATE_SR", _complete_collected()) == []

    def test_notes_optional_not_required(self) -> None:
        data = _complete_collected()
        data.pop("notes", None)
        missing = get_missing_fields("CREATE_SR", data)
        assert "notes" not in missing

    def test_unknown_stage_returns_empty(self) -> None:
        assert get_missing_fields("DOES_NOT_EXIST", {}) == []


# ---------------------------------------------------------------------------
# Supervisor / RBAC
# ---------------------------------------------------------------------------


class TestGeneralInquirySupervisorAndRbac:
    def test_supervisor_decision_accepts_intent(self) -> None:
        d = SupervisorDecision(
            intent="CREATE_GENERAL_INQUIRY",
            confidence=0.95,
            service_category="INQUIRY",
            sub_category="GENERAL",
            target_agent="general_inquiry_agent",
            reasoning="user wants a general inquiry",
        )
        assert d.intent == "CREATE_GENERAL_INQUIRY"
        assert d.target_agent == "general_inquiry_agent"

    def test_intent_in_all_intents(self) -> None:
        assert "CREATE_GENERAL_INQUIRY" in ALL_INTENTS

    def test_intent_in_sr_action_intents(self) -> None:
        assert "CREATE_GENERAL_INQUIRY" in _SR_ACTION_INTENTS

    def test_intent_in_create_new_sr_intents(self) -> None:
        assert "CREATE_GENERAL_INQUIRY" in _CREATE_NEW_SR_INTENTS

    def test_entry_dispatch(self) -> None:
        assert _AGENT_ENTRY_NODES["general_inquiry_agent"] == "general_inquiry_entry"

    @pytest.mark.parametrize(
        "role",
        ["MALL_MANAGER", "FM_MANAGER", "OPERATIONS", "DD_ENGINEER", "ADMIN"],
    )
    def test_admin_roles_permitted(self, role: str) -> None:
        assert "CREATE_GENERAL_INQUIRY" in ROLE_PERMITTED_INTENTS[role]

    @pytest.mark.parametrize(
        "role",
        [
            "AUTHORIZED_SIGNATORY",
            "BILLING_CONTACT",
            "PRIMARY_TENANT_CONTACT",
            "SHOP_MANAGER",
            "COMPANY_ADMIN",
        ],
    )
    def test_tenant_roles_permitted(self, role: str) -> None:
        permitted = intents_for_roles(frozenset({role}))
        assert "CREATE_GENERAL_INQUIRY" in permitted


# ---------------------------------------------------------------------------
# Graph routing
# ---------------------------------------------------------------------------


class TestGeneralInquiryRouting:
    def test_entry_confirmed_goes_to_merge(self) -> None:
        state = _state(confirmation_status="CONFIRMED")
        assert _route_after_general_inquiry_entry(state) == "merge_state"

    def test_entry_rejected_goes_to_extraction(self) -> None:
        state = _state(confirmation_status="REJECTED")
        assert _route_after_general_inquiry_entry(state) == "field_extraction"

    def test_entry_waiting_goes_to_response(self) -> None:
        state = _state(status="WAITING_FOR_USER")
        assert _route_after_general_inquiry_entry(state) == "response_generation"

    def test_entry_default_goes_to_extraction(self) -> None:
        state = _state()
        assert _route_after_general_inquiry_entry(state) == "field_extraction"

    def test_confirmation_confirmed_goes_to_payload(self) -> None:
        state = _state(confirmation_status="CONFIRMED")
        assert (
            _route_after_general_inquiry_confirmation(state)
            == "general_inquiry_payload_builder"
        )

    def test_confirmation_pending_goes_to_response(self) -> None:
        state = _state(confirmation_status="PENDING")
        assert _route_after_general_inquiry_confirmation(state) == "response_generation"

    def test_validation_complete_routes_to_gi_confirmation(self) -> None:
        state = _state(
            active_agent="general_inquiry_agent",
            workflow_stage="CREATE_SR",
            collected_data=_complete_collected(),
            validation_errors=[],
        )
        assert _route_after_validation(state) == "general_inquiry_confirmation"

    def test_validation_missing_routes_to_missing_field(self) -> None:
        state = _state(
            active_agent="general_inquiry_agent",
            workflow_stage="CREATE_SR",
            collected_data={"title": "only title"},
            validation_errors=[],
        )
        assert _route_after_validation(state) == "missing_field"


# ---------------------------------------------------------------------------
# Entry node
# ---------------------------------------------------------------------------


class TestGeneralInquiryEntryNode:
    @pytest.mark.asyncio
    async def test_sets_create_sr_on_first_entry(self) -> None:
        state = _state(workflow_stage=None)
        result = await general_inquiry_entry_node(state)
        assert result["workflow_stage"] == "CREATE_SR"

    @pytest.mark.asyncio
    async def test_confirm_phrase_sets_confirmed(self) -> None:
        state = _state(confirmation_status="PENDING", user_message="yes")
        result = await general_inquiry_entry_node(state)
        assert result["confirmation_status"] == "CONFIRMED"

    @pytest.mark.asyncio
    async def test_reject_phrase_sets_rejected(self) -> None:
        state = _state(confirmation_status="PENDING", user_message="change")
        result = await general_inquiry_entry_node(state)
        assert result["confirmation_status"] == "REJECTED"

    @pytest.mark.asyncio
    async def test_confirm_submit_action(self) -> None:
        state = _state(action_override="confirm_submit")
        result = await general_inquiry_entry_node(state)
        assert result["confirmation_status"] == "CONFIRMED"

    @pytest.mark.asyncio
    async def test_cancel_clears_agent(self) -> None:
        state = _state(user_message="cancel")
        result = await general_inquiry_entry_node(state)
        assert result["active_agent"] is None
        assert result["status"] == "CANCELLED"


# ---------------------------------------------------------------------------
# Confirmation node
# ---------------------------------------------------------------------------


class TestGeneralInquiryConfirmationNode:
    @pytest.mark.asyncio
    async def test_builds_card_when_complete(self) -> None:
        state = _state(collected_data=_complete_collected())
        result = await general_inquiry_confirmation_node(state)
        assert result["confirmation_status"] == "PENDING"
        assert result["status"] == "READY_TO_SUBMIT"
        assert result["response_ui"]["type"] == "confirmation_card"
        assert result["response_ui"]["requestType"] == "General Inquiry"
        keys = {f["key"] for f in result["response_ui"]["fields"]}
        assert "title" in keys
        assert "description" in keys
        assert "lease_code" in keys

    @pytest.mark.asyncio
    async def test_skips_when_missing_required(self) -> None:
        state = _state(collected_data={"title": "only"})
        result = await general_inquiry_confirmation_node(state)
        assert result == {}

    @pytest.mark.asyncio
    async def test_noop_when_already_confirmed(self) -> None:
        state = _state(
            collected_data=_complete_collected(),
            confirmation_status="CONFIRMED",
        )
        result = await general_inquiry_confirmation_node(state)
        assert result == {}


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------


class TestGeneralInquiryPayloadBuilder:
    @pytest.mark.asyncio
    async def test_builds_inquiry_general_shape(self) -> None:
        state = _state(collected_data=_complete_collected())
        result = await general_inquiry_payload_builder_node(state)
        payload = result["backend_refs"]["create_payload"]
        assert payload["service_category"] == "INQUIRY"
        assert payload["sub_category"] == "GENERAL"
        assert payload["title"] == "Query about service charge"
        assert payload["lease_code"] == "t0105712"
        assert payload["lease_id"] == 95404
        assert payload["payload"]["description"].startswith("Please clarify")
        assert payload["payload"]["documents_ids"] == []
        assert result["status"] == "READY_TO_SUBMIT"

    @pytest.mark.asyncio
    async def test_includes_uploaded_document_ids(self) -> None:
        state = _state(
            collected_data=_complete_collected(),
            backend_refs={
                "uploaded_documents": [
                    {"document_id": "doc-aaa"},
                    {"document_id": "doc-bbb"},
                ]
            },
        )
        result = await general_inquiry_payload_builder_node(state)
        inner = result["backend_refs"]["create_payload"]["payload"]
        assert inner["documents_ids"] == ["doc-aaa", "doc-bbb"]
        assert inner["file"] == "doc-aaa"


# ---------------------------------------------------------------------------
# API submission
# ---------------------------------------------------------------------------


class TestGeneralInquiryApiSubmission:
    @pytest.mark.asyncio
    async def test_blocked_when_not_confirmed(self) -> None:
        state = _state(
            confirmation_status="PENDING",
            backend_refs={"create_payload": {"title": "x"}},
        )
        result = await general_inquiry_api_submission_node(state)
        assert result["status"] == "FAILED"
        assert "not been confirmed" in result["response_message"]

    @pytest.mark.asyncio
    async def test_blocked_when_no_payload(self) -> None:
        state = _state(confirmation_status="CONFIRMED", backend_refs={})
        result = await general_inquiry_api_submission_node(state)
        assert result["status"] == "FAILED"
        assert "payload not found" in result["response_message"]

    @pytest.mark.asyncio
    async def test_blocked_on_validation_errors(self) -> None:
        state = _state(
            confirmation_status="CONFIRMED",
            validation_errors=[{"field": "title", "blocking": True}],
            backend_refs={"create_payload": {"title": "x"}},
        )
        result = await general_inquiry_api_submission_node(state)
        assert result["status"] == "FAILED"
        assert "validation error" in result["response_message"]

    @pytest.mark.asyncio
    async def test_success_sets_sr_created(self) -> None:
        mock_result = ServiceRequestCreationResult(
            sr_id="8431",
            endpoint="/service-requests",
            request_payload={},
            response_payload={"data": {"service_request_id": "8431"}},
            latency_ms=12,
            status_code=200,
            correlation_id="corr-1",
        )
        mock_svc = MagicMock()
        mock_svc.create_service_request = AsyncMock(return_value=mock_result)

        state = _state(
            confirmation_status="CONFIRMED",
            backend_refs={"create_payload": {"service_category": "INQUIRY"}},
        )
        with (
            patch(
                "app.agents.graph.nodes.general_inquiry.general_inquiry_api_submission_node.get_service_request_api_service",
                return_value=mock_svc,
            ),
            patch(
                "app.agents.graph.nodes.general_inquiry.general_inquiry_api_submission_node.record_sr_api_call",
                new_callable=AsyncMock,
            ),
        ):
            result = await general_inquiry_api_submission_node(state)

        assert result["status"] == "SUBMITTED"
        assert result["workflow_stage"] == "SR_CREATED"
        assert result["backend_refs"]["sr_id"] == "8431"
        assert "8431" in result["response_message"]
        mock_svc.create_service_request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_api_error_returns_failed(self) -> None:
        mock_result = ServiceRequestCreationResult(
            sr_id=None,
            endpoint="/service-requests",
            request_payload={},
            response_payload=None,
            latency_ms=5,
            status_code=500,
            error="Failed to insert service request",
        )
        mock_svc = MagicMock()
        mock_svc.create_service_request = AsyncMock(return_value=mock_result)

        state = _state(
            confirmation_status="CONFIRMED",
            backend_refs={"create_payload": {"service_category": "INQUIRY"}},
        )
        with (
            patch(
                "app.agents.graph.nodes.general_inquiry.general_inquiry_api_submission_node.get_service_request_api_service",
                return_value=mock_svc,
            ),
            patch(
                "app.agents.graph.nodes.general_inquiry.general_inquiry_api_submission_node.record_sr_api_call",
                new_callable=AsyncMock,
            ),
        ):
            result = await general_inquiry_api_submission_node(state)

        assert result["status"] == "FAILED"
        assert "API error" in result["response_message"]
