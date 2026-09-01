"""Tenant supporting-document / audited-sales upload and admin Ask upload."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.graph.nodes.sales_data.admin_review_entry_node import (
    _deterministic_action,
    admin_review_entry_node,
)
from app.agents.graph.nodes.sales_data.supporting_doc_confirmation_node import (
    supporting_doc_confirmation_node,
)
from app.agents.graph.nodes.sales_data.supporting_doc_submission_node import (
    supporting_doc_submission_node,
)
from app.agents.graph.nodes.sales_data.supporting_doc_upload_node import (
    _requested_need_status,
    supporting_doc_upload_node,
)
from app.agents.graph.nodes.supervisor_node import _sales_data_shortcut


_NEED_ROWS = [
    {
        "lease_id": 3,
        "lease_code": "LC-1003",
        "status": "NEED_REUPLOAD",
        "sales_period_start": "2026-08-01",
        "sales_period_end": "2026-08-31",
        "sales_amt": 50000.0,
        "comment_text": "Please re-upload with correct figures.",
    },
    {
        "lease_id": 6,
        "lease_code": "LC-1004",
        "status": "NEED_UPLOAD_SUPPORTING_DOCUMENT",
        "sales_period_start": "2026-07-01",
        "sales_period_end": "2026-07-31",
        "sales_amt": 72000.0,
        "comment_text": "Please attach the POS report for July.",
    },
    {
        "lease_id": 7,
        "lease_code": "LC-1005",
        "status": "NEED_UPLOAD_AUDITED_SALES",
        "sales_period_start": "2026-07-01",
        "sales_period_end": "2026-07-31",
        "sales_amt": 185000.0,
        "comment_text": "Please upload audited sales figures.",
    },
]


def _state(**overrides: Any) -> dict[str, Any]:
    return {
        "session_id": "sess-doc-001",
        "user_id": "tenant-001",
        "user_message": "",
        "intent": "UPLOAD_SUPPORTING_DOCUMENT",
        "action_override": None,
        "workflow_stage": None,
        "backend_refs": {},
        "collected_data": {},
        "corrected_fields": {},
        **overrides,
    }


def test_requested_need_status_maps_portal_ask_types() -> None:
    assert _requested_need_status("upload supporting document") == (
        "NEED_UPLOAD_SUPPORTING_DOCUMENT"
    )
    assert _requested_need_status("I need to upload audited sales") == (
        "NEED_UPLOAD_AUDITED_SALES"
    )
    assert _requested_need_status("re-upload a PDF for LC-1003") == "NEED_REUPLOAD"
    assert _requested_need_status("attach extra doc") == (
        "NEED_UPLOAD_SUPPORTING_DOCUMENT"
    )


@pytest.mark.asyncio
async def test_cancel_supporting_doc_restores_pdf_prompt() -> None:
    result = await supporting_doc_confirmation_node(
        _state(
            action_override="cancel_supporting_doc",
            workflow_stage="AWAITING_DOC_CONFIRM",
            collected_data={
                "lease_id": 6,
                "lease_code": "LC-1004",
                "document_id": "doc-1",
                "filename": "pos.pdf",
                "current_status": "NEED_UPLOAD_SUPPORTING_DOCUMENT",
                "target_status": "UPLOADED_SUPPORTING_DOCUMENT",
                "sales_period_start": "2026-07-01",
                "sales_period_end": "2026-07-31",
                "sales_amt": 72000.0,
            },
            backend_refs={"doc_session_id": "sess-file-1"},
        )
    )
    assert result["workflow_stage"] == "AWAITING_DOC_UPLOAD"
    assert result["response_ui"]["type"] == "supporting_doc_prompt"
    assert result["response_ui"]["show_file_upload"] is True
    assert result["response_ui"]["lease_code"] == "LC-1004"
    assert "document_id" not in result["collected_data"]
    assert "doc_session_id" not in (result.get("backend_refs") or {})


def test_upload_again_shortcuts_to_supporting_document() -> None:
    result = _sales_data_shortcut(
        "i want to upload again",
        ["TENANT_CSV_UPLOADER"],
    )
    assert result is not None
    assert result["intent"] == "UPLOAD_SUPPORTING_DOCUMENT"


def test_tenant_doc_shortcut_beats_csv_upload_phrase() -> None:
    result = _sales_data_shortcut(
        "I want to upload a supporting document for lease LC-1004",
        ["TENANT_CSV_UPLOADER"],
    )
    assert result is not None
    assert result["intent"] == "UPLOAD_SUPPORTING_DOCUMENT"

    audited = _sales_data_shortcut(
        "I want to upload audited sales for LC-1005",
        ["TENANT_CSV_UPLOADER"],
    )
    assert audited is not None
    assert audited["intent"] == "UPLOAD_SUPPORTING_DOCUMENT"


def test_admin_deterministic_ask_upload_actions() -> None:
    assert (
        _deterministic_action("request supporting document for LC-2001")
        == "NEED_UPLOAD_SUPPORTING_DOCUMENT"
    )
    assert (
        _deterministic_action("request audited sales for LC-2002")
        == "NEED_UPLOAD_AUDITED_SALES"
    )
    assert _deterministic_action("request reupload for LC-2001") == "NEED_REUPLOAD"


@pytest.mark.asyncio
async def test_supporting_doc_prompt_uses_portal_label() -> None:
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=_NEED_ROWS)
    with (
        patch(
            "app.agents.graph.nodes.sales_data.supporting_doc_upload_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.supporting_doc_upload_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await supporting_doc_upload_node(
            _state(user_message="I need to upload a supporting document for lease LC-1004")
        )

    assert result["workflow_stage"] == "AWAITING_DOC_UPLOAD"
    ui = result["response_ui"]
    assert ui["type"] == "supporting_doc_prompt"
    assert ui["lease_code"] == "LC-1004"
    assert ui["current_status"] == "NEED_UPLOAD_SUPPORTING_DOCUMENT"
    assert ui["target_status"] == "UPLOADED_SUPPORTING_DOCUMENT"
    assert ui["ask_label"] == "Supporting document"
    assert "supporting document" in result["response_message"].lower()
    assert "comment is required" in result["response_message"].lower()


@pytest.mark.asyncio
async def test_audited_sales_prompt_uses_portal_label() -> None:
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=_NEED_ROWS)
    with (
        patch(
            "app.agents.graph.nodes.sales_data.supporting_doc_upload_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.supporting_doc_upload_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await supporting_doc_upload_node(
            _state(user_message="I need to upload audited sales for lease LC-1005")
        )

    ui = result["response_ui"]
    assert ui["lease_code"] == "LC-1005"
    assert ui["current_status"] == "NEED_UPLOAD_AUDITED_SALES"
    assert ui["target_status"] == "UPLOADED_AUDITED_SALES"
    assert ui["ask_label"] == "Audited sales data"


@pytest.mark.asyncio
async def test_kind_filter_picks_supporting_when_no_lease() -> None:
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=_NEED_ROWS)
    with (
        patch(
            "app.agents.graph.nodes.sales_data.supporting_doc_upload_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.supporting_doc_upload_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await supporting_doc_upload_node(
            _state(user_message="I need to upload a supporting document")
        )

    assert result["collected_data"]["lease_code"] == "LC-1004"
    assert result["response_ui"]["current_status"] == "NEED_UPLOAD_SUPPORTING_DOCUMENT"


@pytest.mark.asyncio
async def test_confirm_requires_comment() -> None:
    result = await supporting_doc_confirmation_node(
        _state(
            action_override="confirm_supporting_doc",
            workflow_stage="AWAITING_DOC_CONFIRM",
            collected_data={
                "lease_id": 6,
                "lease_code": "LC-1004",
                "document_id": "doc-1",
                "filename": "pos.pdf",
                "current_status": "NEED_UPLOAD_SUPPORTING_DOCUMENT",
                "target_status": "UPLOADED_SUPPORTING_DOCUMENT",
                "sales_period_start": "2026-07-01",
                "sales_period_end": "2026-07-31",
                "sales_amt": 72000.0,
            },
        )
    )
    assert result["workflow_stage"] == "AWAITING_DOC_CONFIRM"
    assert result["confirmation_status"] == "PENDING"
    assert "comment is required" in result["response_message"].lower()


@pytest.mark.asyncio
async def test_confirm_with_comment_and_amount_proceeds() -> None:
    result = await supporting_doc_confirmation_node(
        _state(
            action_override="confirm_supporting_doc",
            workflow_stage="AWAITING_DOC_CONFIRM",
            collected_data={
                "lease_id": 6,
                "lease_code": "LC-1004",
                "document_id": "doc-1",
                "filename": "pos.pdf",
                "current_status": "NEED_UPLOAD_SUPPORTING_DOCUMENT",
                "target_status": "UPLOADED_SUPPORTING_DOCUMENT",
                "sales_period_start": "2026-07-01",
                "sales_period_end": "2026-07-31",
                "sales_amt": 72000.0,
            },
            corrected_fields={"comment_text": "POS report attached", "sales_amt": 73000},
        )
    )
    assert result["workflow_stage"] == "DOC_SUBMITTING"
    assert result["confirmation_status"] == "CONFIRMED"
    assert result["collected_data"]["comment_text"] == "POS report attached"
    assert result["collected_data"]["sales_amt"] == 73000.0


@pytest.mark.asyncio
async def test_submission_patches_mapped_status_comment_and_amount() -> None:
    client = AsyncMock()
    client.update_sales_data_status = AsyncMock(return_value={"success": True})
    with (
        patch(
            "app.agents.graph.nodes.sales_data.supporting_doc_submission_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.supporting_doc_submission_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await supporting_doc_submission_node(
            _state(
                collected_data={
                    "lease_id": 7,
                    "lease_code": "LC-1005",
                    "document_id": "doc-aud",
                    "filename": "audit.pdf",
                    "current_status": "NEED_UPLOAD_AUDITED_SALES",
                    "target_status": "UPLOADED_AUDITED_SALES",
                    "sales_period_start": "2026-07-01",
                    "sales_period_end": "2026-07-31",
                    "sales_amt": 190000.0,
                    "comment_text": "Audited pack attached",
                }
            )
        )

    payload = client.update_sales_data_status.await_args.args[0]
    assert payload["status"] == "UPLOADED_AUDITED_SALES"
    assert payload["comment_text"] == "Audited pack attached"
    assert payload["sales_amt"] == 190000.0
    assert payload["document_id"] == "doc-aud"
    assert result["response_ui"]["type"] == "supporting_doc_success"
    assert result["response_ui"]["ask_label"] == "Audited sales data"


@pytest.mark.asyncio
async def test_admin_request_supporting_document_confirms_ask_upload() -> None:
    client = AsyncMock()
    client.get_sales_data = AsyncMock(
        return_value=[
            {
                "lease_id": 4,
                "lease_code": "LC-2001",
                "status": "SUBMITTED",
                "sales_period_start": "2026-07-01",
                "sales_period_end": "2026-07-31",
                "sales_amt": 320000.0,
            }
        ]
    )
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
            {
                "session_id": "sess-admin-doc",
                "user_id": "admin-001",
                "user_message": "request supporting document for LC-2001",
                "intent": "APPROVE_SALES_DATA",
                "action_override": None,
                "workflow_stage": "ADMIN_REVIEW",
                "backend_refs": {},
                "collected_data": {},
            }
        )

    assert result["workflow_stage"] == "ADMIN_CONFIRM"
    assert result["collected_data"]["admin_action"] == "NEED_UPLOAD_SUPPORTING_DOCUMENT"
    assert result["response_ui"]["action_label"] == "Ask upload — Supporting document"


@pytest.mark.asyncio
async def test_admin_request_audited_sales_confirms_ask_upload() -> None:
    client = AsyncMock()
    client.get_sales_data = AsyncMock(
        return_value=[
            {
                "lease_id": 5,
                "lease_code": "LC-2002",
                "status": "SUBMITTED",
                "sales_period_start": "2026-07-01",
                "sales_period_end": "2026-07-31",
                "sales_amt": 95000.0,
            }
        ]
    )
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
            {
                "session_id": "sess-admin-aud",
                "user_id": "admin-001",
                "user_message": "request audited sales for LC-2002",
                "intent": "APPROVE_SALES_DATA",
                "action_override": None,
                "workflow_stage": "ADMIN_REVIEW",
                "backend_refs": {},
                "collected_data": {},
            }
        )

    assert result["collected_data"]["admin_action"] == "NEED_UPLOAD_AUDITED_SALES"
    assert result["response_ui"]["action_label"] == "Ask upload — Audited sales data"


@pytest.mark.asyncio
async def test_supporting_doc_same_lease_two_months_lists_both() -> None:
    client = AsyncMock()
    client.get_sales_data = AsyncMock(
        return_value=[
            {
                "lease_id": 1,
                "lease_code": "LC-1001",
                "status": "NEED_UPLOAD_SUPPORTING_DOCUMENT",
                "sales_period_start": "2026-08-01",
                "sales_period_end": "2026-08-31",
                "sales_amt": None,
            },
            {
                "lease_id": 1,
                "lease_code": "LC-1001",
                "status": "NEED_UPLOAD_SUPPORTING_DOCUMENT",
                "sales_period_start": "2026-07-01",
                "sales_period_end": "2026-07-31",
                "sales_amt": 4000.0,
            },
        ]
    )
    with (
        patch(
            "app.agents.graph.nodes.sales_data.supporting_doc_upload_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.supporting_doc_upload_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        listed = await supporting_doc_upload_node(
            _state(user_message="I need to upload a supporting document for lease LC-1001")
        )
        picked = await supporting_doc_upload_node(
            _state(
                user_message=(
                    "I need to upload a supporting document for lease LC-1001 "
                    "(2026-07-01 to 2026-07-31)."
                )
            )
        )

    assert listed["workflow_stage"] == "DOC_SELECT"
    assert len(listed["response_ui"]["rows"]) == 2
    assert picked["workflow_stage"] == "AWAITING_DOC_UPLOAD"
    assert picked["collected_data"]["sales_period_start"] == "2026-07-01"
    assert picked["collected_data"]["sales_amt"] == 4000.0
