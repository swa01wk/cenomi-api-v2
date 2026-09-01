"""Supporting Document Submission Node.

PATCH /api/v1/sales-data with document_id + mapped tenant response status
(RE_UPLOADED / UPLOADED_SUPPORTING_DOCUMENT / UPLOADED_AUDITED_SALES).
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.sales_data_schema import ASK_UPLOAD_LABELS, STATUS_LABELS, TENANT_RESPONSE_STATUS
from app.agents.services.sales_data_api_client import SalesDataApiClient
from app.core.request_context import get_current_bearer_token
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)


@trace_node("supporting_doc_submission", "AGENT")
async def supporting_doc_submission_node(state: ServiceRequestState) -> dict[str, Any]:
    """PATCH the selected row after a successful PUT /files."""
    collected: dict = dict(state.get("collected_data") or {})

    lease_id = collected.get("lease_id")
    lease_code = collected.get("lease_code", "")
    document_id = collected.get("document_id")
    current_status = collected.get("current_status") or "NEED_REUPLOAD"
    new_status = collected.get("target_status") or TENANT_RESPONSE_STATUS.get(
        current_status, "RE_UPLOADED"
    )
    start = collected.get("sales_period_start", "")
    end = collected.get("sales_period_end", "")
    comment = (collected.get("comment_text") or "").strip()

    if not lease_id or not document_id:
        return {
            "status": "WAITING_FOR_USER",
            "workflow_stage": "AWAITING_DOC_UPLOAD",
            "response_message": (
                "Missing lease or document ID. Please attach the file again."
            ),
        }

    if not comment:
        return {
            "status": "WAITING_FOR_USER",
            "workflow_stage": "AWAITING_DOC_CONFIRM",
            "collected_data": collected,
            "response_message": (
                "A **comment is required** before I can submit this file. "
                "Add a short note and confirm again."
            ),
        }

    payload: dict[str, Any] = {
        "lease_id": lease_id,
        "lease_code": lease_code,
        "sales_period_start": start,
        "sales_period_end": end,
        "status": new_status,
        "document_id": document_id,
        "comment_text": comment,
    }
    if collected.get("sales_amt") is not None:
        payload["sales_amt"] = collected["sales_amt"]

    token = get_current_bearer_token()
    client = SalesDataApiClient(bearer_token=token)

    try:
        result = await client.update_sales_data_status(payload)
    except Exception as exc:
        log.error("supporting_doc_submission.failed", exc_info=True)
        return {
            "status": "WAITING_FOR_USER",
            "workflow_stage": "AWAITING_DOC_CONFIRM",
            "response_message": f"⚠️ Update failed: {exc}. Please try again.",
        }

    if not result.get("success"):
        return {
            "status": "WAITING_FOR_USER",
            "workflow_stage": "AWAITING_DOC_CONFIRM",
            "response_message": (
                f"⚠️ Update failed: {result.get('error', 'Unknown error')}."
            ),
        }

    label = STATUS_LABELS.get(new_status, new_status)
    ask_label = ASK_UPLOAD_LABELS.get(current_status, "document")
    message = (
        f"✅ **{ask_label}** uploaded for lease **{lease_code}**.\n\n"
        f"Status is now **{label}**. The Sales Data team has been notified."
    )
    log.info(
        "supporting_doc_submission.success",
        lease_code=lease_code,
        status=new_status,
        document_id=document_id,
    )
    return {
        "workflow_stage": "SALES_DATA_COMPLETE",
        "status": "COMPLETED",
        "confirmation_status": None,
        "collected_data": {},
        "response_message": message,
        "response_ui": {
            "type": "supporting_doc_success",
            "lease_code": lease_code,
            "status": new_status,
            "document_id": document_id,
            "filename": collected.get("filename"),
            "ask_label": ask_label,
            "message": message,
        },
    }
