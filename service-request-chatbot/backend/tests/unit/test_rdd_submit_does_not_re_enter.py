"""Unit tests — a finished RDD submission cannot be walked into a second time.

Observed after a successful report submit: the success message was immediately
followed by "I'm sorry, but the RDD submission is blocked because the action
wasn't recognised. Please reply with either 'submit' or 'final_approve'",
dangling above the review card and its Final Approve button.

The cause is the CONFIRMED flag outliving the action it authorised.
``_route_after_rdd_confirmation`` sends *any* turn carrying
``confirmation_status == "CONFIRMED"`` to rdd_payload_builder → the submission
node, and the form bridge fires a silent patch turn as soon as the new card
lands. ``rdd_action`` does not survive the turn, so that follow-up arrived with
none and fell into the unrecognised-action branch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.graph.help_agent_graph import _route_after_rdd_confirmation
from app.agents.graph.nodes.handover.rdd_api_submission_node import (
    rdd_api_submission_node,
)

_NODE = "app.agents.graph.nodes.handover.rdd_api_submission_node"


@dataclass
class _Ok:
    """What the platform returns for an accepted submit / GET."""

    error: str | None = None
    sr_id: str = "321026"
    status_code: int = 200
    latency_ms: int = 5
    endpoint: str = "/service-requests"
    success: bool = True
    service_request_operations: list[dict[str, Any]] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


def _state(rdd_action: str | None) -> dict[str, Any]:
    refs: dict[str, Any] = {
        "sr_id": "321026",
        "rdd_payload": {"payload": {"guideLineLink": "http://x"}},
        "user_role": "DD_ENGINEER",
    }
    if rdd_action:
        refs["rdd_action"] = rdd_action
    return {
        "session_id": None,
        "user_id": None,
        "auth_context": None,
        "workflow_stage": "RDD_REVIEW",
        "confirmation_status": "CONFIRMED",
        "collected_data": {"guideLineLink": "http://x", "document_status": "APPROVED"},
        "documents": [],
        "validation_errors": [],
        "backend_refs": refs,
    }


async def _run(rdd_action: str | None) -> dict[str, Any]:
    svc = AsyncMock()
    svc.submit_report.return_value = _Ok()
    svc.get_service_request.return_value = _Ok()
    with patch(f"{_NODE}.get_service_request_api_service", return_value=svc):
        return await rdd_api_submission_node(_state(rdd_action))


class TestAfterASuccessfulSubmit:
    @pytest.mark.asyncio
    async def test_it_reports_success(self) -> None:
        result = await _run("submit")
        assert result["status"] == "SUBMITTED"
        assert "successfully submitted" in result["response_message"]

    @pytest.mark.asyncio
    async def test_the_confirmed_flag_is_retired(self) -> None:
        assert (await _run("submit"))["confirmation_status"] is None

    @pytest.mark.asyncio
    async def test_the_next_turn_no_longer_re_enters_the_submission(self) -> None:
        """The silent form patch that follows the card must not resubmit."""
        result = await _run("submit")
        next_turn = {**_state("submit"), **result}
        assert _route_after_rdd_confirmation(next_turn) == "response_generation"

    @pytest.mark.asyncio
    async def test_the_spent_action_and_payload_are_dropped(self) -> None:
        """Final Approve is a separate action that sets its own."""
        refs = (await _run("submit"))["backend_refs"]
        assert "rdd_action" not in refs
        assert "rdd_payload" not in refs
        assert refs["rdd_status"] == "REPORT_SUBMITTED"


class TestTheUnrecognisedActionBranch:
    @pytest.mark.asyncio
    async def test_it_still_refuses_an_actionless_turn(self) -> None:
        result = await _run(None)
        assert result["status"] == "FAILED"
        assert "unrecognised rdd_action" in result["response_message"]

    @pytest.mark.asyncio
    async def test_but_it_cannot_repeat_itself_turn_after_turn(self) -> None:
        result = await _run(None)
        assert result["confirmation_status"] is None
        assert (
            _route_after_rdd_confirmation({**_state(None), **result})
            == "response_generation"
        )
