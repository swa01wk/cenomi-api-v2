"""Submit the confirmed Fitout Progress SR to the Service Request API.

Responsibilities
----------------
* Guard: proceed only when ``confirmation_status == "CONFIRMED"``.
* Guard: block on any blocking validation error.
* Guard: block when ``create_payload`` is absent.
* CREATE path: ``POST /service-requests`` → ``SR_CREATED`` / ``SR_APPROVED``.

Non-responsibilities
--------------------
* Does not call the LLM, and does not build the payload.

Saying what actually happened
-----------------------------
A create by a user holding ``DD_ENGINEER`` is auto-approved inside the platform's
create transaction, and ``leases.fitout_progress`` is written there — so
"it will be routed to the approver for review" would promise a step that never
happens. The platform's own status wins when the create response carries one;
otherwise the caller's role decides, which is reliable here because creating is
DD-only. Both cases are honest about whether the lease was updated.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.fitout_progress_schema import (
    PROGRESS_FIELD,
    role_can_approve,
)
from app.agents.services.fitout_progress_suggestions_service import (
    format_percentage,
    parse_percentage,
)
from app.agents.services.service_request_api_service import get_service_request_api_service
from app.observability.analytics_constants import (
    FLOW_FITOUT_PROGRESS,
    TOOL_TYPE_SR_CREATE,
    TOOL_TYPE_SR_PATCH,
)
from app.observability.decorators import trace_node
from app.observability.tool_capture import record_sr_api_call, record_sr_decision

logger = logging.getLogger(__name__)

# Platform statuses that mean the SR needs no further approval step.
_APPROVED_STATUSES: frozenset[str] = frozenset({"APPROVED", "FINISHED", "COMPLETED", "DONE"})


def _has_blocking_errors(errors: list[dict[str, Any]]) -> bool:
    return any(e.get("blocking", True) for e in errors)


def _created_sr_status(result: Any) -> str:
    """The SR status the platform reported on create, upper-cased, or ""."""
    body = getattr(result, "response_payload", None)
    if not isinstance(body, dict):
        return ""
    for container in (body, body.get("data")):
        if not isinstance(container, dict):
            continue
        for key in ("status", "sr_status", "_sr_status", "current_sr_status"):
            value = container.get(key)
            if value:
                return str(value).strip().upper()
    return ""


def _was_auto_approved(result: Any, state: ServiceRequestState) -> bool:
    """True when this create needed no separate approval step.

    The status the platform returned is authoritative. The create response
    normally carries only ``service_request_id``, and then the caller's role is
    used — the same fallback the fitout confirmation agents use, and sound here
    because ``AutoApproveFitoutProgressForApproverCreator`` fires exactly when
    the creator holds the level's role.
    """
    platform_status = _created_sr_status(result)
    if platform_status:
        return platform_status in _APPROVED_STATUSES

    from app.agents.services.review_decorations_service import derive_user_role

    role = derive_user_role(state.get("auth_context"))
    return bool(role and role_can_approve(role))


def _percentage_label(payload: dict[str, Any]) -> str:
    raw = (payload.get("payload") or {}).get(PROGRESS_FIELD)
    parsed = parse_percentage(raw)
    return format_percentage(parsed) if parsed is not None else str(raw or "")


@trace_node("fitout_progress_api_submission", "TOOL")
async def fitout_progress_api_submission_node(state: ServiceRequestState) -> dict[str, Any]:
    """Submit the Fitout Progress payload to the SR API."""

    # ── 1. Confirmation guard ──────────────────────────────────────────────
    if state.get("confirmation_status") != "CONFIRMED":
        logger.warning(
            "fitout_progress_api_submission: blocked — confirmation_status=%s",
            state.get("confirmation_status"),
        )
        return {
            "status": "FAILED",
            "response_message": (
                "Submission blocked: this hasn't been confirmed yet. Please "
                "review the percentage and confirm before I record it."
            ),
        }

    # ── 2. Blocking-validation guard ───────────────────────────────────────
    validation_errors: list[dict[str, Any]] = state.get("validation_errors") or []
    if _has_blocking_errors(validation_errors):
        count = sum(1 for e in validation_errors if e.get("blocking", True))
        return {
            "status": "FAILED",
            "response_message": (
                f"Submission blocked: {count} validation error(s) must be "
                "resolved first."
            ),
        }

    # ── 3. Payload guard ───────────────────────────────────────────────────
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    payload: dict[str, Any] | None = backend_refs.get("create_payload")
    if not payload:
        logger.warning("fitout_progress_api_submission: blocked — create_payload missing")
        return {
            "status": "FAILED",
            "response_message": (
                "Submission blocked: the request payload could not be found. "
                "Please try again."
            ),
        }

    workflow_stage: str = state.get("workflow_stage") or "CREATE_SR"

    # ── 4. Approver guard — before any PATCH ───────────────────────────────
    if workflow_stage == "APPROVE_SR":
        from app.agents.services.review_decorations_service import derive_user_role

        role = derive_user_role(state.get("auth_context"))
        if role and not role_can_approve(role):
            logger.info(
                "fitout_progress_api_submission: approve denied for role=%s", role
            )
            return {
                "status": "WAITING_FOR_USER",
                "response_message": (
                    "Approving a fit-out progress request isn't available for "
                    "your role — the RDD Project Manager approves these."
                ),
            }

    # ── 5. API call ────────────────────────────────────────────────────────
    svc = get_service_request_api_service()

    if workflow_stage == "APPROVE_SR":
        sr_id = str(backend_refs.get("sr_id") or "")
        result = await svc.patch_service_request(sr_id, payload)
        await record_sr_api_call(
            state,
            tool_name="service_request_api.fitout_progress.update",
            tool_type=TOOL_TYPE_SR_PATCH,
            request_payload=payload,
            result=result,
        )
    else:
        result = await svc.create_service_request(payload)
        await record_sr_api_call(
            state,
            tool_name="service_request_api.fitout_progress.create",
            tool_type=TOOL_TYPE_SR_CREATE,
            request_payload=payload,
            result=result,
        )

    logger.info(
        "fitout_progress_api_submission: stage=%s status_code=%s sr_id=%s error=%s",
        workflow_stage,
        result.status_code,
        result.sr_id,
        result.error,
    )

    # ── 6. Failure path ────────────────────────────────────────────────────
    if result.error is not None or result.sr_id is None:
        backend_refs["service_request_status"] = "FAILED"
        detail = f" (Detail: {result.error})" if result.error else ""
        # Deliberately not retried here: a repeated POST is a second SR and a
        # second write to the lease.
        return {
            "backend_refs": backend_refs,
            "status": "FAILED",
            "response_message": (
                "The platform didn't accept the request, so nothing was "
                f"recorded. Please try again in a moment.{detail}"
            ),
        }

    # ── 7. Success ─────────────────────────────────────────────────────────
    backend_refs["sr_id"] = result.sr_id
    backend_refs["service_request_status"] = "SUBMITTED"
    if result.correlation_id:
        backend_refs["correlation_id"] = result.correlation_id

    percentage = _percentage_label(payload)
    lease_code = (payload.get("payload") or {}).get("lease_code") or ""
    sr_ref = result.sr_id

    # ── 7a. The approver's decision ────────────────────────────────────────
    if workflow_stage == "APPROVE_SR":
        approved = (payload.get("status") or "").upper() == "APPROVED"
        await record_sr_decision(
            state,
            approved=approved,
            sr_id=sr_ref,
            flow=FLOW_FITOUT_PROGRESS,
            stage=workflow_stage,
        )
        if approved:
            stage = "SR_APPROVED"
            success_message = (
                f"Service Request **{sr_ref}** approved at **{percentage}%**. "
                f"The lease now reads {percentage}%."
            )
        else:
            # REJECT is terminal and the write-back hook is registered for
            # APPROVED only, so the lease keeps whatever it already held.
            stage = "SR_REJECTED"
            success_message = (
                f"Service Request **{sr_ref}** rejected. The lease was not "
                "updated. A new request can be raised with the right "
                "percentage."
            )
        # SUBMITTED, as the confirmation agents' approvals return: it is what
        # makes the turn report action_result=success, and that is the signal
        # the SR details page refreshes on. With WAITING_FOR_USER the SR was
        # approved but the page kept "Pending" and its Action button, which
        # read as if nothing had happened.
        return {
            "backend_refs": backend_refs,
            "status": "SUBMITTED",
            "workflow_stage": stage,
            "confirmation_status": None,
            "confirmation_required": False,
            "response_message": success_message,
            "response_ui": {
                "type": "message",
                "message": success_message,
                "workflow_complete": True,
                "workflow_stage": stage,
            },
        }

    # ── 7b. A create, which may already be settled ─────────────────────────
    if _was_auto_approved(result, state):
        stage = "SR_APPROVED"
        success_message = (
            f"Recorded. Service Request **{sr_ref}** sets fit-out progress for "
            f"**{lease_code}** to **{percentage}%**. Because you're the "
            "approver, it was approved on creation and the lease now reads "
            f"{percentage}%."
        )
    else:
        stage = "SR_CREATED"
        success_message = (
            f"Service Request **{sr_ref}** records fit-out progress for "
            f"**{lease_code}** at **{percentage}%**. It's with the RDD Project "
            "Manager for approval — the lease is updated once they approve."
        )

    return {
        "backend_refs": backend_refs,
        "status": "WAITING_FOR_USER",
        "workflow_stage": stage,
        "confirmation_status": None,
        "confirmation_required": False,
        "response_message": success_message,
        "response_ui": {
            "type": "message",
            "message": success_message,
            "workflow_complete": True,
            "workflow_stage": stage,
        },
    }
