"""Confirmation card before submission for the Fitout Progress SR workflow.

Responsibilities
----------------
* Check that the stage's required fields are present in ``collected_data``.
* If complete: build the review card and set ``confirmation_status = PENDING``.
* If incomplete: return empty so ``missing_field_node`` asks the question.

Non-responsibilities
--------------------
* Does not call the LLM, submit, or modify ``collected_data``.

Why a card at all, when the flow asks only one question
------------------------------------------------------
A create by the approver is auto-approved inside the platform's create
transaction and writes ``leases.fitout_progress`` there and then; the workflow
has no cancel and no rewind. The card is one tap, not a second question, and it
is the only place an 8-instead-of-80 can be caught before it lands on the lease.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.fitout_progress_schema import (
    COMMENT_FIELD,
    OPTIONAL_FIELDS,
    PROGRESS_FIELD,
    get_missing_fields,
)
from app.agents.services.fitout_progress_suggestions_service import (
    display_date,
    format_percentage,
    parse_percentage,
    recorded_percentage,
    resolve_window,
)
from app.observability.decorators import trace_node

logger = logging.getLogger(__name__)

REQUEST_TYPE_LABEL = "Fitout Progress"

_FIELD_LABELS: dict[str, str] = {
    "lease_code": "Lease Code",
    "lease_brand_mall": "Lease",
    "company_name": "Company Name",
    "brand_name": "Brand Name",
    "mall_name": "Mall Name",
    "fitout_start_date": "Actual Fitout Start Date",
    "fitout_end_date": "Original Fitout End Date",
    PROGRESS_FIELD: "Fitout Progress %",
    COMMENT_FIELD: "Comments",
    "recorded_fitout_progress": "Currently Recorded",
}


def _display_rows(
    collected: dict[str, Any], *, editable: bool = True
) -> list[dict[str, Any]]:
    """Card rows: the lease, the window it is judged against, then the answer.

    The lease rows are read-only exactly as they are in chat — the lease is
    resolved by lookup and every identifier on the request hangs off it. The
    percentage and the note are the two fields the platform lets level 1 edit
    (``action_rules.UPDATE.editable_fields``), so they are the two the card
    lets the user fix in place.
    """
    start, end = resolve_window(collected)
    recorded = recorded_percentage(collected)

    rows: list[dict[str, Any]] = [
        {
            "key": "lease_brand_mall",
            "label": _FIELD_LABELS["lease_brand_mall"],
            "value": str(collected.get("lease_brand_mall") or ""),
            "editable": False,
        },
        {
            "key": "lease_code",
            "label": _FIELD_LABELS["lease_code"],
            "value": str(collected.get("lease_code") or ""),
            "editable": False,
        },
    ]
    if start:
        rows.append(
            {
                "key": "fitout_start_date",
                "label": _FIELD_LABELS["fitout_start_date"],
                "value": display_date(start),
                "editable": False,
            }
        )
    if end:
        rows.append(
            {
                "key": "fitout_end_date",
                "label": _FIELD_LABELS["fitout_end_date"],
                "value": display_date(end),
                "editable": False,
            }
        )
    if recorded is not None:
        rows.append(
            {
                "key": "recorded_fitout_progress",
                "label": _FIELD_LABELS["recorded_fitout_progress"],
                "value": f"{format_percentage(recorded)}%",
                "editable": False,
            }
        )

    raw_percentage = collected.get(PROGRESS_FIELD)
    percentage_row: dict[str, Any] = {
        "key": PROGRESS_FIELD,
        "label": _FIELD_LABELS[PROGRESS_FIELD],
        # An editable row round-trips through an input, so it carries the raw
        # machine value the payload will use, not a formatted one.
        "value": "" if raw_percentage is None else str(raw_percentage),
        "editable": editable,
    }
    if editable:
        percentage_row["inputType"] = "number"
    rows.append(percentage_row)
    comment = collected.get(COMMENT_FIELD)
    if comment:
        rows.append(
            {
                "key": COMMENT_FIELD,
                "label": _FIELD_LABELS[COMMENT_FIELD],
                "value": str(comment),
                "editable": editable,
            }
        )
    return rows


def _build_confirmation_card(
    collected: dict[str, Any],
    workflow_stage: str,
    *,
    rejecting: bool = False,
    sr_id: str = "",
) -> dict[str, Any]:
    raw = collected.get(PROGRESS_FIELD)
    parsed = parse_percentage(raw)
    # An unparseable value still gets shown rather than hidden: validation is
    # what refuses it, and the user needs to see what it read.
    percentage = format_percentage(parsed) if parsed is not None else str(raw or "")

    if rejecting:
        subject = f"SR **{sr_id}**" if sr_id else "this request"
        message = (
            f"Reject {subject}? This is final — the request closes and the "
            "lease is left as it is."
        )
        confirm_action = "confirm_reject_fitout_progress"
        confirm_label = "Reject"
    elif workflow_stage == "APPROVE_SR":
        message = (
            f"Approve this request at **{percentage}%**? The lease is updated "
            "as soon as you approve."
        )
        confirm_action = "confirm_approve_fitout_progress"
        confirm_label = "Approve"
    else:
        message = (
            f"Recording fit-out progress at **{percentage}%**. This goes "
            "straight onto the lease — confirm, or give me a different number."
        )
        confirm_action = "confirm_create_fitout_progress"
        confirm_label = "Confirm"

    return {
        "type": "confirmation_card",
        "requestType": REQUEST_TYPE_LABEL,
        "stage": workflow_stage,
        # On a rejection nothing is editable: the number is not what is being
        # decided, and letting it be edited here would suggest the rejection
        # carries a new value to the platform. It does not.
        "fields": _display_rows(collected, editable=not rejecting),
        "message": message,
        "confirmAction": confirm_action,
        "confirmLabel": confirm_label,
    }


@trace_node("fitout_progress_confirmation", "AGENT")
async def fitout_progress_confirmation_node(state: ServiceRequestState) -> dict[str, Any]:
    """Render the review card once the percentage has been answered."""
    existing_status: str | None = state.get("confirmation_status")
    if existing_status == "CONFIRMED":
        return {}
    if existing_status == "REJECTED":
        extracted = state.get("extracted_fields") or {}
        corrected = state.get("corrected_fields") or {}
        if not extracted and not corrected:
            return {}

    collected: dict[str, Any] = state.get("collected_data") or {}
    workflow_stage: str = state.get("workflow_stage") or "CREATE_SR"
    backend_refs: dict[str, Any] = state.get("backend_refs") or {}
    rejecting = backend_refs.get("fitout_progress_action") == "reject"

    missing = get_missing_fields(workflow_stage, collected)
    non_optional_missing = [f for f in missing if f not in OPTIONAL_FIELDS]
    if non_optional_missing:
        logger.info(
            "fitout_progress_confirmation: skipped — missing %s", non_optional_missing
        )
        return {}

    logger.info(
        "fitout_progress_confirmation: ready for stage=%s rejecting=%s lease_code=%s",
        workflow_stage,
        rejecting,
        collected.get("lease_code"),
    )
    return {
        "confirmation_required": True,
        "confirmation_status": "PENDING",
        "status": "READY_TO_SUBMIT",
        "response_ui": _build_confirmation_card(
            collected,
            workflow_stage,
            rejecting=rejecting,
            sr_id=str(backend_refs.get("sr_id") or ""),
        ),
        "response_message": None,
    }
