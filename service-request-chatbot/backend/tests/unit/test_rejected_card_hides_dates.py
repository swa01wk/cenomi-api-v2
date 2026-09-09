"""A disapproved review card must not list dates a rejection never collects.

Follows the change that stopped the flow asking for them: with a Rejected
report uploaded, ``document_status`` is DISAPPROVED, no dates are asked and
none block the submit — but the review card still listed four empty rows the
reviewer had no way to fill.

``trading_date`` goes with them: it is only ever derived from the fit-out end,
so with no fit-out end there is nothing to show.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.handover import confirmation_node as C

_DATES = {"actual_handover_date", "fitout_start_date", "fitout_end_date", "trading_date"}


def _state(status: str, **over: Any) -> dict[str, Any]:
    collected: dict[str, Any] = {
        "lease_code": "t0112285",
        "brand": "Brand Saad Eddin Sweets",
        "mall": "Yasmeen Mall",
        "guideLineLink": "http://google.com",
        "document_status": status,
        "comments": "Rejected — fit-out not to specification.",
        "actual_handover_date": "",
        "fitout_start_date": "",
        "fitout_end_date": "",
        "trading_date": "",
    }
    collected.update(over)
    return {
        "workflow_stage": "RDD_REVIEW",
        "active_agent": "rdd_handover_agent",
        "sub_category": "HANDOVER",
        "collected_data": collected,
        "documents": [
            {"document_type_id": "SR_REJECTED_HANDOVER_REPORT", "document_id": "d1"}
        ],
        "backend_refs": {
            "sr_id": "341673",
            "documents": [{"document_type_id": "SR_REJECTED_HANDOVER_REPORT"}],
        },
        "validation_errors": [],
    }


async def _rows(status: str, **over: Any) -> list[str]:
    result = await C.confirmation_node(_state(status, **over))  # type: ignore[arg-type]
    return [f["key"] for f in (result.get("response_ui") or {}).get("fields", [])]


@pytest.mark.asyncio
async def test_a_disapproved_card_shows_no_dates() -> None:
    rows = await _rows("DISAPPROVED")
    assert rows, "the card should still be built"
    assert not _DATES.intersection(rows)


@pytest.mark.asyncio
async def test_it_still_shows_what_the_rejection_does_record() -> None:
    rows = await _rows("DISAPPROVED")
    for key in ("lease_code", "guideLineLink", "document_status", "comments"):
        assert key in rows, key


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", ["disapproved", "  DISAPPROVED  "])
async def test_the_status_is_matched_however_it_is_written(spelling: str) -> None:
    assert not _DATES.intersection(await _rows(spelling))


@pytest.mark.asyncio
async def test_an_approved_review_still_shows_its_dates() -> None:
    rows = await _rows(
        "APPROVED",
        actual_handover_date="2026-09-16",
        fitout_start_date="2026-10-06",
        fitout_end_date="2026-10-20",
        trading_date="2026-10-28",
    )
    assert _DATES.issubset(rows)
