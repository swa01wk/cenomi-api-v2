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
    _document_id_from_upload,
    _requested_need_status,
    supporting_doc_upload_node,
)
from app.agents.graph.nodes.supervisor_node import _sales_data_shortcut
from app.api.routes.supporting_doc_upload import get_uploaded_doc, store_uploaded_doc


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


def test_upload_again_shortcuts_to_sales_upload_not_supporting_document() -> None:
    result = _sales_data_shortcut(
        "i want to upload again",
        ["TENANT_CSV_UPLOADER"],
    )
    assert result is not None
    assert result["intent"] == "UPLOAD_SALES_DATA"


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
    # Softened from the blunt "A comment is required" — the note is still
    # required at submit time, the prompt just says so more gently.
    assert "short note" in result["response_message"].lower()
    # Card title/button must name THIS request, not a generic "Attach".
    assert ui["title"] == "Supporting document"
    assert ui["cta"] == "Upload supporting document"
    # CSV is accepted for every ask-type — POS/finance exports are as common
    # as PDFs as supporting evidence.
    assert ".csv" in ui["accept"]


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
    assert ui["title"] == "Audited sales data"
    assert ui["cta"] == "Upload audited sales"
    assert ".csv" in ui["accept"]


@pytest.mark.asyncio
async def test_reupload_prompt_uses_portal_label_and_accepts_csv() -> None:
    """NEED_REUPLOAD asks for a corrected sales data FILE — the same CSV the
    tenant originally uploaded — so its card must say so and its attach
    control must take .csv, unlike the two document-only ask-types above."""
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
            _state(user_message="I need to re-upload sales data for lease LC-1003")
        )

    ui = result["response_ui"]
    assert ui["lease_code"] == "LC-1003"
    assert ui["current_status"] == "NEED_REUPLOAD"
    assert ui["target_status"] == "RE_UPLOADED"
    assert ui["ask_label"] == "Sales data"
    assert ui["title"] == "Sales data"
    assert ui["cta"] == "Re-upload sales data"
    assert ".csv" in ui["accept"]


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
async def test_put_files_failure_restores_upload_prompt_and_drops_cache() -> None:
    """PUT /files raising must not leave the user with no way to retry.

    Regression guard for the bug where a failed upload left
    ``response_ui`` unset (defaulting to a bare ``{"type": "message"}`` with
    no ``show_file_upload``) and silently kept the same rejected bytes
    cached, so a "silent refresh" turn would just resubmit the identical
    payload and fail again forever.
    """
    doc_session_id = "sess-doc-fail-1"
    store_uploaded_doc(doc_session_id, "pos.pdf", "application/pdf", b"%PDF-1.4 fake bytes")

    client = AsyncMock()
    client.upload_file = AsyncMock(side_effect=RuntimeError("connection reset"))
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
            _state(
                collected_data={
                    "lease_id": 6,
                    "lease_code": "LC-1004",
                    "current_status": "NEED_UPLOAD_SUPPORTING_DOCUMENT",
                    "sales_period_start": "2026-07-01",
                    "sales_period_end": "2026-07-31",
                    "sales_amt": 72000.0,
                },
                backend_refs={"doc_session_id": doc_session_id},
            )
        )

    assert result["workflow_stage"] == "AWAITING_DOC_UPLOAD"
    assert result["status"] == "WAITING_FOR_USER"
    ui = result["response_ui"]
    assert ui["type"] == "supporting_doc_prompt"
    assert ui["show_file_upload"] is True
    assert ui["lease_code"] == "LC-1004"
    assert "upload failed" in result["response_message"].lower()
    # Retry must require a fresh attach, not silently resend the rejected bytes.
    assert get_uploaded_doc(doc_session_id) is None
    assert "doc_session_id" not in (result.get("backend_refs") or {})
    assert "document_id" not in result["collected_data"]


