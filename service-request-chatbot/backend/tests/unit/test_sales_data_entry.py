"""Unit tests for sales_data_entry_node routing."""

from __future__ import annotations

from typing import Any

import pytest


def _state(**overrides: Any) -> dict[str, Any]:
    return {
        "session_id": "sess-sd-001",
        "user_id": "user-001",
        "user_message": "",
        "intent": None,
        "action_override": None,
        "workflow_stage": None,
        "backend_refs": {},
        "corrected_fields": {},
        **overrides,
    }


@pytest.mark.asyncio
async def test_upload_after_status_lookup_returns_upload_prompt() -> None:
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    result = await sales_data_entry_node(
        _state(
            intent="CHECK_SALES_DATA_STATUS",
            workflow_stage="STATUS_LOOKUP",
            user_message="I want to upload sales data",
        )
    )
    assert result["workflow_stage"] == "PENDING_MONTHS_CHECK"
    assert result["status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_notification_submit_does_not_go_to_manual_entry() -> None:
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    result = await sales_data_entry_node(
        _state(
            intent="UPLOAD_SALES_DATA",
            user_message=(
                "I received a notification about pending sales data for lease "
                "LC-1001 for August 2026. I want to submit it."
            ),
            corrected_fields={
                "notification_context": {
                    "lease_code": "LC-1001",
                    "period_label": "August 2026",
                    "source": "notification",
                }
            },
        )
    )
    assert result["workflow_stage"] == "PENDING_MONTHS_CHECK"
    assert result["status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_upload_august_stores_asked_period() -> None:
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    result = await sales_data_entry_node(
        _state(
            intent="UPLOAD_SALES_DATA",
            user_message="I want to upload August 2026 sales data",
        )
    )
    assert result["workflow_stage"] == "PENDING_MONTHS_CHECK"
    assert result["status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_period_mismatch_proceed_goes_to_review_table() -> None:
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    result = await sales_data_entry_node(
        _state(
            intent="UPLOAD_SALES_DATA",
            workflow_stage="AWAITING_PERIOD_MISMATCH_CONFIRM",
            action_override="confirm_period_mismatch",
            backend_refs={
                "asked_period": {
                    "label": "August 2026",
                    "year_month": "2026-08",
                },
                "csv_session_id": "csv-1",
                "csv_rows": [{"lease_code": "LC-1001"}],
            },
            collected_data={
                "_csv_rows": [{"lease_code": "LC-1001", "sales_amt": 85000}],
                "_csv_filename": "july.csv",
                "_asked_period": {"label": "August 2026", "year_month": "2026-08"},
            },
        )
    )
    assert result["workflow_stage"] == "AWAITING_TABLE_CONFIRM"
    assert result["status"] == "IN_PROGRESS"
    assert result["backend_refs"]["csv_rows"][0]["lease_code"] == "LC-1001"


@pytest.mark.asyncio
async def test_period_mismatch_decline_returns_to_upload() -> None:
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    result = await sales_data_entry_node(
        _state(
            intent="UPLOAD_SALES_DATA",
            workflow_stage="AWAITING_PERIOD_MISMATCH_CONFIRM",
            action_override="cancel_period_mismatch",
            backend_refs={
                "asked_period": {
                    "label": "August 2026",
                    "year_month": "2026-08",
                },
                "csv_session_id": "csv-1",
                "csv_rows": [{"lease_code": "LC-1001"}],
                "period_mismatch": {"file_label": "July 2026"},
            },
        )
    )
    assert result["workflow_stage"] == "UPLOAD_INTENT"
    assert result["status"] == "WAITING_FOR_USER"
    assert result["backend_refs"]["asked_period"]["year_month"] == "2026-08"
    assert "csv_session_id" not in result["backend_refs"]
    assert result["response_ui"]["type"] == "sales_data_upload_prompt"
    assert "August 2026" in result["response_message"]


@pytest.mark.asyncio
async def test_new_csv_during_period_mismatch_revalidates() -> None:
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    result = await sales_data_entry_node(
        _state(
            intent="UPLOAD_SALES_DATA",
            workflow_stage="AWAITING_PERIOD_MISMATCH_CONFIRM",
            action_override="csv_uploaded",
            backend_refs={
                "asked_period": {"label": "August 2026", "year_month": "2026-08"},
            },
            corrected_fields={"csv_session_id": "csv-2"},
        )
    )
    assert result["workflow_stage"] == "CSV_LAYER1_VALIDATION"


@pytest.mark.asyncio
async def test_typed_amount_still_routes_to_manual_entry() -> None:
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    result = await sales_data_entry_node(
        _state(
            intent="UPLOAD_SALES_DATA",
            user_message="Sales for August for lease LC-1001 is SAR 85000",
        )
    )
    assert result["workflow_stage"] == "MANUAL_ENTRY"


@pytest.mark.asyncio
async def test_partial_lease_without_amount_goes_to_manual_entry() -> None:
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    result = await sales_data_entry_node(
        _state(
            intent="UPLOAD_SALES_DATA",
            user_message="Submit sales data for lease LC-1001",
        )
    )
    assert result["workflow_stage"] == "MANUAL_ENTRY"
    assert result["status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_explicit_csv_with_lease_stays_on_upload_prompt() -> None:
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    result = await sales_data_entry_node(
        _state(
            intent="UPLOAD_SALES_DATA",
            user_message="I want to upload a CSV for lease LC-1001",
        )
    )
    assert result["workflow_stage"] == "PENDING_MONTHS_CHECK"
    assert result["status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_other_months_reminder_stage_returns_to_gate() -> None:
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    result = await sales_data_entry_node(
        _state(
            intent="UPLOAD_SALES_DATA",
            workflow_stage="AWAITING_OTHER_MONTHS_REMINDER",
            user_message="proceed",
        )
    )
    assert result["workflow_stage"] == "AWAITING_OTHER_MONTHS_REMINDER"
    assert result["status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_pending_list_month_pick_stays_on_gate() -> None:
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    result = await sales_data_entry_node(
        _state(
            intent="UPLOAD_SALES_DATA",
            workflow_stage="AWAITING_PENDING_MONTHS_CHOICE",
            user_message="LC-1001 August 2026",
        )
    )
    assert result["workflow_stage"] == "AWAITING_PENDING_MONTHS_CHOICE"
    assert result["status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_supporting_document_routes_to_doc_flow() -> None:
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    result = await sales_data_entry_node(
        _state(
            intent="UPLOAD_SALES_DATA",
            user_message="I need to upload a supporting document for lease LC-1004",
        )
    )
    assert result["workflow_stage"] == "DOC_SELECT"
    assert result["status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_audited_sales_routes_to_doc_flow() -> None:
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    result = await sales_data_entry_node(
        _state(
            intent="UPLOAD_SALES_DATA",
            user_message="I need to upload audited sales for lease LC-1005",
        )
    )
    assert result["workflow_stage"] == "DOC_SELECT"


@pytest.mark.asyncio
async def test_cancel_supporting_doc_keeps_doc_flow() -> None:
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    result = await sales_data_entry_node(
        _state(
            intent="UPLOAD_SUPPORTING_DOCUMENT",
            action_override="cancel_supporting_doc",
            workflow_stage="AWAITING_DOC_CONFIRM",
            collected_data={"lease_code": "LC-1004", "document_id": "doc-1"},
        )
    )
    assert result["workflow_stage"] == "AWAITING_DOC_CONFIRM"
    assert result["status"] == "IN_PROGRESS"
    assert "collected_data" not in result
    assert result.get("intent") == "UPLOAD_SUPPORTING_DOCUMENT"


@pytest.mark.asyncio
async def test_upload_again_routes_to_doc_flow() -> None:
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    result = await sales_data_entry_node(
        _state(
            intent="UPLOAD_SALES_DATA",
            user_message="i want to upload again",
        )
    )
    assert result["workflow_stage"] == "DOC_SELECT"
    assert result["status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_status_question_still_looks_up_status() -> None:
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    result = await sales_data_entry_node(
        _state(
            intent="CHECK_SALES_DATA_STATUS",
            user_message="What data are pending?",
        )
    )
    assert result["workflow_stage"] == "STATUS_LOOKUP"
    assert result["status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_open_notification_seed_goes_to_pending_months() -> None:
    """Notification Agent open path: backend_refs.notification_context + UPLOAD."""
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    result = await sales_data_entry_node(
        _state(
            intent="UPLOAD_SALES_DATA",
            user_message="",
            backend_refs={
                "opened_from_notification": True,
                "notification_context": {
                    "lease_code": "LC-1001",
                    "period_hint": "month August",
                    "source": "notification",
                },
            },
            corrected_fields={
                "notification_context": {
                    "lease_code": "LC-1001",
                    "period_hint": "month August",
                    "source": "notification",
                },
            },
        )
    )
    assert result["workflow_stage"] == "PENDING_MONTHS_CHECK"
    assert result["status"] == "IN_PROGRESS"
