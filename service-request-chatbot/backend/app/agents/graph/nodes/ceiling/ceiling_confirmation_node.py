"""Confirmation card for the Ceiling Inspection create flow.

Shows the inspection name, lease, title, description, dates, comments, optional
notes and attachments, plus the inspection-done-by role id as a read-only row.
The platform form looks that id up in the roles catalog; the chat card sends
the same id so both sides show one name. The user never chooses that role.

When the RDD Project Manager raises the request, the draft inspection report
and its status (Approved / Approved with changes) are collected first: the
platform auto-approves RDD PM's own level at create only when both come with
the request. The form's required comments cover "approved with changes".

Non-responsibilities
--------------------
* Does not call the LLM.
* Does not build or submit the payload.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.nodes.ceiling.ceiling_report import (
    document_requirement,
    status_from_turn,
    status_suggestions,
)
from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.ceiling_schema import (
    DOC_STATUS_APPROVED_WITH_CHANGES,
    DRAFT_REPORT_STATUS_KEY,
    OPTIONAL_FIELDS,
    STAGE_CREATE,
    creator_uploads_report,
    draft_report_doc_type,
    get_missing_fields,
    has_draft_report,
    inspection_done_by_role_id,
    is_resolved_sub_category,
    sub_category_label,
)
from app.agents.services.display_formatters import format_field_display_value
from app.observability.decorators import trace_node

logger = logging.getLogger(__name__)

_FIELD_LABELS: dict[str, str] = {
    "lease_brand_mall": "Lease",
    "title": "Title",
    "description": "Description",
    "startDate": "Start Date",
    "endDate": "End Date",
    "comments": "Comments",
    "notes": "Notes",
}

_DISPLAY_ORDER: tuple[str, ...] = (
    "lease_brand_mall",
    "title",
    "description",
    "startDate",
    "endDate",
    "comments",
    "notes",
)

# The lease is resolved by lookup and every identifier on the request hangs off
# it, so it stays read-only on the card exactly as it is in chat.
_EDITABLE_KEYS: frozenset[str] = frozenset(
    {"title", "description", "startDate", "endDate", "comments", "notes"}
)
_DATE_KEYS: frozenset[str] = frozenset({"startDate", "endDate"})


def build_confirmation_card(
    collected: dict[str, Any],
    sub_category: str,
    documents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    label = sub_category_label(sub_category)
    fields: list[dict[str, Any]] = [
        {
            "key": "sub_category",
            "label": "Inspection",
            "value": label,
            "editable": False,
        }
    ]
    for key in _DISPLAY_ORDER:
        raw = collected.get(key)
        if key == "notes" and raw in (None, ""):
            continue
        editable = key in _EDITABLE_KEYS
        value = ("" if raw is None else str(raw)) if editable else format_field_display_value(key, raw)
        row: dict[str, Any] = {
            "key": key,
            "label": _FIELD_LABELS.get(key, key),
            "value": value,
            "editable": editable,
        }
        if editable and key in _DATE_KEYS:
            row["inputType"] = "date"
        fields.append(row)

    fields.append(
        {
            "key": "inspection_done_by",
            "label": "Inspection Done By",
            "value": inspection_done_by_role_id(collected),
            "editable": False,
        }
    )

    report_status = collected.get(DRAFT_REPORT_STATUS_KEY)
    if report_status:
        fields.append(
            {
                "key": DRAFT_REPORT_STATUS_KEY,
                "label": f"{label} Report",
                "value": (
                    "Approved with changes"
                    if report_status == DOC_STATUS_APPROVED_WITH_CHANGES
                    else "Approved"
                ),
                "editable": False,
            }
        )

    draft_type = draft_report_doc_type(sub_category)
    attached = [
        str(d.get("file_name") or d.get("document_type_id") or "")
        for d in (documents or [])
        if isinstance(d, dict)
        and d.get("document_id")
        and not (report_status and d.get("document_type_id") == draft_type)
    ]
    if attached:
        fields.append(
            {
                "key": "documents",
                "label": "Other Documents",
                "value": ", ".join(a for a in attached if a),
                "editable": False,
            }
        )

    return {
        "type": "confirmation_card",
        "requestType": label,
        "stage": STAGE_CREATE,
        "fields": fields,
        "message": (
            f"All required details have been collected. Please review and confirm the "
            f"{label}, or let me know if you want to change anything."
        ),
        "confirmAction": "confirm_create_ceiling_inspection",
    }


@trace_node("ceiling_confirmation", "AGENT")
async def ceiling_confirmation_node(state: ServiceRequestState) -> dict[str, Any]:
    """Show the confirmation card once every required create field is present."""
    existing = state.get("confirmation_status")
    if existing == "CONFIRMED":
        return {}
    if existing == "REJECTED" and not (
        state.get("extracted_fields") or state.get("corrected_fields")
    ):
        return {}

    sub_category = state.get("sub_category") or ""
    if not is_resolved_sub_category(sub_category):
        logger.warning("ceiling_confirmation: unresolved sub_category=%s", sub_category)
        return {}

    collected: dict[str, Any] = state.get("collected_data") or {}
    missing = [
        f
        for f in get_missing_fields(STAGE_CREATE, sub_category, collected)
        if f not in OPTIONAL_FIELDS
    ]
    if missing:
        logger.info("ceiling_confirmation: skipped — missing %s", missing)
        return {}

    report_patch: dict[str, Any] = {}
    if creator_uploads_report(state.get("auth_context")):
        waiting, collected = _report_step(state, collected, sub_category)
        if waiting is not None:
            return waiting
        report_patch = {"collected_data": collected}

    card = build_confirmation_card(collected, sub_category, state.get("documents"))
    return {
        **report_patch,
        "confirmation_required": True,
        "confirmation_status": "PENDING",
        "status": "READY_TO_SUBMIT",
        "response_ui": card,
        "response_message": None,
    }


def _report_wait(message: str, decorations: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "WAITING_FOR_USER",
        "confirmation_status": None,
        "confirmation_required": False,
        "active_field": None,
        "response_message": message,
        "response_ui": {"type": "message", "message": message, "verbatim": True},
        "response_decorations": decorations,
    }


def _report_step(
    state: ServiceRequestState, collected: dict[str, Any], sub_category: str
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """RDD PM create: ask for the draft report, then its status.

    Returns ``(waiting_patch, collected)``; ``waiting_patch`` is None once both
    are in, and ``collected`` then carries the status.
    """
    label = sub_category_label(sub_category)
    if not has_draft_report(state.get("documents"), sub_category):
        return (
            _report_wait(
                f"As the RDD Project Manager, please upload the {label} report. "
                "Your level is approved when you submit, so the report goes with the request.",
                {"document_requirement": document_requirement(state, sub_category)},
            ),
            collected,
        )
    status = collected.get(DRAFT_REPORT_STATUS_KEY) or status_from_turn(state)
    if not status:
        return (
            _report_wait(
                f"The {label} report is uploaded. Is it approved, or approved with changes?",
                # Keep the card: the post-upload turn is silent, and the client
                # only shows a silent reply that is a card.
                {
                    "document_requirement": document_requirement(state, sub_category),
                    **status_suggestions(),
                },
            ),
            collected,
        )
    return None, {**collected, DRAFT_REPORT_STATUS_KEY: status}
