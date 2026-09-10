"""Human confirmation before work permit create or review PATCH.

Create: all CREATE_WORK_PERMIT required fields must be present.
Review: no extra fields — card is built from GET-hydrated collected_data.
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
)
from app.observability.decorators import trace_node

logger = logging.getLogger(__name__)

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

    if not is_review:
        missing = get_missing_fields("CREATE_WORK_PERMIT", collected_data)
        if missing:
            logger.info(
                "work_permit_confirmation_node: skipped — %d field(s) still missing: %s",
                len(missing),
                missing,
            )
            return {}

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
