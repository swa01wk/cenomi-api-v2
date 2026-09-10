"""A form patch after submission must not start a new SR.

Observed on SR 320996. The Mall Manager submitted at 13:00:45 —

    Done — your Handover Service Request has been submitted successfully.
    Your reference number is 320996.

— and 11 seconds later an empty turn arrived and the assistant replied
"Please share the lease code for this handover request." The persisted draft
had fallen back to ``workflow_stage = CREATE_SR`` with an empty
``collected_data``.

That empty turn is the form bridge's silent patch. ``intent`` survives from the
turn that created the SR, so the patch matched the "user wants another SR"
branch in handover_entry and wiped the finished draft. On the client that
turned ``isComplete`` false, so the success block — and with it the
"View Service Request" button that soft-navigates to the SR page — disappeared
a second after appearing. The report was "soft nav is not working"; the button
was gone before it could be clicked.

Starting another SR still works: it needs a user who actually asks.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.handover.handover_entry_node import handover_entry_node

_TERMINAL_STAGES = (
    "SR_CREATED",
    "SR_COMPLETED",
    "CANCELLED",
    "HANDED_OVER_COMPLETED",
)


def _submitted(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "intent": "CREATE_RDD_SERVICE_REQUEST",
        "active_agent": "rdd_handover_agent",
        "sub_category": "HANDOVER",
        "workflow_stage": "SR_CREATED",
        "collected_data": {"lease_code": "t0110657", "startDate": "2026-09-10"},
        "backend_refs": {"sr_id": "320996"},
        "documents": [],
        "validation_errors": [],
    }
    state.update(overrides)
    return state


def _wiped(result: dict[str, Any]) -> bool:
    return result.get("collected_data") == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", _TERMINAL_STAGES)
async def test_a_silent_form_patch_leaves_the_finished_draft_alone(
    stage: str,
) -> None:
    result = await handover_entry_node(  # type: ignore[arg-type]
        _submitted(
            workflow_stage=stage,
            user_message="",
            corrected_fields={"title": "handover-t0110657-inspection"},
        )
    )

    assert not _wiped(result), f"{stage}: the draft was reset by a form patch"
    assert result.get("workflow_stage") != "CREATE_SR", stage


@pytest.mark.asyncio
async def test_path_b_is_covered_too() -> None:
    # Already Trading reaches HANDED_OVER_COMPLETED under its own intent.
    result = await handover_entry_node(  # type: ignore[arg-type]
        _submitted(
            intent="CREATE_HANDED_OVER_SR",
            sub_category="HANDED_OVER",
            workflow_stage="HANDED_OVER_COMPLETED",
            user_message="",
            corrected_fields={"comments": "noted"},
        )
    )

    assert not _wiped(result)
    assert result.get("workflow_stage") != "CREATE_HANDED_OVER_SR"


@pytest.mark.asyncio
async def test_asking_for_another_sr_still_clears_the_finished_one() -> None:
    # The branch exists for this, and must keep working.
    result = await handover_entry_node(  # type: ignore[arg-type]
        _submitted(user_message="create another handover", corrected_fields=None)
    )

    assert _wiped(result)
    assert result["workflow_stage"] == "CREATE_SR"
    assert result["backend_refs"] == {}


@pytest.mark.asyncio
async def test_a_patch_that_arrives_with_a_typed_message_is_not_silent() -> None:
    # Only a patch with nothing typed is the bridge acting on its own; a message
    # alongside it is the user speaking, and the supervisor classifies it.
    result = await handover_entry_node(  # type: ignore[arg-type]
        _submitted(
            user_message="create another handover",
            corrected_fields={"title": "x"},
        )
    )

    assert _wiped(result)
