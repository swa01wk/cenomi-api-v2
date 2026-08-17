"""Unit tests — missing_field_node: clarifying questions, one per turn.

Test groups
-----------
TestMissingFieldNodeBasic          — State updates (missing_fields list, status).
TestOneQuestionAtATime             — Exactly one question is asked per invocation.
TestBackendProtectedFieldHandling  — Backend fields trigger lease_code question, not direct ask.
TestLeaseResolutionLogic           — lease_code → lease_brand_mall fallback chain.
TestNoMissingFields                — All fields present → IN_PROGRESS, no question.
TestWorkflowStageRouting           — Correct fields are checked per stage.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.handover.missing_field_node import (
    BACKEND_PROTECTED_FIELDS,
    missing_field_node,
)
from app.agents.schemas.handover_schema import (
    CREATE_SR_STAGE,
    FM_REVIEW_STAGE,
    RDD_REVIEW_STAGE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(**overrides: Any) -> dict[str, Any]:
    return {"session_id": "sess-test", "user_id": "user-test", **overrides}


def _collected(**fields: Any) -> dict[str, Any]:
    return dict(fields)


# ---------------------------------------------------------------------------
# TestMissingFieldNodeBasic
# ---------------------------------------------------------------------------


class TestMissingFieldNodeBasic:
    @pytest.mark.asyncio
    async def test_returns_missing_fields_list(self) -> None:
        """Node must always return a 'missing_fields' key in its output."""
        state = _state(workflow_stage="FM_REVIEW", collected_data={})
        result = await missing_field_node(state)
        assert "missing_fields" in result
        assert isinstance(result["missing_fields"], list)

    @pytest.mark.asyncio
    async def test_missing_fields_contains_absent_required_fields(self) -> None:
        state = _state(workflow_stage="FM_REVIEW", collected_data={})
        result = await missing_field_node(state)
        missing = set(result["missing_fields"])
        assert "unit_readiness_date" in missing
        # expected_handover_date is auto-computed (not in FM_REVIEW required_fields)
        # comments is now back in FM_REVIEW required_fields (Fix 4)
        assert "comments" in missing

    @pytest.mark.asyncio
    async def test_status_is_waiting_when_question_asked(self) -> None:
        state = _state(workflow_stage="FM_REVIEW", collected_data={})
        result = await missing_field_node(state)
        assert result["status"] == "WAITING_FOR_USER"

    @pytest.mark.asyncio
    async def test_response_message_is_non_empty_when_question_asked(self) -> None:
        state = _state(workflow_stage="FM_REVIEW", collected_data={})
        result = await missing_field_node(state)
        assert isinstance(result["response_message"], str) and result["response_message"]

    @pytest.mark.asyncio
    async def test_response_ui_has_correct_shape(self) -> None:
        state = _state(workflow_stage="FM_REVIEW", collected_data={})
        result = await missing_field_node(state)
        ui = result["response_ui"]
        assert ui["type"] == "text_question"
        assert "field" in ui
        assert "message" in ui

    @pytest.mark.asyncio
    async def test_response_ui_field_matches_response_message_context(self) -> None:
        """The 'field' key in response_ui should correspond to the question being asked."""
        state = _state(workflow_stage="FM_REVIEW", collected_data={})
        result = await missing_field_node(state)
        ui = result["response_ui"]
        assert ui["message"] == result["response_message"]

    @pytest.mark.asyncio
    async def test_active_field_set_when_asking(self) -> None:
        state = _state(workflow_stage="RDD_REVIEW", collected_data={})
        result = await missing_field_node(state)
        assert result["active_field"] == result["response_ui"]["field"]
        assert result["active_field"] is not None

    @pytest.mark.asyncio
    async def test_active_field_cleared_when_nothing_missing(self) -> None:
        collected = {f: "value" for f in FM_REVIEW_STAGE.required_fields}
        state = _state(workflow_stage="FM_REVIEW", collected_data=collected)
        result = await missing_field_node(state)
        assert result["active_field"] is None


# ---------------------------------------------------------------------------
# TestOneQuestionAtATime
# ---------------------------------------------------------------------------


class TestOneQuestionAtATime:
    @pytest.mark.asyncio
    async def test_only_one_question_asked_per_turn(self) -> None:
        """Even when multiple fields are missing, response_message is a single question."""
        state = _state(
            workflow_stage="FM_REVIEW",
            collected_data={},
        )
        result = await missing_field_node(state)
        # response_message should be a single question string, not a list
        assert isinstance(result["response_message"], str)
        # The response_ui has exactly one 'field' key (single target)
        assert isinstance(result["response_ui"]["field"], str)

    @pytest.mark.asyncio
    async def test_after_first_field_answered_next_is_asked(self) -> None:
        """FM_REVIEW has unit_readiness_date + comments. After unit_readiness_date
        is answered, the node asks for comments (optional). After comments → IN_PROGRESS."""
        state1 = _state(workflow_stage="FM_REVIEW", collected_data={})
        result1 = await missing_field_node(state1)
        first_field = result1["response_ui"]["field"]
        assert first_field == "unit_readiness_date"

        # Simulate user answering unit_readiness_date
        collected = {first_field: "2025-06-01"}
        state2 = _state(workflow_stage="FM_REVIEW", collected_data=collected)
        result2 = await missing_field_node(state2)

        # FM_REVIEW now has comments as optional; after unit_readiness_date → asks comments
        assert result2["status"] == "WAITING_FOR_USER"
        assert result2["response_ui"]["field"] == "comments"

        # Simulate declining comments
        collected2 = {**collected, "comments": ""}
        state3 = _state(workflow_stage="FM_REVIEW", collected_data=collected2)
        result3 = await missing_field_node(state3)
        assert result3["status"] == "IN_PROGRESS"

    @pytest.mark.asyncio
    async def test_rdd_review_asks_first_user_field_only(self) -> None:
        """RDD_REVIEW stage with no collected data should ask exactly one question."""
        state = _state(workflow_stage="RDD_REVIEW", collected_data={})
        result = await missing_field_node(state)
        assert result["status"] == "WAITING_FOR_USER"
        assert isinstance(result["response_ui"]["field"], str)

    @pytest.mark.asyncio
    async def test_questions_progress_through_fm_review_fields(self) -> None:
        """Iterating through FM_REVIEW fields one by one should cover all of them."""
        required = list(FM_REVIEW_STAGE.required_fields)
        # Remove backend-protected fields from required (not directly asked)
        user_fields = [f for f in required if f not in BACKEND_PROTECTED_FIELDS]

        collected: dict[str, Any] = {}
        asked_fields: list[str] = []

        for _ in range(len(user_fields) + 1):
            state = _state(workflow_stage="FM_REVIEW", collected_data=collected)
            result = await missing_field_node(state)
            if result["status"] != "WAITING_FOR_USER":
                break
            field = result["response_ui"]["field"]
            asked_fields.append(field)
            collected[field] = "dummy-value"

        assert set(asked_fields) == set(user_fields)


# ---------------------------------------------------------------------------
# TestBackendProtectedFieldHandling
# ---------------------------------------------------------------------------


class TestBackendProtectedFieldHandling:
    @pytest.mark.asyncio
    async def test_backend_missing_triggers_lease_code_question(self) -> None:
        """When backend-derived fields are absent, ask for lease_code, not the backend field."""
        # CREATE_SR requires property_id (backend-derived) and title (user-supplied).
        # With an empty collected_data, the node should ask for lease_code to trigger lookup.
        state = _state(workflow_stage="CREATE_SR", collected_data={})
        result = await missing_field_node(state)
        assert result["status"] == "WAITING_FOR_USER"
        # Must ask for a lease resolution trigger, not a raw backend field.
        asked_field = result["response_ui"]["field"]
        assert asked_field not in BACKEND_PROTECTED_FIELDS

    @pytest.mark.asyncio
    async def test_does_not_ask_tenant_profile_id_directly(self) -> None:
        state = _state(workflow_stage="CREATE_SR", collected_data={})
        result = await missing_field_node(state)
        assert result["response_ui"]["field"] != "tenant_profile_id"

    @pytest.mark.asyncio
    async def test_does_not_ask_property_id_directly(self) -> None:
        state = _state(workflow_stage="CREATE_SR", collected_data={})
        result = await missing_field_node(state)
        assert result["response_ui"]["field"] != "property_id"

    @pytest.mark.asyncio
    async def test_does_not_ask_lease_id_directly(self) -> None:
        state = _state(workflow_stage="CREATE_SR", collected_data={})
        result = await missing_field_node(state)
        assert result["response_ui"]["field"] != "lease_id"

    @pytest.mark.asyncio
    async def test_does_not_emit_lease_resource_picker(self) -> None:
        state = _state(workflow_stage="CREATE_SR", collected_data={})
        result = await missing_field_node(state)
        assert "response_decorations" not in result

    @pytest.mark.asyncio
    async def test_missing_fields_list_includes_backend_fields(self) -> None:
        """missing_fields list should accurately reflect ALL missing fields including backend ones."""
        state = _state(workflow_stage="CREATE_SR", collected_data={})
        result = await missing_field_node(state)
        # Some backend fields should appear in missing_fields even if we don't ask for them
        backend_required = [
            f for f in CREATE_SR_STAGE.required_fields if f in BACKEND_PROTECTED_FIELDS
        ]
        missing_set = set(result["missing_fields"])
        for bf in backend_required:
            assert bf in missing_set, f"Backend field '{bf}' should be in missing_fields"


# ---------------------------------------------------------------------------
# TestLeaseResolutionLogic
# ---------------------------------------------------------------------------


class TestLeaseResolutionLogic:
    @pytest.mark.asyncio
    async def test_asks_lease_code_when_both_trigger_fields_absent(self) -> None:
        """lease_code is preferred over lease_brand_mall when neither is present."""
        state = _state(workflow_stage="CREATE_SR", collected_data={})
        result = await missing_field_node(state)
        # Verify it asks for lease_code specifically (first trigger preference)
        assert result["response_ui"]["field"] == "lease_code"

    @pytest.mark.asyncio
    async def test_falls_back_to_lease_brand_mall_when_lease_code_present(self) -> None:
        """When lease_code is known but backend fields still missing, ask lease_brand_mall."""
        # Provide lease_code but no backend fields yet (simulating lookup pending)
        collected = {"lease_code": "LC-001"}
        state = _state(workflow_stage="CREATE_SR", collected_data=collected)
        result = await missing_field_node(state)
        # Should not ask lease_code again; should progress to lease_brand_mall or user fields
        asked = result["response_ui"]["field"] if result["status"] == "WAITING_FOR_USER" else None
        if asked is not None:
            assert asked != "lease_code"

    @pytest.mark.asyncio
    async def test_does_not_ask_company_after_lease_is_resolved(self) -> None:
        """Successful lease lookup must not re-ask tenant/company when a backend
        field (e.g. city) is empty on that lease — QA-only flake on some codes."""
        collected = {
            "lease_code": "t0031371",
            "lease_id": 42,
            "tenant_profile_id": 116,
            "property_id": 3041,
            "brand_id": 267,
            "mall": "Jawharat Jeddah",
            "brand": "Brand Under Armour",
            "lease": "t0031371",
            "unit_codes": ["FF050"],
            "contracted_area": 420,
            "lease_brand_mall": "t0031371 - Brand Under Armour - Jawharat Jeddah",
            # city omitted — previously kept backend_missing true, so the next
            # empty lease trigger (company) was asked even though the form
            # already showed the tenant from the lease.
        }
        state = _state(
            workflow_stage="CREATE_SR",
            active_agent="rdd_handover_agent",
            collected_data=collected,
        )
        result = await missing_field_node(state)
        asked = (
            result.get("response_ui", {}).get("field")
            if result.get("status") == "WAITING_FOR_USER"
            else None
        )
        assert asked != "company"
        assert asked != "lease_code"
        assert asked != "lease_brand_mall"

    @pytest.mark.asyncio
    async def test_lease_code_question_text_is_from_question_map(self) -> None:
        """The question asked for lease_code must come from HANDOVER_FIELD_QUESTIONS."""
        from app.agents.services.missing_field_service import HANDOVER_FIELD_QUESTIONS

        state = _state(workflow_stage="CREATE_SR", collected_data={})
        result = await missing_field_node(state)
        assert result["response_message"] == HANDOVER_FIELD_QUESTIONS["lease_code"]

    @pytest.mark.asyncio
    async def test_when_both_triggers_present_and_backend_missing_no_question(self) -> None:
        """If both lease_code and lease_brand_mall are present, no lease trigger question needed.
        The node should not ask the user anything further for backend resolution."""
        collected = {"lease_code": "LC-001", "lease_brand_mall": "Nike - Riyadh Park"}
        state = _state(workflow_stage="CREATE_SR", collected_data=collected)
        result = await missing_field_node(state)
        # If backend fields are still missing but both triggers provided,
        # the node falls through to user fields or signals IN_PROGRESS
        asked = result["response_ui"]["field"] if result["status"] == "WAITING_FOR_USER" else None
        if asked is not None:
            assert asked not in BACKEND_PROTECTED_FIELDS
            assert asked not in ("lease_code", "lease_brand_mall")


# ---------------------------------------------------------------------------
# TestNoMissingFields
# ---------------------------------------------------------------------------


class TestNoMissingFields:
    @pytest.mark.asyncio
    async def test_all_fm_review_fields_present_returns_in_progress(self) -> None:
        collected = {f: "value" for f in FM_REVIEW_STAGE.required_fields}
        state = _state(workflow_stage="FM_REVIEW", collected_data=collected)
        result = await missing_field_node(state)
        assert result["status"] == "IN_PROGRESS"

    @pytest.mark.asyncio
    async def test_all_rdd_review_fields_present_returns_in_progress(self) -> None:
        collected = {f: "value" for f in RDD_REVIEW_STAGE.required_fields}
        state = _state(workflow_stage="RDD_REVIEW", collected_data=collected)
        result = await missing_field_node(state)
        assert result["status"] == "IN_PROGRESS"

    @pytest.mark.asyncio
    async def test_no_missing_fields_list_is_empty(self) -> None:
        collected = {f: "value" for f in FM_REVIEW_STAGE.required_fields}
        state = _state(workflow_stage="FM_REVIEW", collected_data=collected)
        result = await missing_field_node(state)
        assert result["missing_fields"] == []

    @pytest.mark.asyncio
    async def test_no_question_when_nothing_missing(self) -> None:
        collected = {f: "value" for f in FM_REVIEW_STAGE.required_fields}
        state = _state(workflow_stage="FM_REVIEW", collected_data=collected)
        result = await missing_field_node(state)
        assert result["response_message"] == ""
        assert "response_ui" not in result


# ---------------------------------------------------------------------------
# TestWorkflowStageRouting
# ---------------------------------------------------------------------------


class TestWorkflowStageRouting:
    @pytest.mark.asyncio
    async def test_default_stage_is_create_sr_when_absent(self) -> None:
        """When workflow_stage is absent, node defaults to CREATE_SR."""
        state = _state(collected_data={})
        result = await missing_field_node(state)
        # CREATE_SR has many required fields including backend ones → should ask lease_code
        assert result["status"] == "WAITING_FOR_USER"

    @pytest.mark.asyncio
    async def test_unknown_stage_falls_back_to_create_sr(self) -> None:
        """An unrecognised stage must not raise and should fall back gracefully."""
        state = _state(workflow_stage="UNKNOWN_STAGE", collected_data={})
        result = await missing_field_node(state)
        assert "missing_fields" in result
        assert "status" in result

    @pytest.mark.asyncio
    async def test_fm_review_only_checks_fm_fields(self) -> None:
        """FM_REVIEW should only report fields from its own StageDefinition."""
        collected = {f: "value" for f in FM_REVIEW_STAGE.required_fields}
        state = _state(workflow_stage="FM_REVIEW", collected_data=collected)
        result = await missing_field_node(state)
        # No CREATE_SR-only fields should appear in missing_fields
        assert result["missing_fields"] == []

    @pytest.mark.asyncio
    async def test_rdd_review_asks_for_rdd_specific_field(self) -> None:
        """RDD_REVIEW should eventually ask for its specific fields."""
        state = _state(workflow_stage="RDD_REVIEW", collected_data={})
        result = await missing_field_node(state)
        rdd_user_fields = [
            f for f in RDD_REVIEW_STAGE.required_fields
            if f not in BACKEND_PROTECTED_FIELDS
        ]
        asked = result["response_ui"]["field"] if result["status"] == "WAITING_FOR_USER" else None
        if asked is not None:
            assert asked in rdd_user_fields


# ---------------------------------------------------------------------------
# WorkflowConfig integration — config-aware question lookup (new tests)
# ---------------------------------------------------------------------------


class TestMissingFieldNodeWorkflowConfig:
    """Verify missing_field_node uses per-workflow config when present."""

    @pytest.mark.asyncio
    async def test_uses_workflow_field_questions_when_config_present(self) -> None:
        """When WorkflowConfig is registered, its field_questions are used."""
        from app.agents.registries.workflow_config import (
            WorkflowConfig, register_workflow, WORKFLOW_CONFIG_REGISTRY
        )

        custom_questions = {"lease_code": "CUSTOM: What is your lease code?"}
        mock_cfg = WorkflowConfig(
            agent_name="test_questions_agent",
            extraction_prompt="prompt",
            field_questions=custom_questions,
            stage_sync_nodes={},
            collection_stages=frozenset({"CREATE_SR"}),
            confirmation_nodes={"CREATE_SR": "confirmation"},
            terminal_stages=frozenset(),
            # Include backend fields so tenant_profile_id etc. go to backend_missing,
            # triggering the lease_code trigger question (from custom_questions).
            backend_fields=frozenset({
                "tenant_profile_id", "property_id", "brand_id", "lease_id",
                "contract_id", "unit_codes", "city", "contracted_area", "lease_brand_mall",
            }),
            auto_generated_fields=frozenset({"title"}),
            lease_trigger_fields=("lease_code",),
            action_intents=frozenset(),
        )
        try:
            register_workflow(mock_cfg)
            state = _state(
                active_agent="test_questions_agent",
                workflow_stage="CREATE_SR",
                collected_data={},  # All fields missing → lease trigger fires
            )
            result = await missing_field_node(state)
            # Backend fields are missing → lease_code trigger fires
            # → custom_questions["lease_code"] = "CUSTOM: ..." is used
            assert result.get("status") == "WAITING_FOR_USER"
            assert "CUSTOM" in result.get("response_message", ""), (
                f"Expected CUSTOM question, got: {result.get('response_message')!r}"
            )
        finally:
            WORKFLOW_CONFIG_REGISTRY.pop("test_questions_agent", None)

    @pytest.mark.asyncio
    async def test_falls_back_to_handover_questions_when_no_active_agent(self) -> None:
        """active_agent=None → HANDOVER_FIELD_QUESTIONS used without error."""
        state = _state(
            active_agent=None,
            workflow_stage="CREATE_SR",
            collected_data={},
        )
        # Should not raise even with no active_agent
        result = await missing_field_node(state)
        assert "status" in result

    @pytest.mark.asyncio
    async def test_rdd_does_not_reask_actual_handover_when_only_docs_blocking(self) -> None:
        """Document-count errors must not steal the next question from missing fields."""
        state = _state(
            workflow_stage="RDD_REVIEW",
            collected_data={
                "actual_handover_date": "2026-08-20",
                # other RDD required fields still missing
            },
            validation_errors=[
                {
                    "field": "_documents",
                    "validation_type": "document_count",
                    "status": "FAILED",
                    "blocking": True,
                    "message": "A Handover Report must be uploaded before submitting.",
                }
            ],
        )
        result = await missing_field_node(state)
        asked = (result.get("response_ui") or {}).get("field")
        assert asked != "actual_handover_date"
        assert asked in {
            "guideLineLink",
            "document_status",
            "fitout_start_date",
            "fitout_end_date",
        }

    @pytest.mark.asyncio
    async def test_backend_fields_from_config_exclude_correct_fields(self) -> None:
        """auto_generated_fields from WorkflowConfig are excluded from user_missing."""
        from app.agents.registries.workflow_config import (
            WorkflowConfig, register_workflow, WORKFLOW_CONFIG_REGISTRY
        )

        mock_cfg = WorkflowConfig(
            agent_name="test_auto_gen_agent",
            extraction_prompt="prompt",
            field_questions={"permit_field": "What is the permit field?"},
            stage_sync_nodes={},
            collection_stages=frozenset({"CREATE_SR"}),
            confirmation_nodes={"CREATE_SR": "confirmation"},
            terminal_stages=frozenset(),
            backend_fields=frozenset(),
            auto_generated_fields=frozenset({"permit_number", "title"}),
            lease_trigger_fields=(),
            action_intents=frozenset(),
        )
        try:
            register_workflow(mock_cfg)
            # Use CREATE_SR which has "title" in required_fields
            # With this config, "title" should be in auto_generated → not prompted
            state = _state(
                active_agent="test_auto_gen_agent",
                workflow_stage="CREATE_SR",
                collected_data={"lease_code": "LC-001", "lease_id": "LEASE-001"},
            )
            result = await missing_field_node(state)
            # title should not be prompted (auto_generated)
            asked_field = (result.get("response_ui") or {}).get("field")
            assert asked_field != "title", "auto_generated field 'title' should not be asked"
        finally:
            WORKFLOW_CONFIG_REGISTRY.pop("test_auto_gen_agent", None)


# ---------------------------------------------------------------------------
# TestCorrectionPendingField
# ---------------------------------------------------------------------------


class TestCorrectionOnLockedField:
    """B3: fields the merge layer refuses to overwrite must not be offered."""

    @pytest.mark.asyncio
    async def test_lease_derived_field_correction_is_refused(self) -> None:
        state = _state(
            workflow_stage="CREATE_SR",
            collected_data={"lease_id": 7001, "mall": "Hamraa Mall"},
            correction_pending_field="mall",
        )
        result = await missing_field_node(state)

        assert result["correction_pending_field"] is None
        assert result["active_field"] is None
        assert "can't be edited" in result["response_message"]
        # Must not pose a question it cannot honour.
        assert "what would you like to change it to" not in (
            result["response_message"].lower()
        )

    @pytest.mark.asyncio
    async def test_backend_protected_field_correction_is_refused(self) -> None:
        state = _state(
            workflow_stage="CREATE_SR",
            collected_data={"lease_id": 7001, "city": "Riyadh"},
            correction_pending_field="city",
        )
        result = await missing_field_node(state)
        assert "can't be edited" in result["response_message"]

    @pytest.mark.asyncio
    async def test_editable_field_still_offered_when_lease_resolved(self) -> None:
        state = _state(
            workflow_stage="CREATE_SR",
            collected_data={"lease_id": 7001, "title": "Some title"},
            correction_pending_field="title",
        )
        result = await missing_field_node(state)
        assert result["active_field"] == "title"
        assert "Some title" in result["response_message"]


class TestCorrectionPendingField:
    """missing_field_node must honor a pending 'change field X' redirect."""

    @pytest.mark.asyncio
    async def test_asks_for_correction_pending_field(self) -> None:
        state = _state(
            workflow_stage="CREATE_SR",
            collected_data={"title": "handover-t0031259", "description": "old desc"},
            correction_pending_field="title",
        )
        result = await missing_field_node(state)

        assert result["missing_fields"] == ["title"]
        assert result["active_field"] == "title"
        assert result["status"] == "WAITING_FOR_USER"
        assert result["response_ui"]["field"] == "title"
        assert "handover-t0031259" in result["response_message"]

    @pytest.mark.asyncio
    async def test_response_is_marked_verbatim(self) -> None:
        """Regression: without verbatim=True, response_generation_node's LLM
        rewrite sees the field's OLD value still in collected_data and
        re-confirms it instead of asking for a new one — silently discarding
        the correction. The re-ask message must bypass that rewrite."""
        state = _state(
            workflow_stage="CREATE_SR",
            collected_data={"endDate": "2026-08-21T19:00:00"},
            correction_pending_field="endDate",
        )
        result = await missing_field_node(state)

        assert result["response_ui"]["verbatim"] is True
        assert result["response_message"] == result["response_ui"]["message"]

    @pytest.mark.asyncio
    async def test_date_field_correction_includes_format_hint(self) -> None:
        state = _state(
            workflow_stage="CREATE_SR",
            collected_data={"endDate": "2026-08-21T19:00:00"},
            correction_pending_field="endDate",
        )
        result = await missing_field_node(state)

        assert "DD/MM/YYYY" in result["response_message"]
        assert "21/08/2026" in result["response_message"]

    @pytest.mark.asyncio
    async def test_enum_field_correction_includes_chip_decorations(self) -> None:
        state = _state(
            workflow_stage="CREATE_SR",
            collected_data={"inspection_done_by": "FM_MANAGER"},
            correction_pending_field="inspection_done_by",
        )
        result = await missing_field_node(state)

        options = result["response_decorations"]["suggestions"]["options"]
        assert [(o["label"], o["value"]) for o in options] == [
            ("FM Manager", "FM_MANAGER"),
            ("Operations", "OPERATIONS"),
        ]
        # Each chip must also carry the action the entry node keys off, so a
        # correction click writes the value directly instead of going through
        # LLM re-extraction (which re-asks the question just answered).
        assert [o["action"] for o in options] == [
            "select_inspection_done_by_fm_manager",
            "select_inspection_done_by_operations",
        ]

    @pytest.mark.asyncio
    async def test_clears_correction_pending_field_signal(self) -> None:
        """The signal is one-turn only — must be explicitly cleared in the patch."""
        state = _state(
            workflow_stage="CREATE_SR",
            collected_data={"title": "handover-t0031259"},
            correction_pending_field="title",
        )
        result = await missing_field_node(state)

        assert result["correction_pending_field"] is None

    @pytest.mark.asyncio
    async def test_takes_priority_over_other_missing_fields(self) -> None:
        """Even with other required fields missing, the correction is asked first."""
        state = _state(
            workflow_stage="FM_REVIEW",
            collected_data={},
            correction_pending_field="unit_readiness_date",
        )
        result = await missing_field_node(state)

        assert result["active_field"] == "unit_readiness_date"

    @pytest.mark.asyncio
    async def test_no_pending_correction_falls_through_normally(self) -> None:
        state = _state(
            workflow_stage="CREATE_SR",
            collected_data={"title": "handover-t0031259", "description": "old desc"},
        )
        result = await missing_field_node(state)

        assert result.get("active_field") != "title"
