"""Asked-month vs CSV-month warning."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.graph.nodes.sales_data.period_utils import (
    parse_asked_period,
    period_mismatch_warning,
)


def test_parse_asked_period_from_upload_request() -> None:
    asked = parse_asked_period("I want to upload August 2026 sales data")
    assert asked is not None
    assert asked["year_month"] == "2026-08"
    assert asked["label"] == "August 2026"


def test_parse_asked_period_ignores_generic_upload() -> None:
    assert parse_asked_period("I want to upload sales data") is None


def test_parse_asked_period_ignores_may_as_verb() -> None:
    assert parse_asked_period("I may want to upload sales data") is None


def test_period_mismatch_warning_when_months_differ() -> None:
    asked = parse_asked_period("upload August 2026")
    warning = period_mismatch_warning(
        asked,
        [{"sales_period_start": "2026-07-01", "sales_period_end": "2026-07-31"}],
    )
    assert warning is not None
    assert warning["asked_label"] == "August 2026"
    assert warning["file_label"] == "July 2026"
    assert "July 2026" in warning["message"]
    assert "August 2026" in warning["message"]
    assert warning["includes_asked_month"] is False


def test_period_mismatch_warning_when_file_includes_asked_plus_extra() -> None:
    asked = parse_asked_period("upload August 2026")
    warning = period_mismatch_warning(
        asked,
        [
            {"sales_period_start": "2026-08-01", "sales_period_end": "2026-08-31"},
            {"sales_period_start": "2026-07-01", "sales_period_end": "2026-07-31"},
        ],
    )
    assert warning is not None
    assert warning["includes_asked_month"] is True
    assert "same sheet" in warning["message"]
    assert "August 2026" in warning["message"]
    assert "July 2026" in warning["message"]


def test_period_mismatch_warning_skipped_when_months_match() -> None:
    asked = parse_asked_period("upload August 2026")
    assert (
        period_mismatch_warning(
            asked,
            [{"sales_period_start": "2026-08-01", "sales_period_end": "2026-08-31"}],
        )
        is None
    )


def test_period_mismatch_warning_skipped_when_no_asked_month() -> None:
    assert (
        period_mismatch_warning(
            None,
            [{"sales_period_start": "2026-07-01", "sales_period_end": "2026-07-31"}],
        )
        is None
    )


def _csv_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "lease_code": "LC-1001",
        "sales_period_start": "2026-07-01",
        "sales_period_end": "2026-07-31",
        "sales_amt": 85000,
    }
    row.update(overrides)
    return row


def _backend_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "lease_id": 1,
        "lease_code": "LC-1001",
        "sales_period_start": "2026-07-01",
        "sales_period_end": "2026-07-31",
        "status": "PENDING",
    }
    row.update(overrides)
    return row


def test_asked_period_from_conversation_history() -> None:
    from app.agents.graph.nodes.sales_data.period_utils import asked_period_from_state

    asked = asked_period_from_state(
        {
            "user_message": "Uploaded the CSV",
            "conversation_history": [
                {
                    "role": "user",
                    "content": "I want to upload August 2026 sales data",
                }
            ],
        }
    )
    assert asked is not None
    assert asked["year_month"] == "2026-08"


@pytest.mark.asyncio
async def test_lease_validation_warns_on_month_mismatch() -> None:
    from app.agents.graph.nodes.sales_data.lease_validation_node import (
        lease_validation_node,
    )

    client = AsyncMock()
    client.get_sales_data = AsyncMock(
        return_value=[
            _backend_row(),
            _backend_row(
                sales_period_start="2026-08-01",
                sales_period_end="2026-08-31",
            ),
        ]
    )
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
                    "asked_period": {
                        "label": "August 2026",
                        "year_month": "2026-08",
                    },
                    "csv_rows": [_csv_row()],
                }
            }
        )

    assert result["workflow_stage"] == "AWAITING_PERIOD_MISMATCH_CONFIRM"
    assert result["status"] == "WAITING_FOR_USER"
    assert result["response_ui"]["type"] == "period_mismatch_warning"
    assert result["response_ui"]["asked_label"] == "August 2026"
    assert result["response_ui"]["file_label"] == "July 2026"
    assert result["backend_refs"]["csv_rows"][0]["lease_id"] == 1


@pytest.mark.asyncio
async def test_lease_validation_warns_using_chat_history() -> None:
    from app.agents.graph.nodes.sales_data.lease_validation_node import (
        lease_validation_node,
    )

    client = AsyncMock()
    client.get_sales_data = AsyncMock(
        return_value=[
            _backend_row(),
            _backend_row(
                sales_period_start="2026-08-01",
                sales_period_end="2026-08-31",
            ),
        ]
    )
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
                "user_message": "Uploaded the CSV",
                "conversation_history": [
                    {
                        "role": "user",
                        "content": "I want to upload August 2026 sales data",
                    }
                ],
                "backend_refs": {"csv_rows": [_csv_row()]},
            }
        )

    assert result["workflow_stage"] == "AWAITING_PERIOD_MISMATCH_CONFIRM"
    assert result["response_ui"]["asked_label"] == "August 2026"
    assert result["response_ui"]["file_label"] == "July 2026"


@pytest.mark.asyncio
async def test_lease_validation_skips_warning_when_month_matches() -> None:
    from app.agents.graph.nodes.sales_data.lease_validation_node import (
        lease_validation_node,
    )

    client = AsyncMock()
    client.get_sales_data = AsyncMock(
        return_value=[
            _backend_row(
                sales_period_start="2026-08-01",
                sales_period_end="2026-08-31",
            )
        ]
    )
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
                    "asked_period": {
                        "label": "August 2026",
                        "year_month": "2026-08",
                    },
                    "csv_rows": [
                        _csv_row(
                            sales_period_start="2026-08-01",
                            sales_period_end="2026-08-31",
                        )
                    ],
                }
            }
        )

    assert result["workflow_stage"] == "AWAITING_TABLE_CONFIRM"
    assert result["status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_lease_validation_skips_warning_without_asked_period() -> None:
    from app.agents.graph.nodes.sales_data.lease_validation_node import (
        lease_validation_node,
    )

    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=[_backend_row()])
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
            {"backend_refs": {"csv_rows": [_csv_row()]}}
        )

    assert result["workflow_stage"] == "AWAITING_TABLE_CONFIRM"
    assert result["status"] == "IN_PROGRESS"
