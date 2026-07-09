"""Human confirmation before submission.

Responsibilities
----------------
* Check that all required fields for the current workflow stage are present
  in ``collected_data``.
* If complete: build a deterministic, stage-specific confirmation card and
  transition the graph into the READY_TO_SUBMIT / PENDING confirmation state.
* If incomplete: return empty so the missing-field node can handle collection.

Non-responsibilities
--------------------
* Does not call the LLM.
* Does not submit or build the API payload.
* Does not modify ``collected_data``.
* Does not process the user's yes/no response (that is the supervisor's job).
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.handover_schema import get_missing_fields
from app.observability.decorators import trace_node

logger = logging.getLogger(__name__)

# ── Display configuration — CREATE_SR ─────────────────────────────────────────

_FIELD_LABELS: dict[str, str] = {
    # CREATE_SR
    "lease_code": "Lease Code",
    "brand": "Brand",
    "mall": "Mall",
    "unit_codes": "Unit Codes",
    "city": "City",
    "contracted_area": "Contracted Area (sqm)",
    "title": "Title",
    "description": "Description",
    "startDate": "Inspection Start Date",
    "endDate": "Inspection End Date",
    "inspection_done_by": "Inspection Done By",
    "notes": "Notes",
    "comments": "Comments",
    # FM_REVIEW
    "unit_readiness_date": "Unit Readiness Date",
    "expected_handover_date": "Expected Handover Date",
    # RDD_REVIEW
    "guideLineLink": "Guidelines Link",
    "document_status": "Document Status",
    "actual_handover_date": "Actual Handover Date",
    "fitout_start_date": "Fitout Start Date",
    "fitout_end_date": "Fitout End Date",
    "trading_date": "Trading Start Date (Auto)",
}

# Fields the user may inline-edit on the CREATE_SR card.
_CREATE_SR_EDITABLE: frozenset[str] = frozenset(
    {"title", "description", "startDate", "endDate", "inspection_done_by", "notes", "comments"}
)

# Fields the user may inline-edit on the FM_REVIEW card.
_FM_REVIEW_EDITABLE: frozenset[str] = frozenset({"unit_readiness_date", "comments"})

# Fields the user may inline-edit on the RDD_REVIEW card.
# trading_date is intentionally excluded — it is auto-computed (read-only display).
# document_status renders as a <select> on the frontend (options emitted separately).
_RDD_REVIEW_EDITABLE: frozenset[str] = frozenset(
    {"guideLineLink", "document_status", "actual_handover_date",
     "fitout_start_date", "fitout_end_date", "comments"}
)

# Ordered display fields per stage (backend-only IDs excluded).
_CREATE_SR_DISPLAY_FIELDS: tuple[str, ...] = (
    "lease_code",
    "brand",
    "mall",
    "unit_codes",
    "city",
    "contracted_area",
    "title",
    "description",
    "startDate",
    "endDate",
    "inspection_done_by",
    "notes",
    "comments",
)

_FM_REVIEW_DISPLAY_FIELDS: tuple[str, ...] = (
    "lease_code",
    "brand",
    "mall",
    "unit_readiness_date",
    "expected_handover_date",
    "comments",
)

_RDD_REVIEW_DISPLAY_FIELDS: tuple[str, ...] = (
    "lease_code",
    "brand",
    "mall",
    "guideLineLink",
    "document_status",
    "actual_handover_date",
    "fitout_start_date",
    "fitout_end_date",
    "trading_date",    # read-only — auto-computed value shown here
    "comments",
)

# Backward-compat alias — imported by existing unit tests and external consumers.
_CONFIRMATION_DISPLAY_FIELDS: tuple[str, ...] = _CREATE_SR_DISPLAY_FIELDS

_STAGE_DISPLAY_CONFIG: dict[str, tuple[tuple[str, ...], frozenset[str], str]] = {
    "CREATE_SR": (
        _CREATE_SR_DISPLAY_FIELDS,
        _CREATE_SR_EDITABLE,
        "All required details have been collected. Please review and confirm the "
        "Handover Service Request, or let me know if you want to change anything.",
    ),
    "FM_REVIEW": (
        _FM_REVIEW_DISPLAY_FIELDS,
        _FM_REVIEW_EDITABLE,
        "FM review details are ready. Please confirm the readiness dates and "
        "ensure required documents are uploaded before proceeding.",
    ),
    "RDD_REVIEW": (
        _RDD_REVIEW_DISPLAY_FIELDS,
        _RDD_REVIEW_EDITABLE,
        "RDD review details are ready. Please confirm the dates, document status, "
        "guidelines link, and ensure the handover report is uploaded. "
        "Trading start date has been auto-calculated as fitout end + 8 days.",
    ),
}

_REQUEST_TYPE_LABELS: dict[str, str] = {
    "CREATE_SR": "Handover Service Request",
    "FM_REVIEW": "FM Review — Save / Approve",
    "RDD_REVIEW": "RDD Report Submission",
}

# ── Internal helpers ───────────────────────────────────────────────────────────


# Maps (workflow_stage, fm_action/rdd_action) → the confirmAction string the
# frontend must send back so the entry node sets the action AND confirms in
# one turn (avoids the multi-turn persistence problem).
_CONFIRM_ACTION_MAP: dict[tuple[str, str], str] = {
    ("FM_REVIEW", "save_progress"): "confirm_fm_save",
    ("FM_REVIEW", "approve"):       "confirm_fm_approve",
    ("RDD_REVIEW", "submit"):       "confirm_rdd_submit",
    ("RDD_REVIEW", "final_approve"): "confirm_rdd_final_approve",
}


def _build_confirmation_card(
    collected_data: dict[str, Any],
    workflow_stage: str,
    backend_refs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, stage-specific confirmation card from *collected_data*.

    The card is purely derived from the input — identical inputs always
    produce an identical card, making it safe to regenerate on retry.

    Parameters
    ----------
    collected_data:
        The current draft field values.
    workflow_stage:
        Determines which fields and labels are shown on the card.
    backend_refs:
        Optional backend_refs dict; used to embed ``confirmAction`` for
        FM/RDD stages so the frontend sends the correct single-turn confirm.

    Returns
    -------
    dict
        ``response_ui`` payload with ``type = "confirmation_card"``.
    """
    display_fields, editable_fields, message = _STAGE_DISPLAY_CONFIG.get(
        workflow_stage,
        (_CREATE_SR_DISPLAY_FIELDS, _CREATE_SR_EDITABLE, _STAGE_DISPLAY_CONFIG["CREATE_SR"][2]),
    )

    # Enum fields that must render as a <select> dropdown on the frontend.
    _ENUM_OPTIONS: dict[str, list[str]] = {
        "document_status": ["APPROVED", "APPROVED_WITH_CHANGES"],
    }

    fields: list[dict[str, Any]] = []
    for field_key in display_fields:
        row: dict[str, Any] = {
            "key": field_key,
            "label": _FIELD_LABELS.get(field_key, field_key),
            "value": collected_data.get(field_key),
            "editable": field_key in editable_fields,
        }
        if field_key in _ENUM_OPTIONS:
            row["options"] = _ENUM_OPTIONS[field_key]
        fields.append(row)

    card: dict[str, Any] = {
        "type": "confirmation_card",
        "requestType": _REQUEST_TYPE_LABELS.get(workflow_stage, "Handover Service Request"),
        "stage": workflow_stage,
        "fields": fields,
        "message": message,
    }

    # Embed the stage-specific confirmAction so the frontend knows which action
    # string to send back instead of the generic "confirm".
    # FM_REVIEW defaults to approve when fm_action was never set (e.g. user
    # reached the card via field collection rather than an Approve chip).
    stage_action: str = ""
    if workflow_stage == "FM_REVIEW":
        stage_action = (backend_refs or {}).get("fm_action") or "approve"
    elif workflow_stage == "RDD_REVIEW":
        stage_action = (backend_refs or {}).get("rdd_action") or ""
    confirm_action = _CONFIRM_ACTION_MAP.get((workflow_stage, stage_action))
    if confirm_action:
        card["confirmAction"] = confirm_action

    return card


