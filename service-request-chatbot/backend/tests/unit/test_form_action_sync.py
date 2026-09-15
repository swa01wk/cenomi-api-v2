"""Unit tests — a step done on the SR form is reported by the chat, never repeated.

Observed: an SR created from the main form while the chat was collecting the
same request; pressing Confirm & Submit in the chat then created a duplicate.
The same held for approve / ask for more info / reassign on the review.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.help_agent_graph import _route_after_load, build_help_agent_graph
from app.agents.graph.nodes.shared.form_action_sync_node import (
    form_action_sync_node,
    is_form_action,
    parse_form_action,
)


def _state(action: str, sr_id: str | None = "321040", **extra: Any) -> dict[str, Any]:
    refs: dict[str, Any] = {"sr_id": sr_id} if sr_id else {}
    return {
        "session_id": "sess-1",
        "user_id": "user-1",
        "user_message": "",
        "action_override": action,
        "backend_refs": refs,
        "workflow_stage": "CREATE_SR",
        "confirmation_status": "PENDING",
        **extra,
    }


class TestRouting:
    def test_a_form_action_goes_straight_to_the_sync_node(self) -> None:
        assert _route_after_load(_state("form_action_done:created:HANDOVER")) == "form_action_sync"

    def test_it_outranks_the_open_sr(self) -> None:
        # With an sr_id the router would otherwise sync the SR and re-enter review.
        state = _state("form_action_done:approved", workflow_stage="FM_REVIEW")
        assert _route_after_load(state) == "form_action_sync"

    def test_other_actions_route_as_before(self) -> None:
        assert _route_after_load(_state("", sr_id=None)) != "form_action_sync"

    def test_the_graph_compiles_with_the_new_node(self) -> None:
        assert build_help_agent_graph() is not None


class TestParsing:
    def test_kind_and_sub_category(self) -> None:
        assert parse_form_action("form_action_done:created:FIT_OUT_CONFIRM_FOS") == (
            "created",
            "FIT_OUT_CONFIRM_FOS",
        )
        assert parse_form_action("form_action_done:approved") == ("approved", "")
        assert is_form_action("form_action_done:x") and not is_form_action("confirm_sr")


class TestCreates:
    @pytest.mark.asyncio
    async def test_handover_created_on_the_form_ends_the_create(self) -> None:
        result = await form_action_sync_node(_state("form_action_done:created:HANDOVER"))  # type: ignore[arg-type]
        assert result["workflow_stage"] == "SR_CREATED"
        assert result["confirmation_status"] is None
        assert result["response_ui"]["workflow_complete"] is True
        assert result["response_message"] == (
            "Your Handover Service Request has been submitted — reference **321040**."
        )

    @pytest.mark.asyncio
    async def test_fitout_confirmation_uses_the_agents_label(self) -> None:
        result = await form_action_sync_node(  # type: ignore[arg-type]
            _state("form_action_done:created:FIT_OUT_CONFIRM_FOS")
        )
        assert "Fitout Start Confirmation Service Request" in result["response_message"]

    @pytest.mark.asyncio
    async def test_handed_over_ends_in_its_own_terminal_stage(self) -> None:
        result = await form_action_sync_node(_state("form_action_done:created:HANDED_OVER"))  # type: ignore[arg-type]
        assert result["workflow_stage"] == "HANDED_OVER_COMPLETED"
        assert "Already Trading SR" in result["response_message"]


class TestReviewActions:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("kind", "outcome"),
        [
            ("approved", "was approved from the form"),
            ("more_info", "is now back with the initiator"),
            ("rejected", "was rejected from the form"),
            ("reassigned", "was reassigned from the form"),
            ("report_submitted", "RDD Handover Report for Service Request"),
        ],
    )
    async def test_the_form_action_is_reported_and_confirmation_retired(
        self, kind: str, outcome: str
    ) -> None:
        result = await form_action_sync_node(  # type: ignore[arg-type]
            _state(f"form_action_done:{kind}", workflow_stage="FM_REVIEW")
        )
        assert outcome in result["response_message"]
        assert "**321040**" in result["response_message"]
        assert result["confirmation_status"] is None
        assert "workflow_stage" not in result  # the next turn re-syncs from the platform
        assert result["response_decorations"] == {"promote_preview": True}

    @pytest.mark.asyncio
    async def test_asking_for_more_info_says_where_the_sr_went(self) -> None:
        result = await form_action_sync_node(  # type: ignore[arg-type]
            _state("form_action_done:more_info", workflow_stage="FM_REVIEW")
        )
        assert result["response_message"] == (
            "You've asked for more information — Service Request **321040** "
            "is now back with the initiator."
        )

    @pytest.mark.asyncio
    async def test_no_sr_id_claims_nothing(self) -> None:
        result = await form_action_sync_node(_state("form_action_done:approved", sr_id=None))  # type: ignore[arg-type]
        assert result["response_message"] == ""
