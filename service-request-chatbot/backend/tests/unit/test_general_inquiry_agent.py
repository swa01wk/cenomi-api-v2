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
    _route_after_load,
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
    infer_title_and_description,
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
        # The optional document step is already answered, so confirmation
        # builds the review card instead of offering Upload / Skip.
        "gi_optional_upload_status": "done",
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
        assert wc.collection_stages == frozenset({"CREATE_SR", "GI_CRT_REVIEW"})
        assert wc.confirmation_nodes == {
            "CREATE_SR": "general_inquiry_confirmation",
            "GI_CRT_REVIEW": "general_inquiry_confirmation",
        }
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
        assert "title" not in missing
        assert "description" not in missing
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
        [
            "MALL_MANAGER",
            "FM_MANAGER",
            "OPERATIONS",
            "DD_ENGINEER",
            "ADMIN",
            "CUSTOMER_RELATIONS_TEAM",
        ],
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

    @pytest.mark.asyncio
    async def test_approve_action_asks_for_a_comment(self) -> None:
        state = _state(
            workflow_stage="GI_CRT_REVIEW",
            action_override="confirm_approve_gi",
            backend_refs={"sr_id": "8898"},
        )
        result = await general_inquiry_entry_node(state)
        assert result["confirmation_status"] is None
        assert result["backend_refs"]["gi_pending_action"] == "approve"
        assert result["collected_data"]["gi_pending_action"] == "approve"
        assert "comment" in result["response_message"].lower()
        assert _route_after_general_inquiry_entry({**state, **result}) == "response_generation"

    @pytest.mark.asyncio
    async def test_approve_comment_confirms_review(self) -> None:
        state = _state(
            workflow_stage="GI_CRT_REVIEW",
            user_message="Approved — tenant can proceed",
            action_override=None,
            backend_refs={"sr_id": "8898"},
            collected_data={"gi_pending_action": "approve", "title": "Hours"},
        )
        result = await general_inquiry_entry_node(state)
        assert result["confirmation_status"] == "CONFIRMED"
        assert result["backend_refs"]["gi_action"] == "approve"
        assert result["collected_data"]["review_comment"] == "Approved — tenant can proceed"

    @pytest.mark.asyncio
    async def test_reject_action_asks_for_a_comment(self) -> None:
        state = _state(workflow_stage="GI_CRT_REVIEW", action_override="reject_gi")
        result = await general_inquiry_entry_node(state)
        assert result["backend_refs"]["gi_pending_action"] == "reject"
        assert result["collected_data"]["gi_pending_action"] == "reject"
        assert "comment" in result["response_message"].lower()
        assert _route_after_general_inquiry_entry({**state, **result}) == "response_generation"

    @pytest.mark.asyncio
    async def test_reject_comment_is_kept_on_the_draft(self) -> None:
        """The next turn only has collected_data — backend_refs lost the pending flag."""
        state = _state(
            workflow_stage="GI_CRT_REVIEW",
            user_message="Out of scope for mall management",
            action_override=None,
            backend_refs={"sr_id": "8899"},
            collected_data={"gi_pending_action": "reject", "title": "Hours"},
        )
        result = await general_inquiry_entry_node(state)
        assert result["confirmation_status"] == "CONFIRMED"
        assert result["backend_refs"]["gi_action"] == "reject"
        assert result["collected_data"]["gi_action"] == "reject"
        assert "gi_pending_action" not in result["collected_data"]
        assert result["collected_data"]["review_comment"] == "Out of scope for mall management"
        assert _route_after_load(state) == "general_inquiry_entry"

    @pytest.mark.asyncio
    async def test_ask_more_info_requires_a_comment(self) -> None:
        state = _state(
            workflow_stage="GI_CRT_REVIEW",
            action_override="ask_more_info_gi",
        )
        result = await general_inquiry_entry_node(state)
        assert result["collected_data"]["gi_pending_action"] == "ask_more_info"
        assert result["confirmation_status"] is None
        assert "comment" in result["response_message"].lower()

    @pytest.mark.asyncio
    async def test_reassign_asks_for_a_role_then_a_comment(self) -> None:
        state = _state(
            workflow_stage="GI_CRT_REVIEW",
            action_override="reassign_gi",
        )
        asked = await general_inquiry_entry_node(state)
        assert asked["collected_data"]["gi_pending_action"] == "reassign"
        assert asked["confirmation_status"] is None

        picked = _state(
            workflow_stage="GI_CRT_REVIEW",
            user_message="MALL_MANAGER",
            collected_data={"gi_pending_action": "reassign", "title": "Hours"},
        )
        role_turn = await general_inquiry_entry_node(picked)
        assert role_turn["confirmation_status"] is None
        assert role_turn["collected_data"]["gi_pending_action"] == "reassign_comment"
        assert role_turn["collected_data"]["assign_to"] == "MALL_MANAGER"
        assert "comment" in role_turn["response_message"].lower()

        commented = _state(
            workflow_stage="GI_CRT_REVIEW",
            user_message="Please check the shop front",
            collected_data=role_turn["collected_data"],
        )
        done = await general_inquiry_entry_node(commented)
        assert done["confirmation_status"] == "CONFIRMED"
        assert done["collected_data"]["gi_action"] == "reassign"
        assert done["collected_data"]["assign_to"] == "MALL_MANAGER"
        assert done["collected_data"]["review_comment"] == "Please check the shop front"

    def test_review_card_has_no_add_comment_button(self) -> None:
        from app.agents.services.general_inquiry_review_summary import build_gi_review_card

        card = build_gi_review_card({"title": "Hours"}, sr_id="1")
        actions = {item["action"] for item in card["actions"]}
        assert "add_comment_gi" not in actions
        assert "confirm_approve_gi" in actions
        assert "reassign_gi" in actions
        assert "ask_more_info_gi" in actions

    def test_entry_rejected_goes_to_extraction(self) -> None:
        state = _state(confirmation_status="REJECTED")
        assert _route_after_general_inquiry_entry(state) == "field_extraction"

    def test_entry_waiting_without_a_field_goes_to_response(self) -> None:
        state = _state(status="WAITING_FOR_USER")
        assert _route_after_general_inquiry_entry(state) == "response_generation"

    def test_lease_code_answer_goes_to_extraction(self) -> None:
        """The reply to 'share the lease code' must be looked up, not narrated."""
        state = _state(
            status="WAITING_FOR_USER",
            active_field="lease_code",
            user_message="I want to create a General Inquiry service request for lease t0028935",
        )
        assert _route_after_general_inquiry_entry(state) == "field_extraction"

    def test_lease_chip_goes_to_merge(self) -> None:
        state = _state(
            status="WAITING_FOR_USER",
            selected_lease={"id": "t0028935"},
            user_message="I want to create a General Inquiry service request for Brand X, lease t0028935.",
        )
        assert _route_after_general_inquiry_entry(state) == "merge_state"

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
    async def test_uploaded_inquiry_file_continues_past_the_card(self) -> None:
        collected = _complete_collected()
        collected["gi_optional_upload_status"] = "awaiting_upload"
        state = _state(
            collected_data=collected,
            user_message=" ",
            documents=[
                {
                    "document_id": "3ad19f54-c7d1-4e29-a046-200000000001",
                    "document_type_id": "SR_GEN_INQ",
                }
            ],
        )
        result = await general_inquiry_entry_node(state)
        assert result["collected_data"]["gi_optional_upload_status"] == "done"
        assert result["status"] == "IN_PROGRESS"
        assert result.get("response_ui") == {}

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
    async def test_confirmation_card_has_confirm_action(self) -> None:
        """The confirmation card must carry confirmAction='confirm_submit'.

        Without it the frontend sends action_override=undefined and
        general_inquiry_entry_node never sets confirmation_status=CONFIRMED —
        the flow loops forever on the card.
        """
        state = _state(collected_data=_complete_collected())
        result = await general_inquiry_confirmation_node(state)
        card = result.get("response_ui") or {}
        assert card.get("confirmAction") == "confirm_submit", (
            f"confirmation_card is missing confirmAction='confirm_submit'; got: {card.get('confirmAction')!r}"
        )

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
    async def test_shows_the_document_upload_before_the_card(self) -> None:
        collected = _complete_collected()
        collected.pop("gi_optional_upload_status")
        state = _state(collected_data=collected)
        result = await general_inquiry_confirmation_node(state)
        ui = result["response_ui"]
        assert ui["type"] == "document_requirement"
        options = ui["suggestions"]["options"]
        assert [opt["label"] for opt in options] == ["Skip"]
        assert result["collected_data"]["gi_optional_upload_status"] == "awaiting_upload"

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
        with patch(
            "app.agents.graph.nodes.general_inquiry.general_inquiry_payload_builder_node.lookup_primary_tenant_contact",
            new=AsyncMock(return_value=("Jane Doe - Primary Tenant Contact", 42)),
        ):
            result = await general_inquiry_payload_builder_node(state)
        payload = result["backend_refs"]["create_payload"]
        assert payload["service_category"] == "INQUIRY"
        assert payload["sub_category"] == "GENERAL"
        assert payload["title"] == "Query about service charge"
        assert payload["lease_code"] == "t0105712"
        assert payload["lease_id"] == 95404
        assert payload["payload"]["description"].startswith("Please clarify")
        assert payload["payload"]["documents_ids"] == []
        assert payload["payload"]["company_name"] == "116"
        assert payload["payload"]["tenant_contact"] == "Jane Doe - Primary Tenant Contact"
        assert payload["created_for"] == 42
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
        with patch(
            "app.agents.graph.nodes.general_inquiry.general_inquiry_payload_builder_node.lookup_primary_tenant_contact",
            new=AsyncMock(return_value=("", None)),
        ):
            result = await general_inquiry_payload_builder_node(state)
        inner = result["backend_refs"]["create_payload"]["payload"]
        assert inner["documents_ids"] == ["doc-aaa", "doc-bbb"]
        assert inner["file"] == "doc-aaa"
        assert inner["company_name"] == "116"

    @pytest.mark.asyncio
    async def test_reject_body_includes_the_inquiry_payload(self) -> None:
        state = _state(
            workflow_stage="GI_CRT_REVIEW",
            collected_data={
                **_complete_collected(),
                "gi_action": "reject",
                "review_comment": "Out of scope",
            },
            backend_refs={"sr_id": "8899"},
            documents=[
                {
                    "document_id": "gen-inq-1",
                    "document_type_id": "SR_GEN_INQ",
                    "filename": "note.pdf",
                }
            ],
        )
        with patch(
            "app.agents.graph.nodes.general_inquiry.general_inquiry_payload_builder_node.lookup_primary_tenant_contact",
            new=AsyncMock(return_value=("", None)),
        ):
            result = await general_inquiry_payload_builder_node(state)
        body = result["backend_refs"]["create_payload"]
        assert body["status"] == "REJECTED"
        assert body["comment"] == "Out of scope"
        assert body["payload"]["title"] == "Query about service charge"
        assert "gen-inq-1" in body["payload"]["documents_ids"]


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

    @pytest.mark.asyncio
    async def test_expired_lease_asks_for_another_lease(self) -> None:
        mock_result = ServiceRequestCreationResult(
            sr_id=None,
            endpoint="/service-requests",
            request_payload={},
            response_payload=None,
            latency_ms=5,
            status_code=400,
            error="HTTP 400: This lease has expired and cannot be used to create a service request.",
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
        assert "has not ended" in result["response_message"]


# ---------------------------------------------------------------------------
# Regression: missing_field_node must never ask handover questions for GI
# ---------------------------------------------------------------------------


class TestMissingFieldNodeGI:
    """missing_field_node uses GI schema, not handover schema, for general_inquiry_agent.

    Regression guard: before the fix, missing_field_node fell through to
    ``handover_get_missing_fields``, whose first user-missing field for CREATE_SR
    is ``already_handovered``, causing the "Has the handover already been completed
    for this lease?" question (with Already Trading / Initiate Handover chips) to
    appear in every GI session.
    """

    @pytest.mark.asyncio
    async def test_no_handover_question_when_no_lease_resolved(self) -> None:
        """With no lease resolved, the bot must ask for lease_code, not already_handovered."""
        from app.agents.graph.nodes.shared.missing_field_node import missing_field_node

        state = _state(
            collected_data={},
            workflow_stage="CREATE_SR",
        )
        result = await missing_field_node(state)  # type: ignore[arg-type]

        # The response must NOT mention the handover question or show handover chips
        msg = (result.get("response_message") or "").lower()
        assert "handover" not in msg, (
            f"GI missing_field_node asked a handover question: {msg!r}"
        )
        decorations = result.get("response_decorations") or {}
        suggestions = (decorations.get("suggestions") or {}).get("options") or []
        option_actions = {opt.get("action") for opt in suggestions}
        assert "select_handover_type_already_trading" not in option_actions, (
            "GI missing_field_node returned 'Already Trading' chip — handover schema was used"
        )
        assert "select_handover_type_initiate_handover" not in option_actions, (
            "GI missing_field_node returned 'Initiate Handover' chip — handover schema was used"
        )
        # The active_field must be a GI-relevant field, not a handover-only one
        active_field = result.get("active_field")
        assert active_field != "already_handovered", (
            "GI missing_field_node set active_field=already_handovered"
        )

    @pytest.mark.asyncio
    async def test_lease_selection_goes_straight_to_document_upload(self) -> None:
        """Lease and brand fill the title and description. Next step is the upload."""
        from app.agents.graph.help_agent_graph import _route_after_validation
        from app.agents.graph.nodes.general_inquiry.general_inquiry_confirmation_node import (
            general_inquiry_confirmation_node,
        )

        collected = {
            k: v
            for k, v in _complete_collected().items()
            if k not in ("title", "description", "gi_optional_upload_status")
        }
        state = _state(collected_data=collected, workflow_stage="CREATE_SR")
        assert _route_after_validation(state) == "general_inquiry_confirmation"

        result = await general_inquiry_confirmation_node(state)  # type: ignore[arg-type]
        ui = result.get("response_ui") or {}
        assert ui.get("type") == "document_requirement"
        saved = result["collected_data"]
        assert saved["title"] == (
            "General Inquiry - Brand Under Armour - Jawharat Jeddah"
        )
        assert saved["description"] == (
            "General Inquiry for Brand Under Armour at Jawharat Jeddah, lease t0105712."
        )
        assert "guide you through the form" not in (result.get("response_message") or "").lower()
        assert "subject of your inquiry" not in (result.get("response_message") or "").lower()


def test_draft_fields_include_title_as_soon_as_it_is_collected() -> None:
    """The form boxes read draft_preview, not the later confirmation card."""
    from app.agents.graph.nodes.general_inquiry.general_inquiry_confirmation_node import (
        general_inquiry_draft_fields,
    )

    rows = general_inquiry_draft_fields(
        {
            "lease_code": "t0105579",
            "title": "t4est",
            "guided_tour": "guided",
            "gi_optional_upload_status": "awaiting_choice",
        }
    )
    by_key = {row["key"]: row["value"] for row in rows}
    assert by_key["title"] == "t4est"
    assert by_key["lease_code"] == "t0105579"
    assert "guided_tour" not in by_key
    assert "description" not in by_key
    assert "gi_optional_upload_status" not in by_key


def test_one_typed_intent_fills_title_and_description() -> None:
    """A single message is the subject and the description. The latest one wins."""
    assert infer_title_and_description(["Guide me", "yes"]) is None
    assert infer_title_and_description(
        ["I want to create a General Inquiry service request"]
    ) is None
    title, description = infer_title_and_description(
        [
            "Guide me",
            "I want to create a General Inquiry service request",
            "water leak in the store",
        ]
    )
    assert title == "water leak in the store"
    assert description == "water leak in the store"
    short_title, short_description = infer_title_and_description(["uuby"])
    assert short_title == "uuby"
    assert short_description == "uuby"
