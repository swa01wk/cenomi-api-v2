"""A confirmation the flow refuses must not fire on a later turn.

Reported live as SR 320998. The user set an inspection end date before the
start date, clicked Confirm & Submit, and was correctly refused:

    [09:57:40] user       Yes, please submit
    [09:57:40] assistant  The end date you entered (08/09/2026) is not after
                          the start date (11/09/2026)...
    [09:57:47] user       (empty)          <- silent form sync
    [09:57:49] assistant  Your Handover Service Request has been submitted
                          successfully — reference 320998.

``confirmation_status`` stayed CONFIRMED through the refusal. Seven seconds
later a background form sync carried a valid date, validation passed, and
every entry route sends a standing CONFIRMED straight to merge_state and on to
submission — so the SR was created by a background sync, from a confirmation
that had already been turned down, without the user submitting again.

A refusal now voids the confirmation. The user confirms once more, and that
confirmation is theirs.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.shared.validation_node import validation_node


def _complete_draft(**extra: Any) -> dict[str, Any]:
    """A draft with nothing outstanding but the optional free-text fields."""
    draft: dict[str, Any] = {
        "tenant_profile_id": 1,
        "property_id": 2,
        "lease_code": "t0109364",
        "lease_id": 3,
        "brand_id": 4,
        "mall": "Riyadh Park",
        "brand": "Nike",
        "lease": "t0109364",
        "unit_codes": ["GF101"],
        "city": "Riyadh",
        "contracted_area": 120,
        "already_handovered": "initiate_handover",
        "title": "handover-t0109364",
        "description": "Handover inspection.",
        "startDate": "2026-09-11",
        "endDate": "2026-09-12",
        "inspectionTime": "09:00",
        "inspectionEndTime": "17:00",
        "inspection_done_by": "FM_MANAGER",
    }
    draft.update(extra)
    return draft


async def _validate(draft: dict[str, Any], confirmed: bool) -> dict[str, Any]:
    state: dict[str, Any] = {
        "workflow_stage": "CREATE_SR",
        "active_agent": "rdd_handover_agent",
        "sub_category": "HANDOVER",
        "collected_data": draft,
        "documents": [],
        "confirmation_status": "CONFIRMED" if confirmed else None,
    }
    return await validation_node(state)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_an_end_date_before_the_start_voids_the_confirmation() -> None:
    result = await _validate(_complete_draft(endDate="2026-09-08"), confirmed=True)

    assert result["status"] == "IN_PROGRESS"
    assert "confirmation_status" in result, "the refused confirmation was left standing"
    assert result["confirmation_status"] is None


@pytest.mark.asyncio
async def test_a_valid_confirmed_draft_keeps_its_confirmation() -> None:
    # Only the optional free-text fields are outstanding, which is exactly the
    # case _route_after_validation exempts for a confirmed draft. Voiding here
    # would strand the user on a card that refuses to submit.
    result = await _validate(_complete_draft(), confirmed=True)

    assert "confirmation_status" not in result, (
        "a confirmation must survive when nothing actually refuses it"
    )


@pytest.mark.asyncio
async def test_an_unconfirmed_draft_is_untouched() -> None:
    result = await _validate(_complete_draft(endDate="2026-09-08"), confirmed=False)

    assert result["status"] == "IN_PROGRESS"
    assert "confirmation_status" not in result


@pytest.mark.asyncio
async def test_a_real_missing_field_also_voids_it() -> None:
    # Not just date ordering — anything the flow would refuse to submit on.
    result = await _validate(_complete_draft(inspection_done_by=""), confirmed=True)

    assert result["confirmation_status"] is None