@pytest.mark.asyncio
async def test_missing_document_id_restores_upload_prompt() -> None:
    """A 200 response from /files with no document_id must also offer a retry."""
    doc_session_id = "sess-doc-fail-2"
    store_uploaded_doc(doc_session_id, "audit.pdf", "application/pdf", b"%PDF-1.4 fake bytes")

    client = AsyncMock()
    client.upload_file = AsyncMock(return_value={"size_bytes": 123})  # no document_id
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
            _state(
                collected_data={
                    "lease_id": 7,
                    "lease_code": "LC-1005",
                    "current_status": "NEED_UPLOAD_AUDITED_SALES",
                    "sales_period_start": "2026-07-01",
                    "sales_period_end": "2026-07-31",
                    "sales_amt": 185000.0,
                },
                backend_refs={"doc_session_id": doc_session_id},
            )
        )

    ui = result["response_ui"]
    assert ui["type"] == "supporting_doc_prompt"
    assert ui["show_file_upload"] is True
    assert get_uploaded_doc(doc_session_id) is None


@pytest.mark.asyncio
async def test_live_files_envelope_is_accepted_as_document_id() -> None:
    """PUT /files returns {success, data.document_id}; the node must not miss it."""
    doc_session_id = "sess-doc-ok-envelope"
    store_uploaded_doc(doc_session_id, "pos.pdf", "application/pdf", b"%PDF-1.4 fake bytes")

    client = AsyncMock()
    client.upload_file = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "document_id": "live-doc-uuid",
                "file_path": "common-file-upload/sales-data/1/pos.pdf",
            },
        }
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
        result = await supporting_doc_upload_node(
            _state(
                collected_data={
                    "lease_id": 6,
                    "lease_code": "t0106037",
                    "current_status": "NEED_UPLOAD_SUPPORTING_DOCUMENT",
                    "sales_period_start": "2026-07-01",
                    "sales_period_end": "2026-07-31",
                    "sales_amt": 72000.0,
                },
                backend_refs={"doc_session_id": doc_session_id},
            )
        )

    assert result["workflow_stage"] == "AWAITING_DOC_CONFIRM"
    assert result["collected_data"]["document_id"] == "live-doc-uuid"
    client.upload_file.assert_awaited()
    kwargs = client.upload_file.await_args.kwargs
    assert kwargs["lease_code"] == "t0106037"
    assert kwargs["document_type_id"] == "SD_SUP_DOC"


def test_document_id_from_upload_shapes() -> None:
    assert _document_id_from_upload({"document_id": "flat"}) == "flat"
    assert _document_id_from_upload({"success": True, "data": {"document_id": "nested"}}) == "nested"
    assert _document_id_from_upload({"success": True, "data": {}}) is None
    assert _document_id_from_upload({"size_bytes": 123}) is None


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
    assert result["response_ui"]["action_label"] == "Confirm"


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
    assert result["response_ui"]["action_label"] == "Confirm"


