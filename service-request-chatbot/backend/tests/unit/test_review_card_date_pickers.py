"""Unit tests — the review cards' date rows carry a native picker.

Reported on the RDD Project Manager's preview card: Actual Handover Date,
Fitout Start Date and Fitout End Date had no calendar. Each was a free-text box
seeded with the display string ("27/09/2026"), so changing a date meant
retyping it in exactly the right shape.

``inputType`` is the flag the card reads (ConfirmationCard.typedInputOf): with
it the row renders ``<input type="date">`` and binds the raw ISO value; without
it the row falls back to text and is seeded with the formatted display value.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.handover.confirmation_node import confirmation_node


def _rows(ui: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["key"]: f for f in ui.get("fields", [])}


async def _card(stage: str, collected: dict[str, Any], refs: dict[str, Any]) -> dict[str, Any]:
    state: dict[str, Any] = {
        "collected_data": dict(collected),
        "workflow_stage": stage,
        "documents": [
            {
                "document_id": "d1",
                "document_type_id": (
                    "DR_SR_HANDOVER_REPORT"
                    if stage == "RDD_REVIEW"
                    else "SR_HANDOVER_CHECKLIST"
                ),
            }
        ],
        "backend_refs": refs,
    }
    return (await confirmation_node(state)).get("response_ui") or {}


_RDD = {
    "guideLineLink": "https://cenomi.com/guidelines",
    "document_status": "APPROVED",
    "actual_handover_date": "2026-09-27",
    "fitout_start_date": "2026-10-15",
    "fitout_end_date": "2026-10-15",
    "trading_date": "2026-10-23",
    "comments": "ok",
    "lease_code": "t0112381",
}

_RDD_REFS = {"sr_id": "320649", "user_role": "DD_ENGINEER", "rdd_action": "submit"}


class TestTheRddCard:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "key", ["actual_handover_date", "fitout_start_date", "fitout_end_date"]
    )
    async def test_each_editable_date_gets_a_picker(self, key: str) -> None:
        rows = _rows(await _card("RDD_REVIEW", _RDD, _RDD_REFS))
        assert rows[key]["inputType"] == "date"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "key", ["actual_handover_date", "fitout_start_date", "fitout_end_date"]
    )
    async def test_a_picker_row_carries_the_raw_iso_value(self, key: str) -> None:
        """A picker binds YYYY-MM-DD; a display string would render blank."""
        rows = _rows(await _card("RDD_REVIEW", _RDD, _RDD_REFS))
        assert rows[key]["value"] == _RDD[key]

    @pytest.mark.asyncio
    async def test_the_auto_trading_date_stays_read_only_text(self) -> None:
        """It is derived from the fitout end date — never typed, never picked."""
        row = _rows(await _card("RDD_REVIEW", _RDD, _RDD_REFS))["trading_date"]
        assert row["editable"] is False
        assert "inputType" not in row
        assert row["value"] == "23/10/2026"


_FM = {
    "unit_readiness_date": "2026-09-20",
    "expected_handover_date": "2026-09-27",
    "comments": "ok",
}

_FM_REFS = {"sr_id": "320649", "user_role": "FM_MANAGER", "fm_action": "approve"}


class TestTheFmCard:
    @pytest.mark.asyncio
    async def test_the_readiness_date_gets_a_picker(self) -> None:
        row = _rows(await _card("FM_REVIEW", _FM, _FM_REFS))[
            "unit_readiness_date"
        ]
        assert row["inputType"] == "date"
        assert row["value"] == "2026-09-20"

    @pytest.mark.asyncio
    async def test_the_computed_handover_date_does_not(self) -> None:
        """Readiness + 7 days is written by merge_state, not by the reviewer."""
        row = _rows(await _card("FM_REVIEW", _FM, _FM_REFS))[
            "expected_handover_date"
        ]
        assert row["editable"] is False
        assert "inputType" not in row
