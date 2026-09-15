"""Unit tests — Final Approve approves, and a picker row holds a real date.

Both reported on SR 321026, after the RDD report had been submitted:

- Tapping **Final Approve** re-rendered the *RDD Report Submission* card
  ("Review the details below and confirm to submit, or cancel") instead of
  approving. The chip had never carried its own CONFIRMED flag; it only
  reached the submission node because the flag from the preceding submit was
  still lying in state. Retiring that stale flag left the chip with nothing.

- The card's three date rows rendered as empty ``dd/mm/yyyy`` pickers. The
  platform stores those dates as display strings ("28/09/2026") and a native
  date input accepts ISO and nothing else, so it drew a blank and said nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.help_agent_graph import _route_after_rdd_confirmation
from app.agents.graph.nodes.handover.confirmation_node import (
    _picker_value,
    confirmation_node,
)
from app.agents.graph.nodes.handover.rdd_review_entry_node import (
    rdd_review_entry_node,
)

# The shape SR 321026 actually holds on the platform.
_PLATFORM_DATES = {
    "guideLineLink": "http://localhost:4200/x",
    "document_status": "APPROVED_WITH_CHANGES",
    "actual_handover_date": "28/09/2026",
    "fitout_start_date": "16/10/2026",
    "fitout_end_date": "17/10/2026",
    "trading_date": "25/10/2026",
    "lease_code": "t0110565",
}

_PICKER_KEYS = ("actual_handover_date", "fitout_start_date", "fitout_end_date")


async def _rdd_card(collected: dict[str, Any]) -> dict[str, dict[str, Any]]:
    state: dict[str, Any] = {
        "collected_data": dict(collected),
        "workflow_stage": "RDD_REVIEW",
        "documents": [
            {"document_id": "d1", "document_type_id": "DR_SR_HANDOVER_REPORT"}
        ],
        "backend_refs": {
            "sr_id": "321026",
            "user_role": "DD_ENGINEER",
            "rdd_action": "submit",
        },
    }
    ui = (await confirmation_node(state)).get("response_ui") or {}
    return {f["key"]: f for f in ui.get("fields", [])}


class TestAPickerRowHoldsARealDate:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", _PICKER_KEYS)
    async def test_a_stored_display_date_is_bound_as_iso(self, key: str) -> None:
        rows = await _rdd_card(_PLATFORM_DATES)
        assert rows[key]["inputType"] == "date"
        assert rows[key]["value"] == {
            "actual_handover_date": "2026-09-28",
            "fitout_start_date": "2026-10-16",
            "fitout_end_date": "2026-10-17",
        }[key]

    @pytest.mark.asyncio
    async def test_an_iso_date_is_left_alone(self) -> None:
        rows = await _rdd_card({**_PLATFORM_DATES, "fitout_end_date": "2026-10-17"})
        assert rows["fitout_end_date"]["value"] == "2026-10-17"

    @pytest.mark.asyncio
    async def test_an_unparseable_value_keeps_its_row_as_text(self) -> None:
        """A value the picker cannot hold is still one the reviewer must see."""
        rows = await _rdd_card({**_PLATFORM_DATES, "fitout_end_date": "next Tuesday"})
        assert "inputType" not in rows["fitout_end_date"]
        assert rows["fitout_end_date"]["value"] == "next Tuesday"

    @pytest.mark.parametrize("empty", [None, ""])
    def test_an_empty_date_keeps_its_picker(self, empty: Any) -> None:
        """An empty picker is the correct empty state, not a reason to drop it.

        (A card never reaches this with an empty required date — the node holds
        the turn until it is answered — but an optional one can.)
        """
        assert _picker_value("date", empty) == empty


class TestFinalApprove:
    async def _act(self, action: str) -> dict[str, Any]:
        return await rdd_review_entry_node(
            {
                "action_override": action,
                "user_message": "",
                "backend_refs": {"sr_id": "321026", "user_role": "DD_ENGINEER"},
                "collected_data": dict(_PLATFORM_DATES),
                "documents": [],
                "workflow_stage": "RDD_REVIEW",
                "confirmation_status": None,
            }
        )

    @pytest.mark.asyncio
    async def test_the_chip_confirms_in_the_same_turn(self) -> None:
        result = await self._act("approve_rdd_final")
        assert result["backend_refs"]["rdd_action"] == "final_approve"
        assert result["confirmation_status"] == "CONFIRMED"

    @pytest.mark.asyncio
    async def test_it_reaches_the_submission_not_another_card(self) -> None:
        result = await self._act("approve_rdd_final")
        assert _route_after_rdd_confirmation(result) == "rdd_payload_builder"

    @pytest.mark.asyncio
    async def test_it_no_longer_depends_on_a_leftover_confirmed_flag(self) -> None:
        """State arrives with confirmation_status None — the chip carries its own."""
        result = await self._act("approve_rdd_final")
        assert result["confirmation_status"] == "CONFIRMED"

    @pytest.mark.asyncio
    async def test_submit_still_reviews_before_sending(self) -> None:
        """Submit keeps its card; only Final Approve skips one."""
        result = await self._act("submit_rdd_report")
        assert result["backend_refs"]["rdd_action"] == "submit"
        assert result.get("confirmation_status") != "CONFIRMED"