@pytest.mark.asyncio
async def test_admin_ui_chosen_action_beats_text_sniffing() -> None:
    """The review table's per-row 'Ask upload' menu now lets the admin pick
    Reupload / Supporting Document / Audited Sales explicitly (mirroring the
    classic portal's 3-way radio choice) — sent as corrected_fields.admin_action
    alongside the same "Request reupload for X" message the button already
    sends. That explicit choice must win even if the accompanying message text
    would otherwise sniff a different action via _deterministic_action.
    """
    client = AsyncMock()
    client.get_sales_data = AsyncMock(
        return_value=[
            {
                "lease_id": 8,
                "lease_code": "LC-3001",
                "status": "SUBMITTED",
                "sales_period_start": "2026-07-01",
                "sales_period_end": "2026-07-31",
                "sales_amt": 41000.0,
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
                "session_id": "sess-admin-ui-choice",
                "user_id": "admin-001",
                "user_message": "Ask upload for LC-3001",
                "intent": "APPROVE_SALES_DATA",
                "action_override": None,
                "workflow_stage": "ADMIN_REVIEW",
                "backend_refs": {},
                "collected_data": {},
                "corrected_fields": {"admin_action": "NEED_UPLOAD_AUDITED_SALES"},
            }
        )

    assert result["collected_data"]["admin_action"] == "NEED_UPLOAD_AUDITED_SALES"
    assert result["response_ui"]["action_label"] == "Confirm"


@pytest.mark.asyncio
async def test_admin_ui_chosen_action_ignores_invalid_value() -> None:
    """A garbage/unexpected corrected_fields.admin_action must not silently
    become the action — it should fall back to text-sniffing as before."""
    client = AsyncMock()
    client.get_sales_data = AsyncMock(
        return_value=[
            {
                "lease_id": 9,
                "lease_code": "LC-3002",
                "status": "SUBMITTED",
                "sales_period_start": "2026-07-01",
                "sales_period_end": "2026-07-31",
                "sales_amt": 8000.0,
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
                "session_id": "sess-admin-ui-bad-choice",
                "user_id": "admin-001",
                "user_message": "Approve LC-3002",
                "intent": "APPROVE_SALES_DATA",
                "action_override": None,
                "workflow_stage": "ADMIN_REVIEW",
                "backend_refs": {},
                "collected_data": {},
                "corrected_fields": {"admin_action": "NOT_A_REAL_STATUS"},
            }
        )

    assert result["collected_data"]["admin_action"] == "APPROVED"


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


@pytest.mark.asyncio
async def test_resolved_notification_reports_its_own_lease_not_others() -> None:
    """Opening a re-upload notification for T0098038 whose August request was
    since approved must say so — not list another lease's re-upload request
    (t0042757) as if the notification were about that one."""
    rows = [
        {
            "lease_id": 11,
            "lease_code": "t0098038",
            "status": "APPROVED",
            "sales_period_start": "2026-08-01",
            "sales_period_end": "2026-08-31",
        },
        {
            "lease_id": 12,
            "lease_code": "t0042757",
            "status": "NEED_REUPLOAD",
            "sales_period_start": "2026-08-01",
            "sales_period_end": "2026-08-31",
        },
    ]
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=rows)
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
            _state(
                corrected_fields={
                    "notification_context": {
                        "lease_code": "T0098038",
                        "period_hint": "month August",
                        "source": "notification",
                    }
                }
            )
        )

    assert result["response_ui"]["type"] == "stale_notification"
    assert "t0098038" in result["response_message"].lower()
    assert "Approved" in result["response_message"]
    assert "t0042757" not in result["response_message"]


@pytest.mark.asyncio
async def test_notification_open_targets_only_its_own_month_and_request() -> None:
    """September supporting-doc notification must not also list August's re-upload."""
    from app.agents.graph.nodes.sales_data.supporting_doc_upload_node import (
        supporting_doc_upload_node,
    )

    rows = [
        {
            "lease_id": 21,
            "lease_code": "t0106013",
            "status": "NEED_UPLOAD_SUPPORTING_DOCUMENT",
            "sales_period_start": "2026-09-01",
            "sales_period_end": "2026-09-30",
        },
        {
            "lease_id": 21,
            "lease_code": "t0106013",
            "status": "NEED_REUPLOAD",
            "sales_period_start": "2026-08-01",
            "sales_period_end": "2026-08-31",
        },
    ]
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=rows)
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
            _state(
                corrected_fields={
                    "notification_context": {
                        "lease_code": "t0106013",
                        "period_hint": "",
                        "period_date": "2026-09",
                        "key": "SALES_DATA_NEED_UPLOAD_SUPPORTING_DOCUMENT",
                        "source": "notification",
                    }
                }
            )
        )

    collected = result.get("collected_data") or {}
    assert collected.get("sales_period_start") == "2026-09-01"
    assert collected.get("current_status") == "NEED_UPLOAD_SUPPORTING_DOCUMENT"
    assert "2026-08-01" not in (result.get("response_message") or "")


