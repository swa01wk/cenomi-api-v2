"""Manual entry confirmation includes Brand/Property from the sales-data API."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.graph.nodes.sales_data.manual_entry_confirmation_node import (
    manual_entry_confirmation_node,
)


def _backend(**overrides: Any) -> dict[str, Any]:
    row = {
        "lease_id": 1,
        "lease_code": "LC-1001",
        "brand_name": "Noon Fashion",
        "property_name": "Riyadh Park",
        "mall_name": "Riyadh Park",
        "sales_period_start": "2026-08-01",
        "sales_period_end": "2026-08-31",
        "status": "PENDING",
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_confirmation_card_includes_mapped_brand_and_property() -> None:
    result = await manual_entry_confirmation_node(
        {
            "collected_data": {
                "lease_code": "LC-1001",
                "brand": "Noon Fashion",
                "property": "Riyadh Park",
                "sales_period_start": "2026-08-01",
                "sales_period_end": "2026-08-31",
                "sales_amt": 100000,
            },
            "workflow_stage": "AWAITING_MANUAL_CONFIRM",
            "user_message": "",
        }
    )
    ui = result["response_ui"]
    assert ui["type"] == "manual_entry_confirm"
    assert ui["brand"] == "Noon Fashion"
    assert ui["property"] == "Riyadh Park"
    # Brand/Property are rendered by the card from `ui["fields"]`, not
    # duplicated into the plain-text chat bubble.
    field_values = {f["key"]: f["value"] for f in ui["fields"]}
    assert field_values["brand"] == "Noon Fashion"
    assert field_values["property"] == "Riyadh Park"


@pytest.mark.asyncio
async def test_lease_validation_maps_brand_property_from_platform() -> None:
    from app.agents.graph.nodes.sales_data.manual_entry_lease_validation_node import (
        manual_entry_lease_validation_node,
    )

    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=[_backend()])
    with (
        patch(
            "app.agents.graph.nodes.sales_data.manual_entry_lease_validation_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.manual_entry_lease_validation_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await manual_entry_lease_validation_node(
            {
                "collected_data": {
                    "lease_code": "LC-1001",
                    "sales_period_start": "2026-08-01",
                    "sales_period_end": "2026-08-31",
                    "sales_amt": 100000,
                },
                "backend_refs": {},
            }
        )

    collected = result["collected_data"]
    assert collected["brand"] == "Noon Fashion"
    assert collected["property"] == "Riyadh Park"
    assert collected["lease_id"] == 1
    assert result["workflow_stage"] == "AWAITING_MANUAL_CONFIRM"
