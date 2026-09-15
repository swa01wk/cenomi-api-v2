"""Confirmation card before submission for the Fitout Confirmation SR workflow.

Responsibilities
----------------
* Check that all required fields for the current workflow stage + sub_category
  are present in collected_data.
* If complete: build a confirmation card and set status PENDING.
* If incomplete: return empty so the missing-field node handles collection.

Non-responsibilities
--------------------
* Does not call the LLM.
* Does not submit or build the API payload.
* Does not modify collected_data.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.fitout_confirm_schema import (
    OPTIONAL_FIELDS,
    SUBCAT_CONFIG,
    get_missing_fields,
)
from app.agents.services.display_formatters import format_field_display_value
from app.observability.decorators import trace_node

logger = logging.getLogger(__name__)

# ── Field labels ──────────────────────────────────────────────────────────────

_FIELD_LABELS: dict[str, str] = {
    # Lease reference
    "lease_code": "Lease Code",
    "lease_brand_mall": "Lease",
    "brand": "Brand",
    "mall": "Mall",
    # FOS radio + dates
    "fitout_start_done": "Fitout Start Done?",
    "actual_fitout_start_date": "Actual Fitout Start Date",
    "planned_fitout_start_date": "Planned Fitout Start Date",
    "planned_fitout_end_date": "Planned Fitout End Date",
    "planned_trading_date": "Planned Trading Date",
    # END radio + dates
    "fitout_complete_done": "Fitout Complete Done?",
    "actual_fitout_complete_date": "Actual Fitout Complete Date",
    "planned_fitout_complete_date": "Planned Fitout Complete Date",
    # TRADING radio + dates
    "trading_done": "Trading Done?",
    "actual_trading_date": "Actual Trading Date",
    # Shared
    "delay_reason": "Delay Reason",
    "comments": "Comments",
    # Readonly reference dates
    "actual_handover_date_readonly": "Actual Handover Date (Prior Milestone)",
    "actual_fitout_start_date_readonly": "Actual Fitout Start Date (Prior Milestone)",
    "actual_fitout_end_date_readonly": "Actual Fitout End Date (Prior Milestone)",
    "base_fitout_start_date": "Base Fitout Start Date",
    "base_fitout_end_date": "Base Fitout End Date",
    "base_trading_date": "Base Trading Date",
}

_REQUEST_TYPE_LABELS: dict[str, str] = {
    "FIT_OUT_CONFIRM_FOS": "Fitout Start Confirmation",
    "FIT_OUT_CONFIRM_END": "Fitout End Confirmation",
    "FIT_OUT_CONFIRM_TRADING": "Trading Confirmation",
}


def _build_display_fields(
    sub_category: str, collected: dict[str, Any], radio_value: str
) -> list[str]:
    """Return ordered list of fields to display on the confirmation card."""
    cfg = SUBCAT_CONFIG.get(sub_category, {})
    display = ["lease_code", "lease_brand_mall"]

    radio_field = cfg.get("radio_field", "")
    if radio_field:
        display.append(radio_field)

    if radio_value == "yes":
        actual = cfg.get("actual_date_field")
        if actual:
            display.append(actual)
    elif radio_value == "no":
        display.extend(cfg.get("planned_date_fields", []))
        display.append("delay_reason")

    if cfg.get("has_comments"):
        display.append("comments")

    return display


# Rows the card lets the user fix in place, per sub-category. The lease rows are
# deliberately absent: the lease is resolved by lookup and every identifier on
# the request hangs off it, so it is read-only here exactly as it is in chat.
def _editable_keys(sub_category: str) -> set[str]:
    cfg = SUBCAT_CONFIG.get(sub_category, {})
    keys: set[str] = {"delay_reason"}
    radio_field = cfg.get("radio_field", "")
    if radio_field:
        keys.add(radio_field)
    actual = cfg.get("actual_date_field")
    if actual:
        keys.add(actual)
    keys.update(cfg.get("planned_date_fields", []))
    if cfg.get("has_comments"):
        keys.add("comments")
    return keys


# The radio is a two-way answer, not free text — the card offers the same two
# values the chat question does, so flipping it here cannot produce a value the
# validator will reject.
_RADIO_OPTIONS: tuple[str, ...] = ("yes", "no")


def _is_date_key(field_key: str) -> bool:
    return field_key.endswith("_date") and not field_key.endswith("_readonly")


def _build_confirmation_card(
    collected: dict[str, Any],
    sub_category: str,
    workflow_stage: str,
) -> dict[str, Any]:
    cfg = SUBCAT_CONFIG.get(sub_category, {})
    radio_field = cfg.get("radio_field", "")
    radio_value = collected.get(radio_field, "")

    display_keys = _build_display_fields(sub_category, collected, radio_value)

    editable_keys = _editable_keys(sub_category)

    fields: list[dict[str, Any]] = []
    for field_key in display_keys:
        raw_value = collected.get(field_key)
        is_editable = field_key in editable_keys
        # An editable row round-trips through an input, so it carries the raw
        # machine value (YYYY-MM-DD, "yes"/"no"). A read-only row carries the
        # formatted string the user reads.
        value = (
            "" if raw_value is None else str(raw_value)
        ) if is_editable else format_field_display_value(field_key, raw_value)
        row: dict[str, Any] = {
            "key": field_key,
            "label": _FIELD_LABELS.get(field_key, field_key),
            "value": value,
            "editable": is_editable,
        }
        if is_editable and field_key == radio_field:
            row["options"] = list(_RADIO_OPTIONS)
        elif is_editable and _is_date_key(field_key):
            row["inputType"] = "date"
        fields.append(row)

    request_type = _REQUEST_TYPE_LABELS.get(sub_category, "Fitout Confirmation SR")
    stage_label = "Approve" if workflow_stage == "APPROVE_SR" else "Create"
    message = (
        f"All required details have been collected. Please review and confirm the "
        f"{request_type} ({stage_label}), or let me know if you want to change anything."
    )

    card: dict[str, Any] = {
        "type": "confirmation_card",
        "requestType": request_type,
        "stage": workflow_stage,
        "fields": fields,
        "message": message,
    }
    if workflow_stage == "APPROVE_SR":
        card["confirmAction"] = "confirm_approve_sr"
        card["confirmLabel"] = "Approve"
    else:
        card["confirmAction"] = "confirm_create_fitout_confirm"
    return card


@trace_node("fitout_confirm_confirmation", "AGENT")
async def fitout_confirm_confirmation_node(state: ServiceRequestState) -> dict[str, Any]:
    """Generate the confirmation card when all required fields are present."""
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
    sub_category: str = state.get("sub_category") or ""

    if sub_category not in SUBCAT_CONFIG:
        logger.warning(
            "fitout_confirm_confirmation: unknown sub_category=%s — skipping", sub_category
        )
        return {}

    missing = get_missing_fields(workflow_stage, sub_category, collected)
    non_optional_missing = [f for f in missing if f not in OPTIONAL_FIELDS]
    if non_optional_missing:
        logger.info(
            "fitout_confirm_confirmation: skipped — %d required field(s) missing: %s",
            len(non_optional_missing),
            non_optional_missing,
        )
        return {}

    logger.info(
        "fitout_confirm_confirmation: all fields present for stage=%s sub_category=%s",
        workflow_stage,
        sub_category,
    )

    card = _build_confirmation_card(collected, sub_category, workflow_stage)
    return {
        "confirmation_required": True,
        "confirmation_status": "PENDING",
        "status": "READY_TO_SUBMIT",
        "response_ui": card,
        "response_message": None,
    }
