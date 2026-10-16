"""PATCH a confirmed level-1 / level-2 ceiling inspection decision.

Payload
-------
The platform payload captured by status sync (``backend_refs.create_payload``)
is sent back with:
* ``documents_ids`` — every document on the SR,
* ``document_status_map`` — the draft report with the reviewer's status
  (APPROVED / APPROVED_WITH_CHANGES), any rejected report as DISAPPROVED,
  everything else with an empty status,
* top-level ``status`` — APPROVED, or ACTION_REQUIRED for "ask for more
  information", and ``comment`` — the reviewer's comment.

The ``document_status_map`` entry shape follows the handover RDD entries. PRD
open point 2: confirm it against one real web-form submission before UAT.

Outcomes (cenomi-api service_request_usecase.go)
------------------------------------------------
* level 1 APPROVED → SR stays IN_PROCESS, FM Manager is next.
* level 2 APPROVED → SR becomes REPORT_SUBMITTED; the tenant acknowledges next.
* ACTION_REQUIRED → back to the requester; the same level resumes afterwards.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.nodes.ceiling.ceiling_review_entry_node import REVIEW_KEY
from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.ceiling_schema import (
    DOC_STATUS_APPROVED,
    DOC_STATUS_DISAPPROVED,
    REVIEW_ACTION_APPROVE,
    REVIEW_ACTION_MORE_INFO,
    REVIEW_ACTION_PLATFORM_STATUS,
    SERVICE_CATEGORY,
    STAGE_L1_REVIEW,
    STAGE_L2_REVIEW,
    STAGE_REVIEW_SUBMITTED,
    draft_report_doc_type,
    rejected_report_doc_type,
    sub_category_label,
)
from app.agents.services.deleted_documents import deleted_document_ids, without_deleted_ids
from app.agents.services.service_request_api_service import get_service_request_api_service
from app.observability.analytics_constants import FLOW_CEILING_INSPECTION, TOOL_TYPE_SR_PATCH
from app.observability.decorators import trace_node
from app.observability.tool_capture import record_sr_api_call, record_sr_decision

logger = logging.getLogger(__name__)


def build_ceiling_review_payload(
    *,
    backend_refs: dict[str, Any],
    documents: list[dict[str, Any]],
    sub_category: str,
    review: dict[str, Any],
) -> dict[str, Any]:
    """Return the ``PATCH /service-requests/{id}`` body for a review decision."""
    sr_id = str(backend_refs.get("sr_id") or "")
    snapshot: dict[str, Any] = dict(backend_refs.get("create_payload") or {})
    inner: dict[str, Any] = dict(snapshot.get("payload") or {})
    action = review.get("action") or REVIEW_ACTION_APPROVE

    draft_type = draft_report_doc_type(sub_category)
    rejected_type = rejected_report_doc_type(sub_category)
    draft_status = review.get("document_status") or DOC_STATUS_APPROVED

    doc_ids: list[str] = []
    status_map: list[dict[str, Any]] = []
    # Same row shape as the form's intialState.documents.
    doc_rows: list[dict[str, Any]] = []
    for doc in documents:
        doc_id = str(doc.get("document_id") or "").strip()
        if not doc_id or doc_id in doc_ids:
            continue
        doc_ids.append(doc_id)
        doc_type = doc.get("document_type_id") or doc.get("document_type")
        if doc_type == draft_type:
            status = draft_status
        elif doc_type == rejected_type:
            status = DOC_STATUS_DISAPPROVED
        else:
            status = ""
        status_map.append({"id": doc_id, "document_status": status})
        doc_rows.append({"comment": "", "document_id": doc_id, "document_type_id": doc_type or ""})
    for existing in inner.get("documents_ids") or []:
        if existing and str(existing) not in doc_ids:
            doc_ids.append(str(existing))
    # The platform refuses a submit that lists an attachment already deleted.
    doc_ids = without_deleted_ids(doc_ids, deleted_document_ids(backend_refs))
    status_map = [entry for entry in status_map if entry["id"] in doc_ids]
    doc_rows = [row for row in doc_rows if row["document_id"] in doc_ids]

    comment = str(review.get("comment") or "")
    inner.update(
        {
            "documents": doc_rows,
            "documents_ids": doc_ids,
            "document_status_map": status_map,
            "sr_id": sr_id,
            "user_action": None,
        }
    )

    return {
        "payload": inner,
        "title": inner.get("title") or snapshot.get("title") or "",
        "tenant_profile_id": inner.get("tenant_profile_id", snapshot.get("tenant_profile_id")),
        "property_id": inner.get("property_id", snapshot.get("property_id")),
        "service_category": SERVICE_CATEGORY,
        "sub_category": sub_category,
        "lease_code": snapshot.get("lease_code") or inner.get("lease") or "",
        "lease_id": snapshot.get("lease_id") or inner.get("lease_id"),
        "service_request_id": sr_id,
        "status": REVIEW_ACTION_PLATFORM_STATUS[action],
        "comment": comment,
    }


def _success_message(stage: str, action: str, label: str, sr_id: str) -> str:
    if action == REVIEW_ACTION_MORE_INFO:
        return (
            f"{label} **{sr_id}** has been sent back to the requester for more "
            "information. It will return to this step once they respond."
        )
    if stage == STAGE_L2_REVIEW:
        return (
            f"{label} **{sr_id}**: report submitted. "
            "The tenant can now acknowledge it."
        )
    return f"{label} **{sr_id}** has been sent to the FM Manager. The request stays in process."


@trace_node("ceiling_review_submission", "TOOL")
async def ceiling_review_submission_node(state: ServiceRequestState) -> dict[str, Any]:
    """Build and PATCH the review decision, then close this reviewer's turn."""
    if state.get("confirmation_status") != "CONFIRMED":
        return {
            "status": "FAILED",
            "response_message": "Submission blocked: the review has not been confirmed.",
        }
    stage = str(state.get("workflow_stage") or "")
    if stage not in (STAGE_L1_REVIEW, STAGE_L2_REVIEW):
        return {"status": "FAILED", "response_message": "Submission blocked: no review is open."}

    sub_category = str(state.get("sub_category") or "")
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    collected: dict[str, Any] = dict(state.get("collected_data") or {})
    review: dict[str, Any] = dict(collected.get(REVIEW_KEY) or {})
    action = review.get("action") or REVIEW_ACTION_APPROVE
    sr_id = str(backend_refs.get("sr_id") or "")

    payload = build_ceiling_review_payload(
        backend_refs=backend_refs,
        documents=list(state.get("documents") or []),
        sub_category=sub_category,
        review=review,
    )
    result = await get_service_request_api_service().patch_service_request(sr_id, payload)
    await record_sr_api_call(
        state,
        tool_name="service_request_api.ceiling_inspection.review",
        tool_type=TOOL_TYPE_SR_PATCH,
        request_payload=payload,
        result=result,
    )
    logger.info(
        "ceiling_review_submission: stage=%s action=%s status_code=%s error=%s",
        stage,
        action,
        result.status_code,
        result.error,
    )

    if result.error is not None:
        # Keep the answers so a retry does not start the review over.
        review["pending_confirm"] = True
        collected[REVIEW_KEY] = review
        return {
            "collected_data": collected,
            "status": "FAILED",
            "confirmation_status": "PENDING",
            "response_message": (
                "I couldn't send your review to the platform. "
                f"Please try again in a moment. (Detail: {result.error})"
            ),
        }

    if action == REVIEW_ACTION_APPROVE:
        await record_sr_decision(
            state, approved=True, sr_id=sr_id, flow=FLOW_CEILING_INSPECTION, stage=stage
        )

    collected.pop(REVIEW_KEY, None)
    message = _success_message(stage, action, sub_category_label(sub_category), sr_id)
    return {
        "collected_data": collected,
        "backend_refs": backend_refs,
        "status": "WAITING_FOR_USER",
        "workflow_stage": STAGE_REVIEW_SUBMITTED,
        "confirmation_status": None,
        "confirmation_required": False,
        "response_message": message,
        "response_ui": {
            "type": "message",
            "message": message,
            "verbatim": True,
            "workflow_complete": True,
            "workflow_stage": STAGE_REVIEW_SUBMITTED,
        },
    }
