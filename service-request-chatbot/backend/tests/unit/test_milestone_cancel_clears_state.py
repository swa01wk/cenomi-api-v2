"""Unit tests — Cancel on the "milestone already confirmed" prompt.

When a lease already has the milestone date confirmed, lease_lookup_node pauses
with Cancel / Continue chips. Cancel wipes the draft and answers "The handover
request has been cleared." — but the reply never reached the user: the wipe left
``active_field`` pointing at a field of the draft it had just deleted, and
_waiting_short_circuit_target reads a set active_field as "this message is the
answer to that question", routing the turn to field_extraction. The cleared
reply was discarded and missing_field_node re-asked for the lease code, so the
cancel looked like it had done nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.help_agent_graph import _route_after_handover_entry
from app.agents.graph.nodes.handover.handover_entry_node import handover_entry_node


def _milestone_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "action_override": "cancel_milestone",
        "confirmation_status": "MILESTONE_PENDING",
        "user_message": "cancel",
        # Set by the earlier "Please provide the lease code" turn and persisted.
        "active_field": "lease_code",
        "workflow_stage": "CREATE_SR",
        "active_agent": "rdd_handover_agent",
        "collected_data": {"lease_code": "t0033400", "lease_id": 999},
    }
    state.update(overrides)
    return state


class TestMilestoneCancelClearsState:
    @pytest.mark.asyncio
    async def test_draft_is_wiped(self) -> None:
        patch = await handover_entry_node(_milestone_state())  # type: ignore[arg-type]
        assert patch["collected_data"] == {}
        assert patch["selected_lease"] is None
        assert patch["workflow_stage"] is None

    @pytest.mark.asyncio
    async def test_active_field_is_cleared(self) -> None:
        """The pointer must not outlive the draft it points into."""
        patch = await handover_entry_node(_milestone_state())  # type: ignore[arg-type]
        assert patch["active_field"] is None
        assert patch["correction_pending_field"] is None

    @pytest.mark.asyncio
    async def test_turn_is_not_routed_back_into_field_collection(self) -> None:
        """The regression: field_extraction here discards the cleared reply and
        re-asks for the lease code."""
        state = _milestone_state()
        patch = await handover_entry_node(state)  # type: ignore[arg-type]
        assert _route_after_handover_entry({**state, **patch}) == "response_generation"

    @pytest.mark.asyncio
    async def test_cleared_reply_survives(self) -> None:
        patch = await handover_entry_node(_milestone_state())  # type: ignore[arg-type]
        assert "cleared" in patch["response_message"].lower()
        assert patch["response_ui"]["verbatim"] is True
        assert patch["response_ui"]["reset_conversation"] is True

    @pytest.mark.asyncio
    async def test_cancel_typed_as_text_behaves_the_same(self) -> None:
        """Typing "cancel" takes the MILESTONE_PENDING text branch, not the
        action_override branch — it must clear just as thoroughly."""
        state = _milestone_state(action_override=None, user_message="cancel")
        patch = await handover_entry_node(state)  # type: ignore[arg-type]
        assert patch["collected_data"] == {}
        assert patch["active_field"] is None
        assert _route_after_handover_entry({**state, **patch}) == "response_generation"

    @pytest.mark.asyncio
    async def test_continue_still_reaches_merge_state(self) -> None:
        """Continue keeps the lease and revises the date — unchanged."""
        state = _milestone_state(action_override="continue_milestone")
        patch = await handover_entry_node(state)  # type: ignore[arg-type]
        assert patch.get("collected_data", {}) != {}
        assert _route_after_handover_entry({**state, **patch}) == "merge_state"


class TestFitoutMilestoneCancelClearsState:
    @pytest.mark.asyncio
    async def test_fitout_cancel_also_clears_active_field(self) -> None:
        from app.agents.graph.nodes.fitout_confirm.fitout_confirm_entry_node import (
            fitout_confirm_entry_node,
        )

        patch = await fitout_confirm_entry_node(  # type: ignore[arg-type]
            {
                "action_override": "cancel_milestone",
                "confirmation_status": "MILESTONE_PENDING",
                "user_message": "cancel",
                "active_field": "lease_code",
                "workflow_stage": "CREATE_SR",
                "sub_category": "FIT_OUT_CONFIRM_FOS",
                "active_agent": "rdd_fitout_start_agent",
                "collected_data": {"lease_code": "t0033400"},
            }
        )
        assert patch["collected_data"] == {}
        assert patch["active_field"] is None
        assert patch["correction_pending_field"] is None
