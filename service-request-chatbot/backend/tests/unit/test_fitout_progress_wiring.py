"""Registration and routing wiring for the Fitout Progress agent (phase 2).

A node that works in isolation is invisible until every table knows about it,
and each of these tables is a place the agent could silently go missing.

Coverage
--------
TestAgentRegistry     — (category, sub_category) → agent
TestWorkflowConfig    — the contract the shared pipeline nodes read
TestIntents           — supervisor enum, derived intent sets, RBAC
TestGraph             — nodes, entry dispatch, validation routing
TestLeasePurpose      — the platform rule the lookup and the picker carry
TestTheOneQuestion    — missing_field_node asks it verbatim, with chips
TestStatusSync        — opening an existing SR: stage, hydration, routing
TestGuidedTourChoice  — the guided/open choice, offered here like everywhere else
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.graph.help_agent_graph import (
    _AGENT_ENTRY_NODES,
    _CREATE_NEW_SR_INTENTS,
    _route_after_sync,
    _route_after_validation,
    build_help_agent_graph,
)
from app.agents.graph.nodes.shared.sr_status_sync_node import _map_operations_to_stage
from app.agents.services.review_decorations_service import payload_to_collected_data
from app.agents.graph.nodes.shared.lease_lookup_node import lease_lookup_node  # noqa: F401
from app.agents.graph.nodes.shared.missing_field_node import missing_field_node
from app.agents.registries.workflow_config import get_workflow_config
from app.agents.registry import SERVICE_REQUEST_AGENT_REGISTRY
from app.agents.schemas.help_agent_schema import ALL_INTENTS, ROLE_PERMITTED_INTENTS
from app.agents.schemas.supervisor_schema import SupervisorDecision
from app.agents.services.resource_picker_decorations import (
    LEASE_PICKER_PURPOSE_BY_AGENT,
    lease_picker_purpose,
    should_browse_leases,
)
from app.observability.analytics_constants import CREATE_SR_INTENTS

AGENT = "rdd_fitout_progress_agent"
CREATE_INTENT = "CREATE_FITOUT_PROGRESS"
APPROVE_INTENT = "APPROVE_FITOUT_PROGRESS"

_LEASE: dict[str, Any] = {
    "lease_brand_mall": "t0105712 - Brand Under Armour - Jawharat Jeddah",
    "lease_code": "t0105712",
    "lease_id": 95404,
    "tenant_profile_id": 116,
    "actual_fitout_start_date": "2026-07-15",
    "base_fitout_end_date": "2026-10-15",
}


class TestAgentRegistry:
    def test_sub_category_resolves_to_the_agent(self) -> None:
        entry = SERVICE_REQUEST_AGENT_REGISTRY["FIT_OUT_AND_HANDOVER"]["FITOUT_PROGRESS"]
        assert entry["agent_name"] == AGENT
        assert entry["schema_key"] == "fitout_progress_schema"


class TestWorkflowConfig:
    def test_registered(self) -> None:
        assert get_workflow_config(AGENT) is not None

    def test_nodes_and_stages(self) -> None:
        cfg = get_workflow_config(AGENT)
        assert cfg is not None
        assert cfg.collection_stages == frozenset({"CREATE_SR", "APPROVE_SR"})
        assert cfg.confirmation_nodes == {
            "CREATE_SR": "fitout_progress_confirmation",
            "APPROVE_SR": "fitout_progress_confirmation",
        }
        assert cfg.stage_sync_nodes == {"APPROVE_SR": "fitout_progress_entry"}
        assert cfg.terminal_stages == frozenset(
            {"SR_CREATED", "SR_APPROVED", "SR_REJECTED"}
        )

    def test_no_cancelled_terminal_stage(self) -> None:
        # The platform workflow has no cancel action for this sub-category.
        cfg = get_workflow_config(AGENT)
        assert cfg is not None
        assert "CANCELLED" not in cfg.terminal_stages

    def test_guided_tour_is_on_like_every_other_workflow(self) -> None:
        # The form pre-fills the percentage with its own computed default, so
        # "leave the form open and type straight into it" is a real choice here
        # — and the answer decides whether that field is locked while the
        # assistant asks.
        cfg = get_workflow_config(AGENT)
        assert cfg is not None
        assert cfg.guided_tour_gate is True

    def test_title_is_auto_generated_never_asked(self) -> None:
        cfg = get_workflow_config(AGENT)
        assert cfg is not None
        assert "title" in cfg.auto_generated_fields

    def test_session_flag_is_never_asked_for(self) -> None:
        cfg = get_workflow_config(AGENT)
        assert cfg is not None
        assert "_progress_confirmed" in cfg.backend_fields


class TestIntents:
    def test_supervisor_accepts_both_intents(self) -> None:
        for intent in (CREATE_INTENT, APPROVE_INTENT):
            decision = SupervisorDecision(
                intent=intent, confidence=0.9, reasoning="test"
            )
            assert decision.intent == intent

    def test_derived_intent_sets(self) -> None:
        assert {CREATE_INTENT, APPROVE_INTENT} <= ALL_INTENTS
        assert CREATE_INTENT in _CREATE_NEW_SR_INTENTS
        assert CREATE_INTENT in CREATE_SR_INTENTS

    def test_dd_engineer_may_create_and_approve(self) -> None:
        permitted = ROLE_PERMITTED_INTENTS["DD_ENGINEER"]
        assert {CREATE_INTENT, APPROVE_INTENT} <= permitted

    def test_other_roles_may_not(self) -> None:
        for role in ("MALL_MANAGER", "OPERATIONS", "FM_MANAGER"):
            permitted = ROLE_PERMITTED_INTENTS[role]
            assert CREATE_INTENT not in permitted
            assert APPROVE_INTENT not in permitted

    def test_admin_may(self) -> None:
        assert CREATE_INTENT in ROLE_PERMITTED_INTENTS["ADMIN"]


class TestGraph:
    def test_nodes_exist(self) -> None:
        graph = build_help_agent_graph()
        for node in (
            "fitout_progress_entry",
            "fitout_progress_confirmation",
            "fitout_progress_payload_builder",
            "fitout_progress_api_submission",
        ):
            assert node in graph.nodes

    def test_entry_dispatch(self) -> None:
        assert _AGENT_ENTRY_NODES[AGENT] == "fitout_progress_entry"

    def test_validation_routes_to_the_question_while_incomplete(self) -> None:
        state = {
            "active_agent": AGENT,
            "workflow_stage": "CREATE_SR",
            "collected_data": dict(_LEASE),
            "validation_errors": [
                {
                    "field": "fitout_progress_percentage",
                    "validation_type": "required",
                    "blocking": True,
                }
            ],
        }
        assert _route_after_validation(state) == "missing_field"

    def test_validation_routes_to_the_card_when_answered(self) -> None:
        state = {
            "active_agent": AGENT,
            "workflow_stage": "CREATE_SR",
            "collected_data": {
                **_LEASE,
                "fitout_progress_percentage": "82",
                "_progress_confirmed": True,
            },
            "validation_errors": [],
        }
        assert _route_after_validation(state) == "fitout_progress_confirmation"

    def test_a_regression_warning_does_not_block_the_card(self) -> None:
        # Progress going backwards is flagged, not refused — only the user knows
        # whether it is a typo or a correction.
        state = {
            "active_agent": AGENT,
            "workflow_stage": "CREATE_SR",
            "collected_data": {
                **_LEASE,
                "fitout_progress_percentage": "30",
                "_progress_confirmed": True,
                "fitout_progress": "90",
            },
            "validation_errors": [
                {
                    "field": "fitout_progress_percentage",
                    "validation_type": "percentage_regression",
                    "blocking": False,
                }
            ],
        }
        assert _route_after_validation(state) == "fitout_progress_confirmation"


class TestLeasePurpose:
    def test_picker_purpose(self) -> None:
        assert LEASE_PICKER_PURPOSE_BY_AGENT[AGENT] == "fitout_progress"
        assert lease_picker_purpose({"active_agent": AGENT}) == "fitout_progress"

    def test_lookup_sends_the_same_purpose(self) -> None:
        # The lookup node builds its own map; the two must agree or the picker
        # and the code search would apply different eligibility rules.
        import inspect

        from app.agents.graph.nodes.shared import lease_lookup_node as module

        source = inspect.getsource(module)
        assert '"rdd_fitout_progress_agent": "fitout_progress"' in source

    def test_create_turn_offers_the_lease_list(self) -> None:
        assert should_browse_leases(
            {"active_agent": AGENT, "workflow_stage": "CREATE_SR", "collected_data": {}}
        )

    def test_no_list_once_the_lease_is_resolved(self) -> None:
        assert not should_browse_leases(
            {
                "active_agent": AGENT,
                "workflow_stage": "CREATE_SR",
                "collected_data": {"lease_id": 95404},
            }
        )


class TestTheOneQuestion:
    def _state(self, **overrides: Any) -> dict[str, Any]:
        state: dict[str, Any] = {
            "active_agent": AGENT,
            "sub_category": "FITOUT_PROGRESS",
            "workflow_stage": "CREATE_SR",
            "collected_data": dict(_LEASE),
            "validation_errors": [],
            "documents": [],
            "auth_context": SimpleNamespace(roles=["DD_ENGINEER"]),
        }
        state.update(overrides)
        return state

    async def test_asks_the_percentage_and_nothing_else(self) -> None:
        out = await missing_field_node(self._state())
        assert out["response_ui"]["field"] == "fitout_progress_percentage"
        assert out["active_field"] == "fitout_progress_percentage"
        assert out["missing_fields"] == ["fitout_progress_percentage"]

    async def test_question_carries_the_window_and_the_suggestion(self) -> None:
        out = await missing_field_node(self._state())
        question = out["response_message"]
        assert "15 Jul 2026" in question
        assert "15 Oct 2026" in question
        assert "How complete is the fit-out today?" in question
        # Verbatim: the chips carry the answers, and an LLM rewrite can drop the
        # question entirely, leaving chips under nothing.
        assert out["response_ui"]["verbatim"] is True

    async def test_chips_offer_the_suggestion_and_a_ladder(self) -> None:
        out = await missing_field_node(self._state())
        suggestions = out["response_decorations"]["suggestions"]
        assert suggestions["kind"] == "labeled_options"
        values = [o["value"] for o in suggestions["options"]]
        assert "100" in values
        assert any("suggested" in o["label"] for o in suggestions["options"])
        # Value-only: no chip carries an action, so a tap reads as the user's
        # own answer and needs no new frontend action code.
        assert all("action" not in o for o in suggestions["options"])

    async def test_no_window_still_offers_the_ladder(self) -> None:
        collected = {
            k: v
            for k, v in _LEASE.items()
            if k not in ("actual_fitout_start_date", "base_fitout_end_date")
        }
        out = await missing_field_node(self._state(collected_data=collected))
        values = [
            o["value"] for o in out["response_decorations"]["suggestions"]["options"]
        ]
        assert values == ["25", "50", "75", "100"]
        assert "between 0 and 100" in out["response_message"]

    async def test_approve_stage_question_names_the_sr(self) -> None:
        out = await missing_field_node(
            self._state(
                workflow_stage="APPROVE_SR",
                collected_data={**_LEASE, "fitout_progress_percentage": "65"},
                backend_refs={"sr_id": "8517"},
            )
        )
        assert "SR 8517" in out["response_message"]
        assert "Approve 65%" in out["response_message"]

    async def test_asks_for_the_lease_before_the_percentage(self) -> None:
        out = await missing_field_node(self._state(collected_data={}))
        assert out["response_ui"]["field"] != "fitout_progress_percentage"


class TestStatusSync:
    """Opening an existing SR: stage from the platform, agent named explicitly."""

    def _ops(self, level_one_status: str) -> list[dict[str, Any]]:
        return [
            {"workflow_level": 0, "assigned_role": "DD_ENGINEER", "status": "SUBMITTED"},
            {
                "workflow_level": 1,
                "assigned_role": "DD_ENGINEER",
                "status": level_one_status,
            },
        ]

    def test_an_open_level_one_is_the_approve_stage(self) -> None:
        assert (
            _map_operations_to_stage(
                self._ops("IN_PROGRESS"),
                None,
                sub_category="FITOUT_PROGRESS",
                platform_status="IN_PROCESS",
                category="FIT_OUT_AND_HANDOVER",
            )
            == "APPROVE_SR"
        )

    def test_approved_is_terminal(self) -> None:
        assert (
            _map_operations_to_stage(
                self._ops("APPROVED"),
                "APPROVE_SR",
                sub_category="FITOUT_PROGRESS",
                platform_status="APPROVED",
                category="FIT_OUT_AND_HANDOVER",
            )
            == "SR_APPROVED"
        )

    def test_rejected_is_its_own_terminal_stage(self) -> None:
        # Unlike the confirmations, REJECT is in this workflow's allowed_actions,
        # so "terminal" cannot simply mean approved.
        assert (
            _map_operations_to_stage(
                self._ops("REJECTED"),
                "APPROVE_SR",
                sub_category="FITOUT_PROGRESS",
                platform_status="REJECTED",
                category="FIT_OUT_AND_HANDOVER",
            )
            == "SR_REJECTED"
        )

    def test_no_operations_yet_keeps_the_current_stage(self) -> None:
        assert (
            _map_operations_to_stage(
                [],
                "CREATE_SR",
                sub_category="FITOUT_PROGRESS",
                platform_status="CREATED",
                category="FIT_OUT_AND_HANDOVER",
            )
            == "CREATE_SR"
        )

    def test_the_confirmations_are_unaffected(self) -> None:
        # Their "terminal means approved" mapping must not pick up a
        # SR_REJECTED it has no stage for.
        assert (
            _map_operations_to_stage(
                self._ops("REJECTED"),
                "APPROVE_SR",
                sub_category="FIT_OUT_CONFIRM_FOS",
                platform_status="REJECTED",
                category="FIT_OUT_AND_HANDOVER",
            )
            == "APPROVE_SR"
        )

    def test_stored_percentage_survives_payload_hydration(self) -> None:
        # Dropped by the allow-list mapper, the approver was told their own SR
        # had no percentage recorded and got no "Approve 65%" to tap.
        hydrated = payload_to_collected_data(
            {
                "lease_code": "t0105712",
                "fitout_start_date": "2026-07-15",
                "fitout_end_date": "2026-10-15",
                "fitout_progress_percentage": "65",
                "comment": "Revised after site visit",
            }
        )
        assert hydrated["fitout_progress_percentage"] == "65"
        assert hydrated["comment"] == "Revised after site visit"

    def test_approve_stage_routes_to_this_agent_entry_node(self) -> None:
        cfg = get_workflow_config(AGENT)
        assert cfg is not None
        state = {"active_agent": AGENT, "workflow_stage": "APPROVE_SR"}
        assert _route_after_sync(state) == "fitout_progress_entry"

    def test_terminal_stages_are_routable(self) -> None:
        # SR_REJECTED is a stage only this workflow has; an unroutable terminal
        # stage sends the turn to the supervisor and loses the SR card.
        for stage in ("SR_APPROVED", "SR_REJECTED"):
            state = {
                "active_agent": AGENT,
                "workflow_stage": stage,
                "backend_refs": {"sr_id": "8517"},
            }
            assert _route_after_sync(state) == "preview"


class TestGuidedTourChoice:
    """The choice is offered here too, and it precedes the one question.

    The suite disables ``guided_tour_choice_enabled`` globally (tests/conftest),
    so these turn it back on — otherwise the gate can never fire and a workflow
    that silently opted out would still look correct.
    """

    @pytest.fixture()
    def tour_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "guided_tour_choice_enabled", True)

    async def test_the_choice_comes_before_the_percentage(
        self, tour_on: None
    ) -> None:
        out = await missing_field_node(
            {
                "active_agent": AGENT,
                "sub_category": "FITOUT_PROGRESS",
                "workflow_stage": "CREATE_SR",
                "collected_data": dict(_LEASE),
                "validation_errors": [],
                "documents": [],
                "auth_context": SimpleNamespace(roles=["DD_ENGINEER"]),
            }
        )
        assert "guide you through the form" in (out.get("response_message") or "")
        # The percentage is still what is outstanding — the choice is about how
        # it gets answered, not whether.
        assert out["missing_fields"] == ["fitout_progress_percentage"]

    async def test_the_percentage_follows_once_it_is_answered(
        self, tour_on: None
    ) -> None:
        out = await missing_field_node(
            {
                "active_agent": AGENT,
                "sub_category": "FITOUT_PROGRESS",
                "workflow_stage": "CREATE_SR",
                "collected_data": {**_LEASE, "guided_tour": "guided"},
                "validation_errors": [],
                "documents": [],
                "auth_context": SimpleNamespace(roles=["DD_ENGINEER"]),
            }
        )
        assert out["response_ui"]["field"] == "fitout_progress_percentage"
        assert "How complete is the fit-out today?" in out["response_message"]
        options = out["response_decorations"]["suggestions"]["options"]
        assert any("suggested" in o["label"] for o in options)


class TestExtractionPromptContract:
    """The provider's own requirement, not a style preference."""

    def test_the_prompt_asks_for_json(self) -> None:
        # The extraction call sets response_format=json_object, and the API
        # answers 400 unless the word "json" appears in the messages:
        #   "'messages' must contain the word 'json' in some form"
        # Without it every extraction call for this agent failed and the turn
        # collected nothing, so the assistant re-asked for what it had.
        from app.agents.prompts.fitout_progress_extraction_prompt import (
            FITOUT_PROGRESS_EXTRACTION_SYSTEM_PROMPT,
        )

        assert "json" in FITOUT_PROGRESS_EXTRACTION_SYSTEM_PROMPT.lower()

    def test_every_agent_that_uses_the_extractor_asks_for_json(self) -> None:
        """One guard for all of them, so the next agent cannot ship without it.

        ``sales_data_agent`` is excluded on purpose: its nodes never call the
        shared ``FieldExtractionService`` — none of them reads
        ``extraction_prompt`` — so its registered prompt is inert and the
        provider's rule cannot bite it.
        """
        from app.agents.registries.workflow_config import (
            WORKFLOW_CONFIG_REGISTRY,
            get_workflow_config,
        )

        get_workflow_config("rdd_handover_agent")
        for name, cfg in WORKFLOW_CONFIG_REGISTRY.items():
            if name == "sales_data_agent":
                continue
            assert "json" in cfg.extraction_prompt.lower(), name
