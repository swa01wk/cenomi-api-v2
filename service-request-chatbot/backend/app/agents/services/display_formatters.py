"""Display-only formatters for chatbot confirmation / preview UI.

Internal state stays ISO / enum codes; these helpers convert for user display.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

# Role / enum codes → friendly labels
ENUM_DISPLAY_LABELS: dict[str, str] = {
    "FM_MANAGER": "FM Manager",
    "OPERATIONS": "Operations",
    "DD_ENGINEER": "RDD Project Manager",
    "MALL_MANAGER": "Mall Manager",
    "ADMIN": "Admin",
    "APPROVED": "Approved",
    "APPROVED_WITH_CHANGES": "Approved with changes",
    "DISAPPROVED": "Disapproved",
    # Path B carries two codes for the same thing: the agent extracts
    # ``already_trading``, while the platform form's select stores
    # ``already_handovered`` (see handover_entry_node, which accepts both).
    # Both read as "Already Trading" to the user.
    "already_trading": "Already Trading",
    "ALREADY_TRADING": "Already Trading",
    "already_handovered": "Already Trading",
    "ALREADY_HANDOVERED": "Already Trading",
    "initiate_handover": "Initiate Handover",
    "INITIATE_HANDOVER": "Initiate Handover",
    # SR workflow-operation statuses (platform-returned enum codes rendered
    # in the "Workflow Stages" preview row).
    "FINISHED": "Completed",
    "PENDING": "Pending",
    "IN_PROCESS": "In Progress",
    "IN_PROGRESS": "In Progress",
    "APPROVED_STATUS": "Approved",
    "REJECTED": "Rejected",
    "CANCELLED": "Cancelled",
    "SUBMITTED": "Submitted",
    "REPORT_SUBMITTED": "Report Submitted",
    "COMPLETED": "Completed",
    "SKIPPED": "Skipped",
    # Workflow stages (surfaced in "Opening ... SR (stage)" messages).
    "FM_REVIEW": "FM Review",
    "RDD_REVIEW": "RDD Review",
    "CREATE_SR": "Create SR",
    "APPROVE_SR": "Approve SR",
    "DRAFT": "Draft",
    "SR_CREATED": "SR Created",
    "SR_APPROVED": "SR Approved",
    "SR_COMPLETED": "SR Completed",
    "HANDED_OVER_COMPLETED": "Handed Over — Completed",
}

_DATE_FIELD_KEYS: frozenset[str] = frozenset(
    {
        "startDate",
        "endDate",
        "unit_readiness_date",
        "expected_handover_date",
        "actual_handover_date",
        "fitout_start_date",
        "fitout_end_date",
        "trading_date",
        "actual_shop_handover_date",
        "actual_fitout_start_date",
        "actual_fitout_end_date",
        "actual_trading_date",
    }
)

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def format_date_ddmmyyyy(value: Any) -> Any:
    """Convert ISO ``YYYY-MM-DD`` (optional time) to ``DD/MM/YYYY`` for display.

    Non-date values are returned unchanged.
    """
    if value is None or value == "":
        return value
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    text = str(value).strip()
    match = _ISO_DATE_RE.match(text)
    if not match:
        return value
    year, month, day = match.group(1), match.group(2), match.group(3)
    return f"{day}/{month}/{year}"


# SCREAMING_SNAKE_CASE codes the platform may return that are not in
# ENUM_DISPLAY_LABELS. Matching these lets us humanize new statuses the moment
# the platform adds them, instead of leaking "REPORT_SUBMITTED" to the user.
_ENUM_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")


def humanize_enum_code(text: str) -> str:
    """``REPORT_SUBMITTED`` → ``Report Submitted``. Title-cases word by word."""
    return " ".join(word.capitalize() for word in text.split("_") if word)


def format_enum_label(value: Any) -> Any:
    """Humanize enum codes (e.g. ``FM_MANAGER`` → ``FM Manager``).

    Curated labels win; any other SCREAMING_SNAKE_CASE code falls back to a
    generic humanization so raw codes never reach the user. Values that are not
    enum-shaped (free text, lower-case keys the FE matches on) pass through
    untouched.
    """
    if value is None or value == "":
        return value
    text = str(value).strip()
    label = ENUM_DISPLAY_LABELS.get(text) or ENUM_DISPLAY_LABELS.get(text.upper())
    if label is not None:
        return label
    if _ENUM_CODE_RE.match(text):
        return humanize_enum_code(text)
    return value


def format_field_display_value(field_key: str, value: Any) -> Any:
    """Format a collected field value for confirmation / preview cards."""
    if value is None:
        return value
    if field_key in _DATE_FIELD_KEYS:
        return format_date_ddmmyyyy(value)
    if field_key in ("inspection_done_by", "document_status"):
        return format_enum_label(value)
    # NOTE: `already_handovered` is intentionally NOT humanized here.
    # It flows through `draft_preview.fields` into the FE bridge, which
    # writes it into Formik. The ServiceReqForms observer swaps HANDOVER ↔
    # HANDED_OVER by matching the literal codes `already_trading` /
    # `initiate_handover`. If we return "Already Trading" here the swap
    # silently breaks. Humanization for the confirmation card happens in
    # `confirmation_node._build_confirmation_card`.
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return value
