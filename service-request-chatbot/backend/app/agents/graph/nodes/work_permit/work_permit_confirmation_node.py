"""Human confirmation before work permit create or review PATCH.

Create: all CREATE_WORK_PERMIT required fields must be present.
Review: no extra fields — card is built from GET-hydrated collected_data.
Non-actors on a review hop get a status message only (no Approve/Reject).
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.work_permit_schema import (
    STAGE_LABELS,
    WP_REVIEW_STAGES,
    WORK_PERMIT_TYPE_LABELS,
    get_missing_fields,
    in_progress_roles,
)
from app.agents.services.permission_service import PermissionService
from app.observability.decorators import trace_node
from app.types.chat import AuthContext

logger = logging.getLogger(__name__)

_permission_service = PermissionService()

_FIELD_LABELS: dict[str, str] = {
    "lease_code": "Lease Code",
    "mall": "Mall",
    "brand": "Brand",
    "unit_codes": "Unit Codes",
    "work_permit_type": "Work Permit Type",
    "description": "Description of Work",
    "start_date": "Start Date",
    "end_date": "End Date",
    "contractor_name": "Contractor Name",
    "work_location": "Work Location",
    "work_type": "Work Type",
    "comments": "Comments",
}

_EDITABLE_FIELDS: frozenset[str] = frozenset(
    {"work_permit_type", "description", "start_date", "end_date", "contractor_name", "comments"}
)

_DISPLAY_FIELDS: tuple[str, ...] = (
    "lease_code",
    "mall",
    "brand",
    "unit_codes",
    "work_permit_type",
    "description",
    "start_date",
    "end_date",
    "contractor_name",
    "work_location",
    "work_type",
    "comments",
)

_CREATE_MESSAGE = (
    "All required details have been collected. Please review the Work Permit Service "
    "Request below and confirm, or let me know if you want to change anything."
)

_REVIEW_MESSAGE = (
    "Please review this Work Permit hop and approve, or reply 'reject' to decline."
)


def _caller_can_act_on_review(state: ServiceRequestState, workflow_stage: str) -> bool:
    """True when the caller may approve/reject this hop.

    No ``auth_context`` (unit tests / shadow) → permissive, matching PATCH guard.
    """
    auth: AuthContext | None = state.get("auth_context")  # type: ignore[assignment]
    if auth is None:
        return True
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    return _permission_service.can_approve_work_permit(
        auth,
        workflow_stage=workflow_stage,
        in_progress_roles=in_progress_roles(backend_refs.get("sr_operations")),
    )


def _non_actor_review_message(stage: str, sr_id: str | None) -> str:
    label = STAGE_LABELS.get(stage, "the next reviewer")
    sr_bit = f" SR **{sr_id}**" if sr_id else ""
    return (
        f"Work Permit{sr_bit} is with **{label}**. "
        "You do not have an action on this hop — open the Service Request to view status, "
        "or wait for the assigned reviewer."
    )


def _build_confirmation_card(
    collected_data: dict[str, Any],
    *,
    review: bool,
    stage: str,
) -> dict[str, Any]:
    fields: list[dict[str, Any]] = []
    for key in _DISPLAY_FIELDS:
        raw_val = collected_data.get(key)
        if raw_val in (None, "", []):
            continue
        if key == "work_permit_type":
            display_val = WORK_PERMIT_TYPE_LABELS.get(raw_val, raw_val)
        else:
            display_val = raw_val
        fields.append(
            {
                "key": key,
                "label": _FIELD_LABELS.get(key, key),
                "value": display_val,
                "editable": (not review) and key in _EDITABLE_FIELDS,
            }
        )
    request_type = "Work Permit Service Request"
    if review:
        request_type = f"Work Permit — {STAGE_LABELS.get(stage, 'Review')}"
    card: dict[str, Any] = {
        "type": "confirmation_card",
        "requestType": request_type,
        "fields": fields,
        "message": _REVIEW_MESSAGE if review else _CREATE_MESSAGE,
    }
    if review:
        card["confirmAction"] = "confirm_approve_wp"
        card["rejectAction"] = "reject_wp"
    else:
        card["confirmAction"] = "confirm_create_wp"
        card["cancelAction"] = "cancel_create_wp"
    return card


@trace_node("work_permit_confirmation", "AGENT")
async def work_permit_confirmation_node(state: ServiceRequestState) -> dict[str, Any]:
    """Generate confirmation card for create or a review hop."""
    existing_status: str | None = state.get("confirmation_status")
    if existing_status == "CONFIRMED":
        return {}
    if existing_status == "REJECTED":
        extracted = state.get("extracted_fields") or {}
        corrected = state.get("corrected_fields") or {}
        if not extracted and not corrected:
            return {}

    workflow_stage: str = state.get("workflow_stage") or "CREATE_WORK_PERMIT"
    is_review = workflow_stage in WP_REVIEW_STAGES
    collected_data: dict[str, Any] = state.get("collected_data") or {}
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    sr_id = backend_refs.get("sr_id")

    if not is_review:
        missing = get_missing_fields("CREATE_WORK_PERMIT", collected_data)
        if missing:
            logger.info(
                "work_permit_confirmation_node: skipped — %d field(s) still missing: %s",
                len(missing),
                missing,
            )
            return {}

    if is_review and not _caller_can_act_on_review(state, workflow_stage):
        msg = _non_actor_review_message(workflow_stage, str(sr_id) if sr_id else None)
        logger.info(
            "work_permit_confirmation_node: non_actor_status stage=%s",
            workflow_stage,
        )
        return {
            "confirmation_required": False,
            "confirmation_status": None,
            "status": "WAITING_FOR_USER",
            "response_message": msg,
            "response_ui": {
                "type": "message",
                "message": msg,
                "workflow_stage": workflow_stage,
            },
        }

    logger.info(
        "work_permit_confirmation_node: generating confirmation card stage=%s",
        workflow_stage,
    )

    return {
        "confirmation_required": True,
        "confirmation_status": "PENDING",
        "status": "READY_TO_SUBMIT",
        "response_ui": _build_confirmation_card(
            collected_data, review=is_review, stage=workflow_stage
        ),
        "response_message": None,
    }
