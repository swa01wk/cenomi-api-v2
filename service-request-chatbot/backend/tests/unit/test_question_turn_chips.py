"""A question turn must not inherit the stage's action chip.

``sr_status_sync_node`` runs on every turn of an open SR and merges the stage
action chip into ``response_decorations`` — "Save Progress" while an FM review
is incomplete, "Approve Review" once it is ready. ``missing_field_node`` only
sets its own chips for the handful of enum fields that have them, so any other
question inherited that chip and rendered it directly underneath itself:

    Please share the unit readiness date for lease t0033367.
    [ Save Progress ]

It reads as the answer to the question, and clicking it abandons the question
to save a half-filled review. The chips are dropped on a question turn; the
rest of the decorations describe the SR rather than the question and stay.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.shared.missing_field_node import missing_field_node

_SAVE_PROGRESS = {
    "kind": "labeled_options",
    "options": [
        {
            "label": "Save Progress",
            "value": "save_fm_progress",
            "action": "save_fm_progress",
        }
    ],
}


def _fm_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "workflow_stage": "FM_REVIEW",
        "active_agent": "rdd_handover_agent",
        "sub_category": "HANDOVER",
        "collected_data": {
            "lease_code": "t0033367",
            "lease_brand_mall": "t0033367 - Brand - Mall",
            # Answered, so the guided-tour gate does not take this turn.
            "guided_tour": "open",
        },
        "documents": [],
        "validation_errors": [],
        "backend_refs": {"sr_id": "341517", "user_role": "FM_MANAGER"},
        "response_decorations": {
            "promote_preview": True,
            "suggestions": _SAVE_PROGRESS,
        },
    }
    state.update(overrides)
    return state


def _effective(result: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """What the client ends up with: the node's decorations, else the inherited."""
    return result.get("response_decorations", state["response_decorations"]) or {}


@pytest.mark.asyncio
async def test_the_unit_readiness_question_carries_no_save_progress_chip() -> None:
    state = _fm_state()
    result = await missing_field_node(state)  # type: ignore[arg-type]

    assert (result.get("response_ui") or {}).get("field") == "unit_readiness_date"
    assert "suggestions" not in _effective(result, state)


@pytest.mark.asyncio
async def test_the_rest_of_the_decorations_survive() -> None:
    # promote_preview and the document card describe the SR, not the question.
    state = _fm_state(
        response_decorations={
            "promote_preview": True,
            "documents_required": {"stage": "FM_REVIEW"},
            "suggestions": _SAVE_PROGRESS,
        }
    )
    result = await missing_field_node(state)  # type: ignore[arg-type]

    deco = _effective(result, state)
    assert deco.get("promote_preview") is True
    assert deco.get("documents_required") == {"stage": "FM_REVIEW"}


@pytest.mark.asyncio
async def test_a_question_with_its_own_chips_keeps_them() -> None:
    # The handover-type question owns its chips; stripping must not reach them.
    state = _fm_state(
        workflow_stage="CREATE_SR",
        collected_data={"lease_code": "t0033367", "guided_tour": "open"},
        backend_refs={},
    )
    result = await missing_field_node(state)  # type: ignore[arg-type]

    if (result.get("response_ui") or {}).get("field") == "already_handovered":
        options = result["response_decorations"]["suggestions"]["options"]
        assert {o["value"] for o in options} == {
            "already_trading",
            "initiate_handover",
        }


@pytest.mark.asyncio
async def test_a_turn_that_inherited_no_chips_is_left_alone() -> None:
    # Nothing to strip: the node must not invent an empty decorations dict and
    # overwrite what the rest of the turn built.
    state = _fm_state(response_decorations={"promote_preview": True})
    result = await missing_field_node(state)  # type: ignore[arg-type]

    assert "response_decorations" not in result
