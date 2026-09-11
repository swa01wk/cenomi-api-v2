"""Unit tests — asking for a new SR while another SR is open.

A Mall Manager opened an SR they had already submitted, clicked "Review with
AI" and got its preview — correct. Typing "create handover" in the same chat
then showed that preview again (or, on SR 321005 pending FM, "You do not have
permission to perform FM review actions") instead of starting a new request.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.help_agent_graph import _route_after_load, build_help_agent_graph
from app.agents.graph.nodes.shared.new_request_node import (
    asks_for_a_new_service_request,
    new_request_node,
)


def _open_sr_state(message: str, **overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "user_message": message,
        "active_agent": "rdd_handover_agent",
        "workflow_stage": "FM_REVIEW",
        "active_field": None,
        "backend_refs": {"sr_id": "321005", "user_role": "MALL_MANAGER"},
        "collected_data": {"lease_code": "t0111321"},
    }
    state.update(overrides)
    return state


class TestRecognisingANewRequest:
    @pytest.mark.parametrize(
        "message",
        [
            "create hanover",
            "create handoevr",
            "create handover",
            "I want to create a Handover service request",
            "raise a fitout start confirmation",
            "create trading confirmation",
            "initiate handover for t0111321",
            "Create a new SR",
        ],
    )
    def test_an_explicit_create_is_recognised(self, message: str) -> None:
        assert asks_for_a_new_service_request(message) is True

    @pytest.mark.parametrize(
        "message",
        [
            "",
            "   ",
            "yes",
            "approve",
            "t0111321",
            "the actual handover date is 28/09/2026",
            "the new handover date is 20/09/2026",
            "the handover start date is fine",
            "submit the handover report",
            "No additional comments",
            "how do I create a handover?",
            "what do I need to create a handover request",
            "create",
        ],
    )
    def test_ordinary_review_answers_are_not(self, message: str) -> None:
        assert asks_for_a_new_service_request(message) is False


class TestRoutingWithAnOpenSr:
    @pytest.mark.parametrize(
        "stage",
        ["FM_REVIEW", "RDD_REVIEW", "SR_CREATED", "SR_COMPLETED", "HANDED_OVER_COMPLETED"],
    )
    def test_a_new_request_leaves_the_open_sr_at_any_stage(self, stage: str) -> None:
        state = _open_sr_state("create handover", workflow_stage=stage)
        assert _route_after_load(state) == "new_request"

    def test_a_review_answer_still_goes_to_the_open_sr(self) -> None:
        assert _route_after_load(_open_sr_state("yes")) == "sr_status_sync"

    def test_a_free_text_answer_is_never_taken_for_one(self) -> None:
        state = _open_sr_state("please create handover checklist", active_field="comments")
        assert _route_after_load(state) == "sr_status_sync"

    def test_without_an_open_sr_nothing_changes(self) -> None:
        """A fresh create is routed exactly as before — no detour."""
        state = _open_sr_state("create handover", backend_refs={}, active_agent=None)
        assert _route_after_load(state) == "supervisor"


class TestReleasingTheOpenSr:
    @pytest.mark.asyncio
    async def test_the_open_sr_is_dropped(self) -> None:
        patch = await new_request_node(_open_sr_state("create handover"))  # type: ignore[arg-type]
        assert "sr_id" not in patch["backend_refs"]
        assert patch["workflow_stage"] is None
        assert patch["active_agent"] is None
        assert patch["intent"] is None
        assert patch["collected_data"] == {}
        assert patch["documents"] == []

    @pytest.mark.asyncio
    async def test_the_callers_role_is_kept(self) -> None:
        patch = await new_request_node(_open_sr_state("create handover"))  # type: ignore[arg-type]
        assert patch["backend_refs"]["user_role"] == "MALL_MANAGER"

    @pytest.mark.asyncio
    async def test_the_release_is_signalled_to_the_save_step(self) -> None:
        """Without it the draft restores the old SR on the very next turn."""
        patch = await new_request_node(_open_sr_state("create handover"))  # type: ignore[arg-type]
        assert patch["backend_refs"] == {
            "user_role": "MALL_MANAGER",
            "released_sr_id": "321005",
        }

    @pytest.mark.asyncio
    async def test_the_turn_carries_on_to_the_supervisor(self) -> None:
        patch = await new_request_node(_open_sr_state("create handover"))  # type: ignore[arg-type]
        assert patch["status"] == "IN_PROGRESS"


def test_the_graph_still_compiles_with_the_new_node() -> None:
    assert build_help_agent_graph() is not None
