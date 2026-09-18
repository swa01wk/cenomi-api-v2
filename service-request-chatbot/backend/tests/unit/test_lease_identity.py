"""Brand / Property matching against GET /sales-data lease names."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.services.sales_data_api_client import partition_rows_by_lease_access


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


def _csv(**overrides: Any) -> dict[str, Any]:
    row = {
        "lease_code": "LC-1001",
        "brand": "Noon Fashion",
        "property": "Riyadh Park",
        "sales_period_start": "2026-08-01",
        "sales_period_end": "2026-08-31",
        "sales_amt": 85000,
    }
    row.update(overrides)
    return row


def test_blank_brand_property_fills_from_platform() -> None:
    result = partition_rows_by_lease_access(
        [_csv(brand="", property="")],
        [_backend()],
    )
    assert result["identity_mismatch"] == []
    assert result["valid"][0]["brand"] == "Noon Fashion"
    assert result["valid"][0]["property"] == "Riyadh Park"


def test_case_and_spacing_match_keeps_platform_spelling() -> None:
    result = partition_rows_by_lease_access(
        [_csv(brand="noon  fashion", property="riyadh park")],
        [_backend()],
    )
    assert result["identity_mismatch"] == []
    assert result["valid"][0]["brand"] == "Noon Fashion"
    assert result["valid"][0]["property"] == "Riyadh Park"


def test_wrong_property_is_identity_mismatch() -> None:
    result = partition_rows_by_lease_access(
        [_csv(property="Jeddah Mall")],
        [_backend()],
    )
    assert result["valid"] == []
    assert len(result["identity_mismatch"]) == 1
    assert "Jeddah Mall" in result["identity_mismatch"][0]["_error"]
    assert "Riyadh Park" in result["identity_mismatch"][0]["_error"]
    assert "upload the file again" in result["identity_mismatch"][0]["_error"]


def test_wrong_brand_is_identity_mismatch() -> None:
    result = partition_rows_by_lease_access(
        [_csv(brand="Zara")],
        [_backend()],
    )
    assert result["valid"] == []
    assert "Zara" in result["identity_mismatch"][0]["_error"]
    assert "Noon Fashion" in result["identity_mismatch"][0]["_error"]


def test_live_cenomi_mall_name_without_property_name() -> None:
    result = partition_rows_by_lease_access(
        [_csv(property="mall of arabia")],
        [_backend(property_name="", mall_name="Mall of Arabia")],
    )
    assert result["identity_mismatch"] == []
    assert result["valid"][0]["property"] == "Mall of Arabia"


def test_platform_missing_names_does_not_reject() -> None:
    result = partition_rows_by_lease_access(
        [_csv()],
        [_backend(brand_name="", property_name="", mall_name="")],
    )
    assert result["identity_mismatch"] == []
    assert len(result["valid"]) == 1


@pytest.mark.asyncio
async def test_lease_validation_node_asks_reupload_on_wrong_property() -> None:
    from app.agents.graph.nodes.sales_data.lease_validation_node import (
        lease_validation_node,
    )

    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=[_backend()])
    with (
        patch(
            "app.agents.graph.nodes.sales_data.lease_validation_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.lease_validation_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await lease_validation_node(
            {"backend_refs": {"csv_rows": [_csv(property="Wrong Mall")]}}
        )

    assert result["workflow_stage"] == "UPLOAD_INTENT"
    assert result["status"] == "WAITING_FOR_USER"
    assert result["response_ui"]["type"] == "lease_validation_error"
    assert result["response_ui"]["show_file_upload"] is True
    assert "upload it again" in result["response_message"].lower()
    assert result["workflow_stage"] != "STALE_NOTIFICATION"


@pytest.mark.asyncio
async def test_lease_validation_node_accepts_lowercase_names() -> None:
    from app.agents.graph.nodes.sales_data.lease_validation_node import (
        lease_validation_node,
    )

    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=[_backend()])
    with (
        patch(
            "app.agents.graph.nodes.sales_data.lease_validation_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.lease_validation_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await lease_validation_node(
            {
                "backend_refs": {
                    "csv_rows": [_csv(brand="NOON FASHION", property="riyadh park")],
                }
            }
        )

    assert result["workflow_stage"] == "AWAITING_TABLE_CONFIRM"
    row = result["backend_refs"]["csv_rows"][0]
    assert row["brand"] == "Noon Fashion"
    assert row["property"] == "Riyadh Park"
