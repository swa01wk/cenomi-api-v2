"""Submit a confirmed General Inquiry SR to the platform.

Guards
------
- ``confirmation_status`` must be ``CONFIRMED``.
- No blocking validation errors.
- ``create_payload`` must be present in ``backend_refs``.

Calls ``POST /service-requests`` via ``ServiceRequestAPIService``.
The platform creates the SR as ``IN_PROCESS`` and triggers the level-1
operation (Mall Manager).  If the creator is a Mall Manager the SR is
auto-approved in the same transaction.
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
    result = await svc.create_service_request(payload)
    await record_sr_api_call(
        state,
        tool_name="service_request_api.general_inquiry.create",
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

    if result.error or not result.sr_id:
        backend_refs["service_request_status"] = "FAILED"
        return {
            "backend_refs": backend_refs,
            "status": "FAILED",
            "response_message": (
                "Unable to submit the inquiry due to an API error. "
                f"Please try again later.{' Detail: ' + result.error if result.error else ''}"
            ),
        }

    backend_refs["sr_id"] = result.sr_id
    backend_refs["service_request_status"] = "SUBMITTED"
    if result.correlation_id:
        backend_refs["correlation_id"] = result.correlation_id

    return {
        "backend_refs": backend_refs,
        "workflow_stage": "SR_CREATED",
        "status": "SUBMITTED",
        "response_message": (
            f"Your General Inquiry has been submitted successfully (SR #{result.sr_id}). "
            "A Mall Manager will review it shortly."
        ),
    }
