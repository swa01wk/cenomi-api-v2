"""Submit a confirmed General Inquiry SR to the platform.

Guards
------
- ``confirmation_status`` must be ``CONFIRMED``.
- No blocking validation errors.
- ``create_payload`` must be present in ``backend_refs``.

Calls ``POST /service-requests`` via ``ServiceRequestAPIService``.
The platform creates the SR as ``IN_PROCESS`` and triggers the level-1
operation (Customer Relations Team).  If the creator is on the Customer
Relations Team the SR is auto-approved in the same transaction.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.services.service_request_api_service import get_service_request_api_service
from app.observability.analytics_constants import TOOL_TYPE_SR_CREATE
from app.observability.decorators import trace_node
from app.observability.tool_capture import record_sr_api_call

logger = logging.getLogger(__name__)


@trace_node("general_inquiry_api_submission", "TOOL")
async def general_inquiry_api_submission_node(
    state: ServiceRequestState,
) -> dict[str, Any]:
    """POST the General Inquiry SR to the Cenomi platform.

    On success: sets ``workflow_stage = "SR_CREATED"``, status ``SUBMITTED``.
    On failure: sets status ``FAILED`` with an error message.
    """
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})

    # ── 1. Confirmation guard ─────────────────────────────────────────────
    if state.get("confirmation_status") != "CONFIRMED":
        logger.warning("general_inquiry_api_submission: blocked — not confirmed")
        return {
            "status": "FAILED",
            "response_message": (
                "Submission blocked: the inquiry has not been confirmed. "
                "Please review the details and confirm before submitting."
            ),
        }

    # ── 2. Blocking validation guard ──────────────────────────────────────
    validation_errors: list[dict[str, Any]] = state.get("validation_errors") or []
    blocking = [e for e in validation_errors if e.get("blocking", True)]
    if blocking:
        logger.warning(
            "general_inquiry_api_submission: blocked — %d blocking error(s)",
            len(blocking),
        )
        return {
            "status": "FAILED",
            "response_message": (
                f"Submission blocked: {len(blocking)} validation error(s) must be "
                "resolved before this request can be submitted."
            ),
        }

    # ── 3. Payload guard ──────────────────────────────────────────────────
    payload: dict[str, Any] | None = backend_refs.get("create_payload")
    if not payload:
        logger.warning("general_inquiry_api_submission: blocked — no create_payload")
        return {
            "status": "FAILED",
            "response_message": "Submission blocked: payload not found. Please try again.",
        }

    # ── 4. API call ───────────────────────────────────────────────────────
    svc = get_service_request_api_service()
    review_status = str(payload.get("status") or "").strip()
    sr_id = str(backend_refs.get("sr_id") or payload.get("service_request_id") or "").strip()
    if review_status and sr_id:
        result = await svc.patch_service_request(sr_id, payload)
        tool_name = "service_request_api.general_inquiry.review"
    else:
        result = await svc.create_service_request(payload)
        tool_name = "service_request_api.general_inquiry.create"
    await record_sr_api_call(
        state,
        tool_name=tool_name,
        tool_type=TOOL_TYPE_SR_CREATE,
        request_payload=payload,
        result=result,
    )

    logger.info(
        "general_inquiry_api_submission: status_code=%s sr_id=%s error=%s",
        result.status_code,
        result.sr_id,
        result.error,
    )

    if result.error or (not review_status and not result.sr_id):
        backend_refs["service_request_status"] = "FAILED"
        detail = f" Detail: {result.error}" if result.error else ""
        if review_status:
            message = (
                "Unable to complete the review due to a platform error. "
                f"Please try again or act from the Service Request page.{detail}"
            )
        else:
            message = (
                "Unable to submit the inquiry due to an API error. "
                f"Please try again later.{detail}"
            )
        return {
            "backend_refs": backend_refs,
            "status": "FAILED",
            "response_message": message,
            "response_ui": {"type": "message", "verbatim": True, "message": message},
        }

    resolved_id = result.sr_id or sr_id
    backend_refs["sr_id"] = resolved_id
    if result.correlation_id:
        backend_refs["correlation_id"] = result.correlation_id

    if review_status == "APPROVED":
        stage = "GI_APPROVED"
        message = f"General Inquiry SR **{resolved_id}** has been approved."
        status = "SUBMITTED"
    elif review_status == "REJECTED":
        stage = "GI_REJECTED"
        message = f"General Inquiry SR **{resolved_id}** has been rejected."
        status = "SUBMITTED"
    elif review_status == "ACTION_REQUIRED":
        stage = "GI_CRT_REVIEW"
        message = (
            f"I've asked for more information on General Inquiry SR **{resolved_id}**. "
            "The requester will be notified."
        )
        status = "WAITING_FOR_USER"
    elif review_status == "REASSIGN_TO":
        stage = "GI_CRT_REVIEW"
        role = payload.get("assign_to") or "the selected role"
        message = f"General Inquiry SR **{resolved_id}** has been reassigned to **{role}**."
        status = "WAITING_FOR_USER"
    else:
        stage = "SR_CREATED"
        message = (
            f"Your General Inquiry has been submitted successfully (SR #{resolved_id}). "
            "The Customer Relations Team will review it shortly."
        )
        status = "SUBMITTED"

    backend_refs["service_request_status"] = status
    return {
        "backend_refs": backend_refs,
        "workflow_stage": stage,
        "status": status,
        "response_message": message,
        "response_ui": {"type": "message", "verbatim": True, "message": message},
        "response_decorations": {},
    }
