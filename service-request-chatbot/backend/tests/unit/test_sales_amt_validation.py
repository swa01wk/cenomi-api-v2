"""Reject a zero/negative sales amount — typed manual entry, and a review-table
edit made after CSV/manual validation already passed.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.graph.nodes.sales_data.manual_entry_extraction_node import (
    manual_entry_extraction_node,
)
from app.agents.graph.nodes.sales_data.sales_data_payload_builder_node import (
    sales_data_payload_builder_node,
)


def _llm_response(fields: dict[str, Any]) -> AsyncMock:
    response = AsyncMock()
    response.content = json.dumps({"fields": fields})
    return response


@pytest.mark.asyncio
async def test_negative_amount_not_stored_reprompts() -> None:
    gateway = AsyncMock()
    gateway.generate = AsyncMock(
        return_value=_llm_response(
            {
                "lease_code": {"value": "T0106037"},
                "sales_amt": {"value": "-5000"},
            }
        )
    )
    with patch(
        "app.agents.graph.nodes.sales_data.manual_entry_extraction_node.get_llm_gateway",
        return_value=gateway,
    ):
        result = await manual_entry_extraction_node(
            {
                "user_message": "lease T0106037, amount -5000",
                "collected_data": {},
                "backend_refs": {},
                "conversation_history": [],
            }
        )

    assert "sales_amt" not in result["collected_data"]
    assert result["collected_data"]["lease_code"] == "T0106037"


@pytest.mark.asyncio
async def test_zero_amount_not_stored() -> None:
    gateway = AsyncMock()
    gateway.generate = AsyncMock(
        return_value=_llm_response({"sales_amt": {"value": "0"}})
    )
    with patch(
        "app.agents.graph.nodes.sales_data.manual_entry_extraction_node.get_llm_gateway",
        return_value=gateway,
    ):
        result = await manual_entry_extraction_node(
            {
                "user_message": "amount is 0",
                "collected_data": {},
                "backend_refs": {},
                "conversation_history": [],
            }
        )

    assert "sales_amt" not in result["collected_data"]


@pytest.mark.asyncio
async def test_positive_amount_still_stored() -> None:
    gateway = AsyncMock()
    gateway.generate = AsyncMock(
        return_value=_llm_response({"sales_amt": {"value": "85,000"}})
    )
    with patch(
        "app.agents.graph.nodes.sales_data.manual_entry_extraction_node.get_llm_gateway",
        return_value=gateway,
    ):
        result = await manual_entry_extraction_node(
            {
                "user_message": "amount is 85,000",
                "collected_data": {},
                "backend_refs": {},
                "conversation_history": [],
            }
        )

    assert result["collected_data"]["sales_amt"] == 85000.0


@pytest.mark.asyncio
async def test_payload_builder_rejects_negative_edited_amount() -> None:
    result = await sales_data_payload_builder_node(
        {
            "backend_refs": {},
            "corrected_fields": {
                "rows": [
                    {
                        "lease_code": "T0106037",
                        "sales_period_start": "2026-08-01",
                        "sales_period_end": "2026-08-31",
                        "sales_amt": -1000,
                    }
                ]
            },
        }
    )

    assert result["status"] == "WAITING_FOR_USER"
    assert result["workflow_stage"] == "AWAITING_TABLE_CONFIRM"
    assert "T0106037" in result["response_message"]
    assert "greater than 0" in result["response_message"]


@pytest.mark.asyncio
async def test_payload_builder_rejects_unparseable_amount_instead_of_defaulting_to_zero() -> None:
    """A blank/garbage amount used to silently become 0.0 and submit anyway."""
    result = await sales_data_payload_builder_node(
        {
            "backend_refs": {},
            "corrected_fields": {
                "rows": [
                    {
                        "lease_code": "T0106038",
                        "sales_period_start": "2026-08-01",
                        "sales_period_end": "2026-08-31",
                        "sales_amt": "",
                    }
                ]
            },
        }
    )

    assert result["status"] == "WAITING_FOR_USER"
    assert "T0106038" in result["response_message"]


@pytest.mark.asyncio
async def test_payload_builder_accepts_valid_rows_and_reports_only_invalid_leases() -> None:
    result = await sales_data_payload_builder_node(
        {
            "backend_refs": {},
            "corrected_fields": {
                "rows": [
                    {
                        "lease_code": "GOOD-001",
                        "sales_period_start": "2026-08-01",
                        "sales_period_end": "2026-08-31",
                        "sales_amt": 5000,
                    },
                    {
                        "lease_code": "BAD-001",
                        "sales_period_start": "2026-08-01",
                        "sales_period_end": "2026-08-31",
                        "sales_amt": -1,
                    },
                ]
            },
        }
    )

    assert result["status"] == "WAITING_FOR_USER"
    assert "BAD-001" in result["response_message"]
    assert "GOOD-001" not in result["response_message"]


@pytest.mark.asyncio
async def test_payload_builder_submits_when_all_amounts_valid() -> None:
    client = AsyncMock()
    client.get_sales_data = AsyncMock(
        return_value=[
            {
                "lease_id": 1,
                "lease_code": "T0106037",
                "status": "PENDING",
                "sales_period_start": "2026-08-01",
                "sales_period_end": "2026-08-31",
                "brand": "Any Brand",
                "property": "Any Property",
            }
        ]
    )
    with (
        patch(
            "app.agents.graph.nodes.sales_data.sales_data_payload_builder_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.sales_data_payload_builder_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await sales_data_payload_builder_node(
            {
                "backend_refs": {},
                "corrected_fields": {
                    "rows": [
                        {
                            "lease_code": "T0106037",
                            "sales_period_start": "2026-08-01",
                            "sales_period_end": "2026-08-31",
                            "sales_amt": 5000,
                        }
                    ]
                },
            }
        )

    assert result["workflow_stage"] == "SUBMITTING"
    assert result["backend_refs"]["submit_payload"][0]["sales_amt"] == 5000.0


@pytest.mark.asyncio
async def test_payload_builder_rejects_stale_row_before_submit() -> None:
    """Layer 2 passed at review-table build time, but the pending record was
    consumed/changed since then (approved, re-uploaded elsewhere, etc.) —
    this must be caught here with a clear message, not surface as a raw
    "Lease Code and Sales Period don't match any pending sales record" only
    after the tenant has already clicked Submit and it hits cenomi-api.
    """
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=[])  # no longer pending anywhere
    with (
        patch(
            "app.agents.graph.nodes.sales_data.sales_data_payload_builder_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.sales_data_payload_builder_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await sales_data_payload_builder_node(
            {
                "backend_refs": {},
                "corrected_fields": {
                    "rows": [
                        {
                            "lease_code": "T0106037",
                            "sales_period_start": "2026-08-01",
                            "sales_period_end": "2026-08-31",
                            "sales_amt": 5000,
                        }
                    ]
                },
            }
        )

    assert result["status"] == "WAITING_FOR_USER"
    assert result["workflow_stage"] != "SUBMITTING"
    assert "T0106037" in result["response_message"]
    assert "no longer" in result["response_message"].lower()
