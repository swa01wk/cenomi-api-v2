"""Submit a confirmed Work Permit SR (POST create or PATCH review).

Guards:
- confirmation_status must be CONFIRMED
- No blocking validation errors
- Payload must be present in backend_refs
- Review hops: caller role must match an IN_PROGRESS assigned_role
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.work_permit_schema import (
    STAGE_LABELS,
    WP_APPROVED,
    WP_CREATED,
    WP_REJECTED,
    WP_REVIEW_STAGES,
    in_progress_roles,
    map_wp_operations_to_stage,
)
from app.agents.services.permission_service import PermissionDeniedError, PermissionService
from app.agents.services.service_request_api_service import get_service_request_api_service
from app.observability.analytics_constants import (
    FLOW_WORK_PERMIT,
    TOOL_TYPE_SR_CREATE,
    TOOL_TYPE_SR_PATCH,
)
from app.observability.decorators import trace_node
from app.observability.tool_capture import record_sr_api_call, record_sr_decision

_permission_service = PermissionService()

logger = logging.getLogger(__name__)


@trace_node("work_permit_api_submission", "TOOL")
async def work_permit_api_submission_node(state: ServiceRequestState) -> dict[str, Any]:
    """Submit the Work Permit create POST or review PATCH."""
    workflow_stage: str = state.get("workflow_stage") or ""
    is_review = workflow_stage in WP_REVIEW_STAGES
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    auth_context = state.get("auth_context")

    # ── 0. Permission check ────────────────────────────────────────────────
    if auth_context is not None:
        try:
            if is_review:
                _permission_service.ensure_can_approve_work_permit(
                    auth_context,
                    workflow_stage=workflow_stage,
                    in_progress_roles=in_progress_roles(backend_refs.get("sr_operations")),
                )
            else:
                _permission_service.ensure_can_create_work_permit(auth_context)
        except PermissionDeniedError as exc:
            logger.warning("work_permit_api_submission_node: permission denied — %s", exc)
            action = "approve" if is_review else "create"
            return {
                "status": "FAILED",
                "response_message": (
                    f"You do not have permission to {action} this Work Permit Service Request."
                ),
            }

    # ── 1. Confirmation guard ──────────────────────────────────────────────
    if state.get("confirmation_status") != "CONFIRMED":
        logger.warning("work_permit_api_submission_node: blocked — not confirmed")
        return {
            "status": "FAILED",
            "response_message": (
                "Submission blocked: the work permit request has not been confirmed. "
                "Please review the details and confirm before submitting."
            ),
        }

    # ── 2. Blocking validation guard ──────────────────────────────────────
    validation_errors: list[dict[str, Any]] = state.get("validation_errors") or []
    blocking = [e for e in validation_errors if e.get("blocking", True)]
    if blocking:
        logger.warning(
            "work_permit_api_submission_node: blocked — %d blocking error(s)", len(blocking)
        )
        return {
            "status": "FAILED",
            "response_message": (
                f"Submission blocked: {len(blocking)} validation error(s) must be "
                "resolved before this request can be submitted."
            ),
        }

    # ── 3. Payload guard ───────────────────────────────────────────────────
    payload: dict[str, Any] | None = backend_refs.get("create_payload")
    if not payload:
        logger.warning("work_permit_api_submission_node: blocked — no create_payload")
        return {
            "status": "FAILED",
            "response_message": "Submission blocked: payload not found. Please try again.",
        }

    # ── 4. API call ────────────────────────────────────────────────────────
    svc = get_service_request_api_service()
    if is_review:
        sr_id = backend_refs.get("sr_id", "")
        result = await svc.patch_service_request(sr_id, payload)
        await record_sr_api_call(
            state,
            tool_name="service_request_api.work_permit.review",
            tool_type=TOOL_TYPE_SR_PATCH,
            request_payload=payload,
            result=result,
        )
    else:
        result = await svc.create_service_request(payload)
        await record_sr_api_call(
            state,
            tool_name="service_request_api.work_permit.create",
            tool_type=TOOL_TYPE_SR_CREATE,
            request_payload=payload,
            result=result,
        )

    logger.info(
        "work_permit_api_submission_node: stage=%s status_code=%s sr_id=%s error=%s",
        workflow_stage,
        result.status_code,
        result.sr_id,
        result.error,
    )

    if result.error or not result.sr_id:
        logger.warning(
            "work_permit_api_submission_node: platform failure response=%s request_keys=%s",
            getattr(result, "response_payload", None),
            sorted(payload.keys()) if isinstance(payload, dict) else None,
        )
        backend_refs["service_request_status"] = "FAILED"
        detail = f" Detail: {result.error}" if result.error else ""
        if is_review and (
            result.status_code == 500 or "HTTP 500" in str(result.error or "")
        ):
            msg = (
                "Unable to complete Work Permit approval due to a platform error "
                "(final approval generates the permit PDF). "
                "Please try again later or approve from the Service Request page."
                f"{detail}"
            )
        else:
            msg = (
                "Unable to submit the work permit request due to an API error. "
                f"Please try again later.{detail}"
            )
        return {
            "backend_refs": backend_refs,
            "status": "FAILED",
            "response_message": msg,
        }

    backend_refs["sr_id"] = result.sr_id
    backend_refs["service_request_status"] = "SUBMITTED"
    if result.correlation_id:
        backend_refs["correlation_id"] = result.correlation_id

    if is_review:
        submitted = (payload.get("status") or "").upper()
        await record_sr_decision(
            state,
            approved=submitted != "REJECTED",
            sr_id=result.sr_id,
            flow=FLOW_WORK_PERMIT,
            stage=workflow_stage,
        )
        return await _review_result(svc, backend_refs, payload, result.sr_id)

    return await _create_result(svc, backend_refs, payload, result.sr_id)


async def _create_result(
    svc: Any,
    backend_refs: dict[str, Any],
    payload: dict[str, Any],
    sr_id: str,
) -> dict[str, Any]:
    """Map POST outcome. GET detects platform auto-approve (e.g. MM Operations)."""
    stage = WP_CREATED
    try:
        get_result = await svc.get_service_request(sr_id)
        if not getattr(get_result, "error", None):
            ops = list(getattr(get_result, "service_request_operations", None) or [])
            backend_refs["sr_operations"] = ops
            backend_refs["platform_sr_status"] = getattr(get_result, "status", None)
            mapped = map_wp_operations_to_stage(
                ops,
                sub_category=payload.get("sub_category"),
                platform_status=getattr(get_result, "status", None),
                current_stage=WP_CREATED,
            )
            if mapped in {WP_APPROVED, WP_REJECTED}:
                stage = mapped
    except Exception:
        logger.warning("work_permit_api_submission_node: post-create GET failed", exc_info=True)

    if stage == WP_APPROVED:
        msg = (
            f"Your Work Permit Service Request has been successfully submitted "
            f"and auto-approved. Your reference number is **{sr_id}**."
        )
    else:
        msg = (
            f"Your Work Permit Service Request has been successfully submitted. "
            f"Your reference number is **{sr_id}**. "
            "It will be routed to the next reviewer when required."
        )
    return {
        "backend_refs": backend_refs,
        "status": "SUBMITTED",
        "workflow_stage": stage,
        "confirmation_status": None,
        "confirmation_required": False,
        "response_message": msg,
        "response_ui": {
            "type": "message",
            "message": msg,
            "workflow_complete": True,
            "workflow_stage": stage,
        },
    }


async def _review_result(
    svc: Any,
    backend_refs: dict[str, Any],
    payload: dict[str, Any],
    sr_id: str,
) -> dict[str, Any]:
    """Map PATCH outcome to the next chatbot stage (GET when possible)."""
    submitted = (payload.get("status") or "").upper()
    if submitted == "REJECTED":
        msg = f"Work Permit SR **{sr_id}** has been rejected."
        return {
            "backend_refs": backend_refs,
            "status": "SUBMITTED",
            "workflow_stage": WP_REJECTED,
            "confirmation_status": None,
            "confirmation_required": False,
            "response_message": msg,
            "response_ui": {
                "type": "message",
                "message": msg,
                "workflow_complete": True,
                "workflow_stage": WP_REJECTED,
            },
        }

    next_stage = WP_APPROVED
    platform_status = "APPROVED"
    try:
        get_result = await svc.get_service_request(sr_id)
        if not get_result.error:
            platform_status = (get_result.status or "").upper()
            ops = list(get_result.service_request_operations or [])
            backend_refs["sr_operations"] = ops
            backend_refs["platform_sr_status"] = get_result.status
            mapped = map_wp_operations_to_stage(
                ops,
                sub_category=payload.get("sub_category"),
                platform_status=get_result.status,
                current_stage=WP_APPROVED,
            )
            next_stage = mapped or WP_APPROVED
    except Exception:
        logger.warning("work_permit_api_submission_node: post-approve GET failed", exc_info=True)

    if next_stage in WP_REVIEW_STAGES:
        label = STAGE_LABELS.get(next_stage, "the next reviewer")
        msg = (
            f"Your approval for Work Permit SR **{sr_id}** was recorded. "
            f"The request is now with **{label}**."
        )
        return {
            "backend_refs": backend_refs,
            "status": "SUBMITTED",
            "workflow_stage": next_stage,
            "confirmation_status": None,
            "confirmation_required": False,
            # Keep agent so reopen/sync stays on WP; clear confirmation so the
            # next hop card is only built for roles that can act (see confirmation).
            "active_agent": "work_permit_agent",
            "response_message": msg,
            "response_ui": {
                "type": "message",
                "message": msg,
                "stage_complete": True,
                "action": "wp_hop_approved",
                "workflow_stage": next_stage,
            },
        }

    msg = f"Work Permit SR **{sr_id}** has been fully approved."
    if platform_status and platform_status not in {"APPROVED", ""}:
        msg = f"Work Permit SR **{sr_id}** is now **{platform_status}**."
    return {
        "backend_refs": backend_refs,
        "status": "SUBMITTED",
        "workflow_stage": WP_APPROVED,
        "confirmation_status": None,
        "confirmation_required": False,
        "response_message": msg,
        "response_ui": {
            "type": "message",
            "message": msg,
            "workflow_complete": True,
            "workflow_stage": WP_APPROVED,
        },
    }
