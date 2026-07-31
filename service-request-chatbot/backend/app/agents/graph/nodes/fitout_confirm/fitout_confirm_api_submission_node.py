"""Submit validated, confirmed fitout confirmation SR to the Service Request API.

Responsibilities
----------------
* Guard: proceed only when ``confirmation_status == "CONFIRMED"``.
* Guard: block if ``validation_errors`` contains any blocking entry.
* Guard: block if ``create_payload`` is absent from ``backend_refs``.
* CREATE path: ``POST /service-requests`` → set ``workflow_stage = "SR_CREATED"``.
* APPROVE path:
    - ``status: APPROVED`` → terminal ``SR_APPROVED``
    - ``status: IN_PROCESS`` → stays at ``APPROVE_SR`` (Update only)

Non-responsibilities
--------------------
* Does not call the LLM.
* Does not build the payload — that is fitout_confirm_payload_builder_node.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.agents.graph.state import ServiceRequestState
from app.agents.services.service_request_api_service import get_service_request_api_service
from app.observability.decorators import trace_node

logger = logging.getLogger(__name__)


def _has_blocking_errors(errors: list[dict[str, Any]]) -> bool:
    return any(e.get("blocking", True) for e in errors)


def _to_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError):
        return None


@trace_node("fitout_confirm_api_submission", "TOOL")
async def fitout_confirm_api_submission_node(state: ServiceRequestState) -> dict[str, Any]:
    """Submit the fitout confirmation SR payload to the SR API."""

    # ── 1. Confirmation guard ──────────────────────────────────────────────
    if state.get("confirmation_status") != "CONFIRMED":
        logger.warning(
            "fitout_confirm_api_submission: blocked — confirmation_status=%s",
            state.get("confirmation_status"),
        )
        return {
            "status": "FAILED",
            "response_message": (
                "Submission blocked: the service request has not been confirmed. "
                "Please review the details and confirm before submitting."
            ),
        }

    # ── 2. Blocking-validation guard ──────────────────────────────────────
    validation_errors: list[dict[str, Any]] = state.get("validation_errors") or []
    if _has_blocking_errors(validation_errors):
        count = sum(1 for e in validation_errors if e.get("blocking", True))
        return {
            "status": "FAILED",
            "response_message": (
                f"Submission blocked: {count} validation error(s) must be "
                "resolved before this request can be submitted."
            ),
        }

    # ── 3. Payload guard ───────────────────────────────────────────────────
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    payload: dict[str, Any] | None = backend_refs.get("create_payload")
    if not payload:
        logger.warning("fitout_confirm_api_submission: blocked — create_payload missing")
        return {
            "status": "FAILED",
            "response_message": (
                "Submission blocked: the request payload could not be found. "
                "Please try again or contact support."
            ),
        }

    workflow_stage: str = state.get("workflow_stage") or "CREATE_SR"
    sub_category: str = state.get("sub_category") or ""

    # ── 4. API call ────────────────────────────────────────────────────────
    svc = get_service_request_api_service()

    if workflow_stage == "APPROVE_SR":
        sr_id = backend_refs.get("sr_id", "")
        result = await svc.patch_service_request(sr_id, payload)
    else:
        result = await svc.create_service_request(payload)

    logger.info(
        "fitout_confirm_api_submission: stage=%s status_code=%s sr_id=%s error=%s",
        workflow_stage,
        result.status_code,
        result.sr_id,
        result.error,
    )

    # ── 5. Failure path ────────────────────────────────────────────────────
    api_success = result.error is None and result.sr_id is not None
    if not api_success:
        backend_refs["service_request_status"] = "FAILED"
        detail = f" (Detail: {result.error})" if result.error else ""
        return {
            "backend_refs": backend_refs,
            "status": "FAILED",
            "response_message": (
                "I was unable to submit your service request due to an API error. "
                f"Please try again later or contact support.{detail}"
            ),
        }

    # ── 6. Success — enrich backend_refs ──────────────────────────────────
    if result.sr_id:
        backend_refs["sr_id"] = result.sr_id
    backend_refs["service_request_status"] = "SUBMITTED"
    if result.correlation_id:
        backend_refs["correlation_id"] = result.correlation_id

    # ── 7. Return correct terminal/continuation state ──────────────────────
    if workflow_stage == "APPROVE_SR":
        submitted_status = (payload.get("status") or "").upper()
        if submitted_status == "APPROVED":
            sr_ref = result.sr_id or backend_refs.get("sr_id", "N/A")
            success_message = (
                f"Service Request **{sr_ref}** has been approved. "
                "The fitout confirmation is now complete."
            )
            return {
                "backend_refs": backend_refs,
                "status": "SUBMITTED",
                "workflow_stage": "SR_APPROVED",
                "confirmation_status": None,
                "confirmation_required": False,
                "response_message": success_message,
                "response_ui": {
                    "type": "message",
                    "message": success_message,
                    "workflow_complete": True,
                    "workflow_stage": "SR_APPROVED",
                },
            }
        else:
            # IN_PROCESS — update only, stays in APPROVE_SR
            sr_ref = result.sr_id or backend_refs.get("sr_id", "N/A")
            success_message = (
                f"Service Request **{sr_ref}** has been updated. "
                "The request is still pending approval."
            )
            return {
                "backend_refs": backend_refs,
                "status": "WAITING_FOR_USER",
                "workflow_stage": "APPROVE_SR",
                "confirmation_status": None,
                "confirmation_required": False,
                "response_message": success_message,
            }

    # CREATE path — SR created, workflow complete
    sr_ref = result.sr_id
    success_message = (
        f"Your {_sub_category_label(sub_category)} Service Request has been submitted "
        f"— reference **{sr_ref}**. "
        "It will be routed to the approver for review."
    )
    return {
        "backend_refs": backend_refs,
        "status": "WAITING_FOR_USER",
        "workflow_stage": "SR_CREATED",
        "confirmation_status": None,
        "confirmation_required": False,
        "response_message": success_message,
        "response_ui": {
            "type": "message",
            "message": success_message,
            "workflow_complete": True,
            "workflow_stage": "SR_CREATED",
        },
    }


def _sub_category_label(sub_category: str) -> str:
    _LABELS = {
        "FIT_OUT_CONFIRM_FOS": "Fitout Start Confirmation",
        "FIT_OUT_CONFIRM_END": "Fitout End Confirmation",
        "FIT_OUT_CONFIRM_TRADING": "Trading Confirmation",
    }
    return _LABELS.get(sub_category, "Fitout Confirmation")