@pytest.mark.asyncio
async def test_resolved_notification_uses_the_named_month_in_any_year() -> None:
    """ "for the month December" must report December 2024, not the latest row."""
    from app.agents.graph.nodes.sales_data.supporting_doc_upload_node import (
        supporting_doc_upload_node,
    )

    rows = [
        {"lease_id": 31, "lease_code": "t0106038", "status": "UPLOADED_AUDITED_SALES",
         "sales_period_start": "2024-12-01", "sales_period_end": "2024-12-31"},
        {"lease_id": 31, "lease_code": "t0106038", "status": "SUBMITTED",
         "sales_period_start": "2026-05-01", "sales_period_end": "2026-05-31"},
    ]
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=rows)
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
            _state(
                corrected_fields={
                    "notification_context": {
                        "lease_code": "t0106038",
                        "period_hint": "month December",
                        "period_date": "2026-12",
                        "key": "SALES_DATA_NEED_UPLOAD_AUDITED_SALES",
                        "source": "notification",
                    }
                }
            )
        )

    message = result["response_message"]
    assert "December 2024" in message
    assert "May 2026" not in message


@pytest.mark.asyncio
async def test_reupload_accepts_a_typed_amount_instead_of_a_file() -> None:
    """NEED_REUPLOAD asks for corrected sales data, so a typed figure is a
    complete answer — the tenant should not have to produce a file just to
    fix a number. Previously this fell through to re-rendering the attach
    prompt, which tripped the repeat guard and came back as "I'm not sure
    what you would like to do next"."""
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    result = await sales_data_entry_node({
        "workflow_stage": "AWAITING_DOC_UPLOAD",
        "intent": "UPLOAD_SUPPORTING_DOCUMENT",
        "action_override": None,
        "user_message": "the corrected amount is 303021",
        "collected_data": {
            "current_status": "NEED_REUPLOAD",
            "lease_code": "t0094842",
        },
        "backend_refs": {},
        "corrected_fields": {},
    })

    assert result["workflow_stage"] == "AWAITING_DOC_CONFIRM"
    assert result["collected_data"]["sales_amt"] == 303021.0


@pytest.mark.asyncio
async def test_evidence_ask_types_still_require_a_file() -> None:
    """A supporting document / audited sales request is for evidence, so a
    typed amount must not short-circuit it."""
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    for status in ("NEED_UPLOAD_SUPPORTING_DOCUMENT", "NEED_UPLOAD_AUDITED_SALES"):
        result = await sales_data_entry_node({
            "workflow_stage": "AWAITING_DOC_UPLOAD",
            "intent": "UPLOAD_SUPPORTING_DOCUMENT",
            "action_override": None,
            "user_message": "the corrected amount is 303021",
            "collected_data": {"current_status": status, "lease_code": "t0094842"},
            "backend_refs": {},
            "corrected_fields": {},
        })
        assert result["workflow_stage"] == "AWAITING_DOC_UPLOAD", status


@pytest.mark.asyncio
async def test_amount_only_reupload_submits_without_a_document_id() -> None:
    from app.agents.graph.nodes.sales_data.supporting_doc_submission_node import (
        supporting_doc_submission_node,
    )

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
        result = await supporting_doc_submission_node({
            "collected_data": {
                "lease_id": 7,
                "lease_code": "t0094842",
                "current_status": "NEED_REUPLOAD",
                "target_status": "RE_UPLOADED",
                "sales_period_start": "2025-08-01",
                "sales_period_end": "2025-08-31",
                "sales_amt": 303021.0,
                "comment_text": "Corrected figure",
            }
        })

    assert result["status"] == "COMPLETED"
    payload = client.update_sales_data_status.await_args.args[0]
    assert payload["sales_amt"] == 303021.0
    # Not sent at all, rather than sent as null.
    assert "document_id" not in payload
