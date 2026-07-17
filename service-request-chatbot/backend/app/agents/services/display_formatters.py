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


def format_enum_label(value: Any) -> Any:
    """Humanize known enum codes (e.g. ``FM_MANAGER`` → ``FM Manager``)."""
    if value is None or value == "":
        return value
    text = str(value).strip()
    return ENUM_DISPLAY_LABELS.get(text, ENUM_DISPLAY_LABELS.get(text.upper(), value))


def format_field_display_value(field_key: str, value: Any) -> Any:
    """Format a collected field value for confirmation / preview cards."""
    if value is None:
        return value
    if field_key in _DATE_FIELD_KEYS:
        return format_date_ddmmyyyy(value)
    if field_key in ("inspection_done_by", "document_status"):
        return format_enum_label(value)
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return value
