"""Choosing No on the card must retire the actual date and offer the planned ones.

Reported on a Fitout End Confirmation for lease t0112177. The user set
"Fitout Complete Done?" to No on the review card and the card went on showing
**Actual Fitout Complete Date 29/06/2026** — a field the No branch does not
collect — while never offering the revised planned dates it now needs.

Two independent causes.

1. ``get_missing_fields`` reported the radio missing however it was answered,
   because it also required ``_approve_radio_confirmed``, and merge_state set
   that only for an answer typed in chat. A choice made on the form or the card
   arrives as ``corrected_fields``, so the flag never flipped and the assistant
   asked the same yes/no question forever — the "dates are taken but the radio
   is not" report. Pre-fill still must not count, and does not: hydrating an
   open SR goes through lease lookup, which resets the flag.

2. The card is only built when the draft is complete, so nothing redrew it
   mid-collection. The turn now carries the rebuilt card as ``refresh_card``.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.agents.graph.nodes.shared.merge_state_node import merge_state_node
from app.agents.graph.nodes.shared.missing_field_node import missing_field_node
from app.agents.schemas.fitout_confirm_schema import get_missing_fields


def _state() -> dict[str, Any]:
    return copy.deepcopy(
        {
            "workflow_stage": "APPROVE_SR",
            "active_agent": "rdd_fitout_end_agent",
            "sub_category": "FIT_OUT_CONFIRM_END",
            "confirmation_status": "PENDING",
            "collected_data": {
                "lease_code": "t0112177",
                "lease_brand_mall": "t0112177-Brand Cazeit -Salaam Mall",
                "guided_tour": "open",
                "fitout_complete_done": "yes",
                "actual_fitout_complete_date": "2026-06-29",
            },
            "documents": [],
            "validation_errors": [],
            "extracted_fields": {},
        }
    )


class TestAChoiceOnTheCardCounts:
    @pytest.mark.asyncio
    async def test_picking_no_moves_the_flow_to_the_planned_dates(self) -> None:
        state = _state()
        state["user_message"] = ""
        state["corrected_fields"] = {"fitout_complete_done": "no"}

        result = await merge_state_node(state)  # type: ignore[arg-type]
        collected = result["collected_data"]

        assert collected["_approve_radio_confirmed"] is True
        assert collected["fitout_complete_done"] == "no"
        # The actual date belongs to the other branch and is retired.
        assert collected["actual_fitout_complete_date"] == ""
        assert get_missing_fields(
            "APPROVE_SR", "FIT_OUT_CONFIRM_END", collected
        ) == [
            "planned_fitout_complete_date",
            "planned_trading_date",
            "delay_reason",
        ]

    @pytest.mark.asyncio
    async def test_picking_yes_asks_for_the_actual_date(self) -> None:
        state = _state()
        state["collected_data"]["fitout_complete_done"] = "no"
        state["user_message"] = ""
        state["corrected_fields"] = {"fitout_complete_done": "yes"}

        result = await merge_state_node(state)  # type: ignore[arg-type]
        collected = result["collected_data"]

        assert collected["_approve_radio_confirmed"] is True
        assert get_missing_fields(
            "APPROVE_SR", "FIT_OUT_CONFIRM_END", collected
        ) == ["actual_fitout_complete_date"]

    @pytest.mark.asyncio
    async def test_editing_another_row_does_not_answer_the_radio(self) -> None:
        # The guard this replaces existed to stop a pre-filled form value
        # standing in for the user's answer. Only the radio itself answers it.
        state = _state()
        state["user_message"] = ""
        state["corrected_fields"] = {"delay_reason": "Awaiting approval"}

        result = await merge_state_node(state)  # type: ignore[arg-type]
        collected = result["collected_data"]

        assert not collected.get("_approve_radio_confirmed")
        assert "fitout_complete_done" in get_missing_fields(
            "APPROVE_SR", "FIT_OUT_CONFIRM_END", collected
        )


class TestTheCardIsRedrawnForTheNewBranch:
    @pytest.mark.asyncio
    async def test_the_no_branch_card_offers_the_planned_dates(self) -> None:
        state = _state()
        state["collected_data"]["fitout_complete_done"] = "no"
        state["collected_data"]["_approve_radio_confirmed"] = True
        state["collected_data"]["actual_fitout_complete_date"] = ""

        result = await missing_field_node(state)  # type: ignore[arg-type]
        card = (result.get("response_ui") or {}).get("refresh_card")

        assert card is not None, "no rebuilt card travelled with the question"
        rows = {f["key"]: f for f in card["fields"]}
        assert "actual_fitout_complete_date" not in rows
        for key in (
            "planned_fitout_complete_date",
            "planned_trading_date",
            "delay_reason",
        ):
            assert rows[key]["editable"] is True, key
        assert rows["planned_trading_date"]["inputType"] == "date"
        # Nothing to submit yet — the card is for filling in.
        assert "confirmAction" not in card

    @pytest.mark.asyncio
    async def test_no_card_travels_for_a_workflow_that_has_none(self) -> None:
        state = _state()
        state["active_agent"] = "rdd_handover_agent"
        state["sub_category"] = "HANDOVER"
        state["workflow_stage"] = "FM_REVIEW"

        result = await missing_field_node(state)  # type: ignore[arg-type]

        assert "refresh_card" not in (result.get("response_ui") or {})
