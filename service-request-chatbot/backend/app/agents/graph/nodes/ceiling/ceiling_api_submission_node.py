"""Submit a confirmed Open / Close Ceiling Inspection to the Service Request API.

Guards (all must pass before any API call)
------------------------------------------
* ``confirmation_status == "CONFIRMED"``
* no blocking ``validation_errors``
* ``backend_refs.create_payload`` present

``POST /service-requests`` → ``workflow_stage = CEILING_SR_CREATED`` and a
success line that names the inspection and says where it now is. The platform
auto-approves the creator's own level (RDD PM at 1 when the draft report came
with the request, FM Manager at 2 once level 1 is approved), so the new SR is
read back to report the live position; the creator's role is the fallback.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.ceiling_schema import (
    DRAFT_REPORT_STATUS_KEY,
    STAGE_L1_REVIEW,
    STAGE_L2_REVIEW,
    STAGE_SR_CREATED,
    creator_uploads_report,
    map_operations_to_stage,
    sub_category_label,
    user_roles,
)
from app.agents.services.service_request_api_service import get_service_request_api_service
from app.observability.analytics_constants import TOOL_TYPE_SR_CREATE
from app.observability.decorators import trace_node
from app.observability.tool_capture import record_sr_api_call

logger = logging.getLogger(__name__)


def _blocked(reason: str) -> dict[str, Any]:
    return {"status": "FAILED", "response_message": f"Submission blocked: {reason}"}


async def _live_stage(sr_id: str) -> str | None:
    """The new SR's review stage on the platform, or None when it can't be read."""
    try:
        result = await get_service_request_api_service().get_service_request(sr_id)
    except Exception:  # the create already succeeded; only the message degrades
        logger.warning("ceiling_api_submission: read-back of %s failed", sr_id, exc_info=True)
        return None
    if getattr(result, "error", None):
        return None
    status: str | None = getattr(result, "status", None)
    operations: list[dict[str, Any]] = list(getattr(result, "service_request_operations", None) or [])
    if not status or not operations:
        envelope: dict[str, Any] = getattr(result, "response_payload", None) or {}
        status = status or envelope.get("_sr_status") or envelope.get("status")
        operations = operations or list(
            envelope.get("_service_request_operations")
            or envelope.get("service_request_operations")
            or []
        )
    return map_operations_to_stage(operations, status, None)


def created_message(
    sr_id: str, label: str, live_stage: str | None, state: ServiceRequestState
) -> str:
    """Where the new request is: the platform's answer first, the creator's role otherwise."""
    auth = state.get("auth_context")
    rddpm_with_report = creator_uploads_report(auth) and bool(
        (state.get("collected_data") or {}).get(DRAFT_REPORT_STATUS_KEY)
    )
    head = f"{label} created — reference **{sr_id}**. "
    if live_stage == STAGE_L2_REVIEW or (live_stage is None and rddpm_with_report):
        return head + (
            "Level 1 (RDD Project Manager) was approved automatically with your report, "
            "so it is now with the FM Manager for approval."
        )
    if live_stage == STAGE_L1_REVIEW and creator_uploads_report(auth):
        return head + (
            "Level 1 is still open because the report did not reach the platform with the "
            "request. Please upload it on the request and approve level 1."
        )
    if "FM_MANAGER" in user_roles(auth):
        return head + (
            "It is with the RDD Project Manager for review. Your FM Manager level will be "
            "approved automatically once they approve."
        )
    return head + "It is with the RDD Project Manager for review."


@trace_node("ceiling_api_submission", "TOOL")
async def ceiling_api_submission_node(state: ServiceRequestState) -> dict[str, Any]:
    """POST the ceiling inspection create payload."""
    if state.get("confirmation_status") != "CONFIRMED":
        return _blocked(
            "the service request has not been confirmed. "
            "Please review the details and confirm before submitting."
        )
    blocking = [e for e in (state.get("validation_errors") or []) if e.get("blocking", True)]
    if blocking:
        return _blocked(
            f"{len(blocking)} validation error(s) must be resolved before this "
            "request can be submitted."
        )
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    payload: dict[str, Any] | None = backend_refs.get("create_payload")
    if not payload:
        return _blocked("the request payload could not be found. Please try again.")

    result = await get_service_request_api_service().create_service_request(payload)
    await record_sr_api_call(
        state,
        tool_name="service_request_api.ceiling_inspection.create",
        tool_type=TOOL_TYPE_SR_CREATE,
        request_payload=payload,
        result=result,
    )
    logger.info(
        "ceiling_api_submission: status_code=%s sr_id=%s error=%s",
        result.status_code,
        result.sr_id,
        result.error,
    )

    if result.error is not None or result.sr_id is None:
        backend_refs["service_request_status"] = "FAILED"
        detail = f" (Detail: {result.error})" if result.error else ""
        return {
            "backend_refs": backend_refs,
            "status": "FAILED",
            "response_message": (
                "I was unable to submit your inspection request due to an API error. "
                f"Please try again later or contact support.{detail}"
            ),
        }

    backend_refs["sr_id"] = result.sr_id
    backend_refs["service_request_status"] = "SUBMITTED"
    if result.correlation_id:
        backend_refs["correlation_id"] = result.correlation_id

    label = sub_category_label(state.get("sub_category"))
    live_stage = await _live_stage(str(result.sr_id))
    message = created_message(str(result.sr_id), label, live_stage, state)
    return {
        "backend_refs": backend_refs,
        "status": "WAITING_FOR_USER",
        "workflow_stage": STAGE_SR_CREATED,
        "confirmation_status": None,
        "confirmation_required": False,
        "response_message": message,
        "response_ui": {
            "type": "message",
            "message": message,
            "verbatim": True,
            "workflow_complete": True,
            "workflow_stage": STAGE_SR_CREATED,
        },
    }
