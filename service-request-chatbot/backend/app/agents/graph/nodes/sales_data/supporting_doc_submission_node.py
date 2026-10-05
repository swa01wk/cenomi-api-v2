"""Supporting Document Submission Node.

PATCH /api/v1/sales-data with document_id + mapped tenant response status
(RE_UPLOADED / UPLOADED_SUPPORTING_DOCUMENT / UPLOADED_AUDITED_SALES).
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.graph.nodes.sales_data.period_utils import (
    format_year_month,
    year_month_from_start,
)
from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.sales_data_schema import ASK_UPLOAD_LABELS, STATUS_LABELS, TENANT_RESPONSE_STATUS
from app.agents.services.permission_service import graph_permission_guard
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
        message = "Missing lease or document ID. Please attach the file again."
        return {
            "status": "WAITING_FOR_USER",
            "workflow_stage": "AWAITING_DOC_UPLOAD",
            "response_message": message,
            # Every branch below needs its own verbatim response_ui — without
            # one at all, response_generation's LLM rewrite has nothing to
            # anchor on and is free to invent unrelated specifics (observed:
            # a fabricated "sales period date format" issue out of what was
            # really a missing comment_text).
            "response_ui": {"type": "message", "message": message, "verbatim": True},
        }

    if not comment:
        message = (
            "A **comment is required** before I can submit this file. "
            "Add a short note and confirm again."
        )
        return {
            "status": "WAITING_FOR_USER",
            "workflow_stage": "AWAITING_DOC_CONFIRM",
            "collected_data": collected,
            "response_message": message,
            "response_ui": {"type": "message", "message": message, "verbatim": True},
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

    denied = graph_permission_guard(state, "SUBMIT_SALES_DATA_SUPPORTING_DOC")
    if denied:
        log.warning("supporting_doc_submission.permission_denied")
        return denied

    token = get_current_bearer_token()
    client = SalesDataApiClient(bearer_token=token)

    try:
        result = await client.update_sales_data_status(payload)
    except Exception as exc:
        log.error("supporting_doc_submission.failed", exc_info=True)
        message = f"Update failed: {exc}. Please try again."
        return {
            "status": "WAITING_FOR_USER",
            "workflow_stage": "AWAITING_DOC_CONFIRM",
            "response_message": message,
            "response_ui": {"type": "message", "message": message, "verbatim": True},
        }

    if not result.get("success"):
        message = f"Update failed: {result.get('error', 'Unknown error')}."
        return {
            "status": "WAITING_FOR_USER",
            "workflow_stage": "AWAITING_DOC_CONFIRM",
            "response_message": message,
            "response_ui": {"type": "message", "message": message, "verbatim": True},
        }

    label = STATUS_LABELS.get(new_status, new_status)
    ask_label = ASK_UPLOAD_LABELS.get(current_status, "document")
    ym = year_month_from_start(str(collected.get("sales_period_start") or ""))
    period = f" ({format_year_month(ym)})" if ym else ""
    message = (
        f"Your {ask_label[:1].lower()}{ask_label[1:]} for lease **{lease_code}**{period} "
        "has been submitted for review — the Sales Data Team has been notified."
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
            "verbatim": True,
            "lease_code": lease_code,
            "status": new_status,
            "document_id": document_id,
            "filename": collected.get("filename"),
            "ask_label": ask_label,
            "message": message,
        },
    }
