"""Human confirmation before General Inquiry SR submission.

Responsibilities
----------------
- Check that all required fields for CREATE_SR are present in ``collected_data``.
- If complete: build a deterministic confirmation card and set PENDING.
- If incomplete: return empty so the missing-field node can handle collection.

Non-responsibilities
--------------------
- Does not call the LLM.
- Does not submit or build the API payload.
- Does not modify ``collected_data``.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.general_inquiry_schema import OPTIONAL_FIELDS, get_missing_fields
from app.agents.services.display_formatters import format_field_display_value
from app.observability.decorators import trace_node

logger = logging.getLogger(__name__)

_FIELD_LABELS: dict[str, str] = {
    "lease_code": "Lease Code",
    "brand": "Brand",
    "mall": "Mall",
    "unit_codes": "Unit Codes",
    "city": "City",
    "title": "Title",
    "description": "Description",
    "notes": "Notes",
}

_DISPLAY_FIELDS: tuple[str, ...] = (
    "lease_code",
    "brand",
    "mall",
    "unit_codes",
    "city",
    "title",
    "description",
    "notes",
)

_EDITABLE_FIELDS: frozenset[str] = frozenset(
    {"title", "description", "notes"}
)

_CONFIRMATION_MESSAGE = (
    "All required details have been collected. Please review your General Inquiry "
    "below and confirm to submit, or let me know if you'd like to change anything."
)


def _build_confirmation_card(
    collected_data: dict[str, Any],
) -> dict[str, Any]:
    """Build the confirmation card payload for the frontend."""
    fields: list[dict[str, Any]] = []
    for field_key in _DISPLAY_FIELDS:
        raw_value = collected_data.get(field_key)
        if raw_value is None and field_key in OPTIONAL_FIELDS:
            continue
        is_editable = field_key in _EDITABLE_FIELDS
        display_value = (
            raw_value if is_editable else format_field_display_value(field_key, raw_value)
        )
        fields.append(
            {
                "key": field_key,
                "label": _FIELD_LABELS.get(field_key, field_key),
                "value": display_value,
                "editable": is_editable,
            }
        )

    return {
        "type": "confirmation_card",
        "requestType": "General Inquiry",
        "stage": "CREATE_SR",
        "fields": fields,
        "message": _CONFIRMATION_MESSAGE,
    }


@trace_node("general_inquiry_confirmation", "AGENT")
async def general_inquiry_confirmation_node(state: ServiceRequestState) -> dict[str, Any]:
    """Generate a confirmation card when all required fields are present.

    No-op when:
    - Required fields are still missing.
    - ``confirmation_status`` is already ``CONFIRMED`` (user confirmed this turn).
    - ``confirmation_status`` is ``REJECTED`` with no new corrections.
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

    missing = get_missing_fields(workflow_stage, collected_data)
    non_optional_missing = [f for f in missing if f not in OPTIONAL_FIELDS]

    if non_optional_missing:
        logger.info(
            "general_inquiry_confirmation: skipped — %d required field(s) still missing: %s",
            len(non_optional_missing),
            non_optional_missing,
        )
        return {}

    logger.info(
        "general_inquiry_confirmation: all required fields present — generating card"
    )

    confirmation_card = _build_confirmation_card(collected_data)

    return {
        "confirmation_required": True,
        "confirmation_status": "PENDING",
        "status": "READY_TO_SUBMIT",
        "response_ui": confirmation_card,
        "response_message": None,
        "missing_fields": [],
        "active_field": None,
    }
