"""Typed sales-data entries must be understood without the LLM."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from app.agents.graph.nodes.sales_data.manual_entry_parser import parse_manual_entry

TODAY = date(2026, 9, 29)
PENDING = [
    {"lease_code": "t0057323", "year_month": "2026-08"},
    {"lease_code": "t0057323", "year_month": "2026-09"},
    {"lease_code": "t0042756", "year_month": "2026-08"},
]
AUG = {"sales_period_start": "2026-08-01", "sales_period_end": "2026-08-31"}


@pytest.mark.parametrize(
    "message, expected",
    [
        ("t0057323 - aug 2026 - 12280SAR", {"lease_code": "t0057323", **AUG, "sales_amt": 12280.0}),
        ("SAR12280 for t0057323 in aug", {"lease_code": "t0057323", **AUG, "sales_amt": 12280.0}),
        ("sales for lease T0057323 last month were SAR 12,280", {"lease_code": "t0057323", **AUG, "sales_amt": 12280.0}),
        ("lease t0042756, 08/2026, amount 9,500.50", {"lease_code": "t0042756", **AUG, "sales_amt": 9500.5}),
        ("57323 sept 26 12.5k", {
            "lease_code": "t0057323",
            "sales_period_start": "2026-09-01",
            "sales_period_end": "2026-09-30",
            "sales_amt": 12500.0,
        }),
        ("T0057323 August 12280", {"lease_code": "t0057323", **AUG, "sales_amt": 12280.0}),
        ("2026-08-01 to 2026-08-31 t0057323 44000", {"lease_code": "t0057323", **AUG, "sales_amt": 44000.0}),
        ("LC-1001 aug 2026 85000", {"lease_code": "LC-1001", **AUG, "sales_amt": 85000.0}),
        ("12280", {"sales_amt": 12280.0}),
        ("may i submit for t0057323", {"lease_code": "t0057323"}),
        ("hi", {}),
    ],
)
def test_parses_messy_entries(message, expected) -> None:
    assert parse_manual_entry(message, pending_items=PENDING, today=TODAY) == expected


def test_attached_minus_is_negative_but_spaced_dash_is_a_separator() -> None:
    assert parse_manual_entry("t0057323 aug 2026 amount -500", pending_items=PENDING, today=TODAY)["sales_amt"] == -500.0
    assert parse_manual_entry("t0057323 - aug 2026 - 500", pending_items=PENDING, today=TODAY)["sales_amt"] == 500.0


def test_month_without_year_is_never_in_the_future() -> None:
    parsed = parse_manual_entry("LC-1001 december 5000", today=TODAY)
    assert parsed["sales_period_start"] == "2025-12-01"


@pytest.mark.asyncio
async def test_complete_message_is_collected_even_when_llm_is_down() -> None:
    from app.agents.graph.nodes.sales_data.manual_entry_extraction_node import (
        manual_entry_extraction_node,
    )

    class _Broken:
        async def generate(self, **_):
            raise RuntimeError("401 invalid api key")

    with patch(
        "app.agents.graph.nodes.sales_data.manual_entry_extraction_node.get_llm_gateway",
        return_value=_Broken(),
    ) as gateway:
        result = await manual_entry_extraction_node({
            "user_message": "t0057323 - aug 2026 - 12280SAR",
            "collected_data": {"_pending_items": PENDING},
            "backend_refs": {},
        })
    collected = result["collected_data"]
    assert collected["lease_code"] == "t0057323"
    assert collected["sales_period_start"].endswith("-08-01")
    assert collected["sales_amt"] == 12280.0
    gateway.assert_not_called()  # nothing missing → no LLM round trip


@pytest.mark.asyncio
async def test_missing_field_question_acknowledges_what_was_given() -> None:
    from app.agents.graph.nodes.sales_data.manual_entry_missing_field_node import (
        manual_entry_missing_field_node,
    )

    result = await manual_entry_missing_field_node({
        "collected_data": {
            "lease_code": "t0057323",
            "sales_period_start": "2026-08-01",
            "sales_period_end": "2026-08-31",
        },
        "backend_refs": {},
    })
    message = result["response_message"]
    assert message.startswith("Lease **t0057323** and **August 2026** noted.")
    assert "total sales amount" in message
    assert "YYYY-MM-DD" not in message
    assert result["response_ui"]["verbatim"] is True


@pytest.mark.asyncio
async def test_missing_everything_asks_one_question_with_an_example() -> None:
    from app.agents.graph.nodes.sales_data.manual_entry_missing_field_node import (
        manual_entry_missing_field_node,
    )

    result = await manual_entry_missing_field_node({
        "collected_data": {"_pending_items": PENDING},
        "backend_refs": {},
    })
    message = result["response_message"]
    # One question per turn (handover-agent rule), plus the all-in-one example.
    assert message.startswith("Which lease is this sales data for?")
    assert message.count("?") == 1
    assert "t0057323" in message  # suggests their pending leases
    assert "all the details in one message" in message


@pytest.mark.parametrize(
    "message",
    [
        "can i put in this month's revenue",
        "need to send sales for aug",
        "i wanna give my sales numbers",
        "key in sales pls",
        "t0057323 aug 2026 12280",
    ],
)
def test_informal_upload_requests_start_the_sales_flow(message) -> None:
    from app.agents.graph.nodes.supervisor_node import _sales_data_shortcut

    result = _sales_data_shortcut(message, ["SHOP_MANAGER"], allow_typed_entry=True)
    assert result and result["intent"] == "UPLOAD_SALES_DATA"


@pytest.mark.parametrize(
    "message",
    ["upload my drawings", "show submitted sales", "did my sales upload go through", "hey"],
)
def test_non_upload_messages_are_not_hijacked(message) -> None:
    from app.agents.graph.nodes.supervisor_node import _sales_data_shortcut

    result = _sales_data_shortcut(message, ["SHOP_MANAGER"], allow_typed_entry=True)
    assert not result or result["intent"] != "UPLOAD_SALES_DATA"
