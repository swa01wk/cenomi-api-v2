"""Unit tests for admin batch approve (admin_targets list + PATCH loop)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.graph.nodes.sales_data.admin_review_entry_node import (
    _lease_tokens_from_message,
    _match_targets,
    _mixed_actions,
    _select_all,
    admin_review_entry_node,
)
from app.agents.graph.nodes.sales_data.admin_status_update_node import (
    admin_status_update_node,
)

_ROWS = [
    {
        "lease_id": 4,
        "lease_code": "LC-2001",
        "status": "SUBMITTED",
        "sales_period_start": "2026-07-01",
        "sales_period_end": "2026-07-31",
        "sales_amt": 320000.0,
    },
    {
        "lease_id": 5,
        "lease_code": "LC-2002",
        "status": "SUBMITTED",
        "sales_period_start": "2026-07-01",
        "sales_period_end": "2026-07-31",
        "sales_amt": 95000.0,
    },
]


def _state(**overrides: Any) -> dict[str, Any]:
    return {
        "session_id": "sess-admin-001",
        "user_id": "admin-001",
        "user_message": "",
        "intent": "APPROVE_SALES_DATA",
        "action_override": None,
        "workflow_stage": "ADMIN_REVIEW",
        "backend_refs": {},
        "collected_data": {},
        **overrides,
    }


def test_lease_tokens_keep_shorthand_2002() -> None:
    tokens = _lease_tokens_from_message("approve Lc-2001 and 2002")
    assert tokens == ["LC-2001", "2002"]


def test_match_targets_expands_shorthand_against_review_list() -> None:
    matched, unmatched = _match_targets(["LC-2001", "2002"], _ROWS)
    assert unmatched == []
    assert [t["lease_code"] for t in matched] == ["LC-2001", "LC-2002"]
    assert [t["lease_id"] for t in matched] == [4, 5]


def test_match_targets_filters_same_lease_by_period() -> None:
    rows = [
        {
            "lease_id": 1,
            "lease_code": "LC-1001",
            "status": "SUBMITTED",
            "sales_period_start": "2026-08-01",
            "sales_period_end": "2026-08-31",
            "sales_amt": 1,
        },
        {
            "lease_id": 1,
            "lease_code": "LC-1001",
            "status": "SUBMITTED",
            "sales_period_start": "2026-07-01",
            "sales_period_end": "2026-07-31",
            "sales_amt": 4000,
        },
    ]
    matched, unmatched = _match_targets(
        ["LC-1001"],
        rows,
        period_start="2026-07-01",
        period_end="2026-07-31",
    )
    assert unmatched == []
    assert len(matched) == 1
    assert matched[0]["sales_period_start"] == "2026-07-01"
    assert matched[0]["sales_amt"] == 4000


def test_select_all_and_mixed_actions() -> None:
    assert _select_all("Approve all")
    assert _select_all("approve both of those")
    assert not _select_all("approve LC-2001")
    assert _mixed_actions("approve LC-2001 and request docs for LC-2002")
    assert not _mixed_actions("approve LC-2001 and 2002")


@pytest.mark.asyncio
async def test_review_sales_data_lists_submitted_rows() -> None:
    client = AsyncMock()
    pending = {**_ROWS[0], "status": "PENDING", "lease_code": "LC-1001"}
    rows = [pending, *_ROWS]
    client.get_sales_data_page = AsyncMock(return_value=(rows, len(rows)))
    with (
        patch(
            "app.agents.graph.nodes.sales_data.admin_review_entry_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.admin_review_entry_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await admin_review_entry_node(
            _state(intent="REVIEW_SALES_DATA", user_message="review sales data")
        )

    assert result["workflow_stage"] == "ADMIN_REVIEW"
    assert result["response_ui"]["type"] == "admin_sales_data_list"
    codes = [r["lease_code"] for r in result["response_ui"]["rows"]]
    assert codes == ["LC-2001", "LC-2002"]
    assert "LC-1001" not in codes
    assert "Approve LC-2001" in result["response_message"]
    assert "3 submissions awaiting review" in result["response_message"]

    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=_ROWS)
    with (
        patch(
            "app.agents.graph.nodes.sales_data.admin_review_entry_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.admin_review_entry_node.SalesDataApiClient",
            return_value=client,
        ),
        patch(
            "app.agents.graph.nodes.sales_data.admin_review_entry_node.get_llm_gateway",
        ) as mock_llm,
    ):
        result = await admin_review_entry_node(
            _state(user_message="approve Lc-2001 and 2002")
        )

    mock_llm.assert_not_called()
    assert result["workflow_stage"] == "ADMIN_CONFIRM"
    codes = [t["lease_code"] for t in result["collected_data"]["admin_targets"]]
    assert codes == ["LC-2001", "LC-2002"]
    assert result["response_ui"]["type"] == "admin_confirm"
    assert len(result["response_ui"]["targets"]) == 2
    assert "LC-2002" in result["response_message"]


@pytest.mark.asyncio
async def test_approve_real_lease_code_with_period_targets_one_month_only() -> None:
    """The review table's own Approve button ("Approve t0106037 for
    2026-05-01") must confirm only that one row, not every month the lease
    has awaiting review — this was the reported bug: clicking Approve on a
    single row in an 18-row list confirmed all 18.
    """
    rows = [
        {
            "lease_id": 9,
            "lease_code": "t0106037",
            "status": "SUBMITTED",
            "sales_period_start": f"2026-{month:02d}-01",
            "sales_period_end": f"2026-{month:02d}-28",
            "sales_amt": 100000.0 + month,
        }
        for month in (4, 5, 6)
    ]
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=rows)
    with (
        patch(
            "app.agents.graph.nodes.sales_data.admin_review_entry_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.admin_review_entry_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await admin_review_entry_node(
            _state(user_message="Approve t0106037 for 2026-05-01")
        )

    assert result["workflow_stage"] == "ADMIN_CONFIRM"
    targets = result["collected_data"]["admin_targets"]
    assert len(targets) == 1
    assert targets[0]["sales_period_start"] == "2026-05-01"


@pytest.mark.asyncio
async def test_approve_all_targets_every_reviewable_row() -> None:
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=_ROWS)
    with (
        patch(
            "app.agents.graph.nodes.sales_data.admin_review_entry_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.admin_review_entry_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await admin_review_entry_node(_state(user_message="Approve all"))

    assert result["workflow_stage"] == "ADMIN_CONFIRM"
    assert len(result["collected_data"]["admin_targets"]) == 2


@pytest.mark.asyncio
async def test_single_lease_approve_still_works() -> None:
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=_ROWS)
    with (
        patch(
            "app.agents.graph.nodes.sales_data.admin_review_entry_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.admin_review_entry_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await admin_review_entry_node(
            _state(user_message="Approve LC-2001")
        )

    targets = result["collected_data"]["admin_targets"]
    assert [t["lease_code"] for t in targets] == ["LC-2001"]
    assert result["collected_data"]["lease_id"] == 4


@pytest.mark.asyncio
async def test_mixed_actions_are_rejected() -> None:
    result = await admin_review_entry_node(
        _state(user_message="approve LC-2001 and request docs for LC-2002")
    )
    assert result["workflow_stage"] == "ADMIN_REVIEW"
    assert "one action" in result["response_message"].lower()


@pytest.mark.asyncio
async def test_confirm_accepts_admin_targets() -> None:
    result = await admin_review_entry_node(
        _state(
            action_override="confirm_admin_action",
            collected_data={
                "admin_action": "APPROVED",
                "admin_targets": [
                    {"lease_id": 4, "lease_code": "LC-2001"},
                    {"lease_id": 5, "lease_code": "LC-2002"},
                ],
            },
        )
    )
    assert result["workflow_stage"] == "ADMIN_SUBMITTING"
    assert result["confirmation_status"] == "CONFIRMED"


@pytest.mark.asyncio
async def test_status_update_patches_each_target() -> None:
    client = AsyncMock()
    client.update_sales_data_statuses = AsyncMock(
        return_value=[
            {"lease_code": "LC-2001", "success": True},
            {"lease_code": "LC-2002", "success": True},
        ]
    )
    with (
        patch(
            "app.agents.graph.nodes.sales_data.admin_status_update_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.admin_status_update_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await admin_status_update_node(
            _state(
                collected_data={
                    "admin_action": "APPROVED",
                    "admin_targets": [
                        {
                            "lease_id": 4,
                            "lease_code": "LC-2001",
                            "sales_period_start": "2026-07-01",
                            "sales_period_end": "2026-07-31",
                        },
                        {
                            "lease_id": 5,
                            "lease_code": "LC-2002",
                            "sales_period_start": "2026-07-01",
                            "sales_period_end": "2026-07-31",
                        },
                    ],
                }
            )
        )

    client.update_sales_data_statuses.assert_awaited_once()
    payloads = client.update_sales_data_statuses.await_args.args[0]
    assert [p["lease_id"] for p in payloads] == [4, 5]
    assert all(p["status"] == "APPROVED" for p in payloads)
    assert result["response_ui"]["type"] == "admin_action_success"
    assert result["response_ui"]["lease_codes"] == ["LC-2001", "LC-2002"]
    assert "LC-2002" in result["response_message"]


@pytest.mark.asyncio
async def test_review_list_includes_uploaded_supporting_and_audited() -> None:
    """Portal Submitted tab: UPLOADED_* rows are reviewable; badge text is Submitted."""
    rows = [
        {
            "lease_id": 4,
            "lease_code": "LC-2001",
            "status": "UPLOADED_SUPPORTING_DOCUMENT",
            "sales_period_start": "2026-07-01",
            "sales_period_end": "2026-07-31",
            "sales_amt": 320000.0,
            "property_name": "Riyadh Park",
            "company_name": "Centrepoint Co",
            "brand_name": "Centrepoint",
        },
        {
            "lease_id": 5,
            "lease_code": "LC-2002",
            "status": "UPLOADED_AUDITED_SALES",
            "sales_period_start": "2026-07-01",
            "sales_period_end": "2026-07-31",
            "sales_amt": 95000.0,
        },
        {
            "lease_id": 6,
            "lease_code": "LC-1004",
            "status": "NEED_UPLOAD_SUPPORTING_DOCUMENT",
            "sales_period_start": "2026-07-01",
            "sales_period_end": "2026-07-31",
            "sales_amt": 72000.0,
        },
    ]
    client = AsyncMock()
    client.get_sales_data_page = AsyncMock(return_value=(rows, len(rows)))
    with (
        patch(
            "app.agents.graph.nodes.sales_data.admin_review_entry_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.admin_review_entry_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await admin_review_entry_node(
            _state(intent="REVIEW_SALES_DATA", user_message="review sales data")
        )

    ui_rows = result["response_ui"]["rows"]
    codes = [r["lease_code"] for r in ui_rows]
    assert codes == ["LC-2001", "LC-2002"]
    assert "LC-1004" not in codes
    assert "3 submissions awaiting review" in result["response_message"]
    assert any(r.get("property_name") == "Riyadh Park" for r in ui_rows)
    assert any(r.get("company_name") == "Centrepoint Co" for r in ui_rows)
