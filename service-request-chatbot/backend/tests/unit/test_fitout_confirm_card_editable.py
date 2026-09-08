"""The fitout confirmation card must let the user fix what it shows.

Every row on this card was built with ``"editable": False``, so the answer the
whole SR turns on — the yes/no milestone question — and the dates underneath it
could only be changed by typing a correction into chat. The Mall Manager could
see a wrong date on the card and had no way to touch it.

The rows the flow collects are now editable and carry the same typing contract
the handover card uses: ``inputType: "date"`` for a native picker, ``options``
for the two-way answer. The lease rows stay read-only — the lease is resolved by
lookup and every identifier on the request hangs off it.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.fitout_confirm.fitout_confirm_confirmation_node import (
    _build_confirmation_card,
)

_SUBCATS = ("FIT_OUT_CONFIRM_FOS", "FIT_OUT_CONFIRM_END", "FIT_OUT_CONFIRM_TRADING")

_RADIO_FIELD = {
    "FIT_OUT_CONFIRM_FOS": "fitout_start_done",
    "FIT_OUT_CONFIRM_END": "fitout_complete_done",
    "FIT_OUT_CONFIRM_TRADING": "trading_done",
}

_ACTUAL_FIELD = {
    "FIT_OUT_CONFIRM_FOS": "actual_fitout_start_date",
    "FIT_OUT_CONFIRM_END": "actual_fitout_complete_date",
    "FIT_OUT_CONFIRM_TRADING": "actual_trading_date",
}


def _rows(card: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["key"]: row for row in card["fields"]}


def _yes_branch(sub_category: str) -> dict[str, Any]:
    return {
        "lease_code": "LC-001",
        "lease_brand_mall": "LC-001 - Brand - Mall",
        _RADIO_FIELD[sub_category]: "yes",
        _ACTUAL_FIELD[sub_category]: "2026-09-10",
    }


@pytest.mark.parametrize("sub_category", _SUBCATS)
class TestYesBranch:
    def test_the_milestone_answer_is_editable_with_both_options(
        self, sub_category: str
    ) -> None:
        rows = _rows(
            _build_confirmation_card(_yes_branch(sub_category), sub_category, "CREATE_SR")
        )
        radio = rows[_RADIO_FIELD[sub_category]]
        assert radio["editable"] is True
        assert radio["options"] == ["yes", "no"]
        # The raw value, not a display string — it round-trips through a select.
        assert radio["value"] == "yes"

    def test_the_actual_date_carries_a_picker_and_an_iso_value(
        self, sub_category: str
    ) -> None:
        rows = _rows(
            _build_confirmation_card(_yes_branch(sub_category), sub_category, "CREATE_SR")
        )
        actual = rows[_ACTUAL_FIELD[sub_category]]
        assert actual["editable"] is True
        assert actual["inputType"] == "date"
        # A native date input only fills from YYYY-MM-DD.
        assert actual["value"] == "2026-09-10"

    def test_the_lease_rows_stay_read_only(self, sub_category: str) -> None:
        rows = _rows(
            _build_confirmation_card(_yes_branch(sub_category), sub_category, "CREATE_SR")
        )
        assert rows["lease_code"]["editable"] is False
        assert rows["lease_brand_mall"]["editable"] is False
        assert "inputType" not in rows["lease_code"]


class TestNoBranch:
    def test_revised_planned_dates_and_delay_reason_are_editable(self) -> None:
        collected = {
            "lease_code": "LC-001",
            "lease_brand_mall": "LC-001 - Brand - Mall",
            "fitout_start_done": "no",
            "planned_fitout_start_date": "2026-10-01",
            "planned_fitout_end_date": "2026-11-01",
            "planned_trading_date": "2026-11-15",
            "delay_reason": "Awaiting municipality approval",
        }
        rows = _rows(
            _build_confirmation_card(collected, "FIT_OUT_CONFIRM_FOS", "CREATE_SR")
        )
        for key in (
            "planned_fitout_start_date",
            "planned_fitout_end_date",
            "planned_trading_date",
        ):
            assert rows[key]["editable"] is True, key
            assert rows[key]["inputType"] == "date", key
        reason = rows["delay_reason"]
        assert reason["editable"] is True
        # Free text, so no picker and no options — a plain box.
        assert "inputType" not in reason
        assert "options" not in reason

    def test_the_approve_stage_card_is_editable_too(self) -> None:
        # Approval is where a wrong date is most expensive to leave standing.
        rows = _rows(
            _build_confirmation_card(
                _yes_branch("FIT_OUT_CONFIRM_TRADING"),
                "FIT_OUT_CONFIRM_TRADING",
                "APPROVE_SR",
            )
        )
        assert rows["trading_done"]["editable"] is True
        assert rows["actual_trading_date"]["editable"] is True


def test_a_missing_value_renders_as_an_empty_box_not_the_string_none() -> None:
    collected = {
        "lease_code": "LC-001",
        "lease_brand_mall": "LC-001 - Brand - Mall",
        "fitout_start_done": "yes",
        "actual_fitout_start_date": None,
    }
    rows = _rows(_build_confirmation_card(collected, "FIT_OUT_CONFIRM_FOS", "CREATE_SR"))
    assert rows["actual_fitout_start_date"]["value"] == ""