# ── Node ───────────────────────────────────────────────────────────────────────


@trace_node("confirmation", "AGENT")
async def confirmation_node(state: ServiceRequestState) -> dict[str, Any]:
    """Generate a stage-specific confirmation card when all required fields are present.

    The node is a no-op (returns ``{}``) in three situations:
    - Required fields are still missing (missing_field_node handles collection).
    - ``confirmation_status`` is already ``"CONFIRMED"`` — the user has already
      confirmed on this turn (set by the entry node); overwriting with
      ``"PENDING"`` would undo the confirmation and block submission.
    - ``confirmation_status`` is ``"REJECTED"`` with no new corrections — the
      graph routes to response_generation to ask what to change.

    Reads from state
    ----------------
    ``collected_data``       — draft field values to confirm.
    ``workflow_stage``       — determines which required fields and display
                               fields apply (defaults to ``"CREATE_SR"``).
    ``confirmation_status``  — existing status; CONFIRMED/REJECTED are preserved.

    Writes to state
    ---------------
    ``confirmation_required``  — ``True`` when all required fields are present.
    ``confirmation_status``    — ``"PENDING"`` (awaiting explicit user response).
    ``status``                 — ``"READY_TO_SUBMIT"``.
    ``response_ui``            — stage-specific confirmation card for the frontend.
    """
    existing_status: str | None = state.get("confirmation_status")
    if existing_status == "CONFIRMED":
        return {}
    if existing_status == "REJECTED":
        extracted = state.get("extracted_fields") or {}
        corrected = state.get("corrected_fields") or {}
        if not extracted and not corrected:
            return {}

    collected_data: dict[str, Any] = state.get("collected_data") or {}
    workflow_stage: str = state.get("workflow_stage") or "CREATE_SR"
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})

    missing = get_missing_fields(workflow_stage, collected_data)

    if missing:
        logger.info(
            "confirmation_node: skipped — %d required field(s) still missing for stage=%s: %s",
            len(missing),
            workflow_stage,
            missing,
        )
        return {}

    logger.info(
        "confirmation_node: all required fields present for stage=%s — generating confirmation card",
        workflow_stage,
    )

    confirmation_card = _build_confirmation_card(collected_data, workflow_stage, backend_refs)

    return {
        "confirmation_required": True,
        "confirmation_status": "PENDING",
        "status": "READY_TO_SUBMIT",
        "response_ui": confirmation_card,
        "response_message": None,
    }
