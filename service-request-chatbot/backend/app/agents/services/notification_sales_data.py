"""Sales-data notification classification and handoff helpers.

The Notification Agent's OPEN path for FIT_OUT_AND_HANDOVER seeds ``sr_id`` and
rides ``sr_status_sync``. Sales-data notifications have **no SR id** —
``text_content.id`` is the lease code (PMSTenantCode). This module turns that
ref into the ``notification_context`` the Sales Data Agent already consumes.
"""

from __future__ import annotations

import re
from typing import Any

# Inbox group key + Open ``sub_category`` the FE should send.
SALES_DATA_SUB_CATEGORY = "SALES_DATA"

# Keys / sub-categories that mean "this Open is a sales-data notification".
SALES_DATA_OPEN_SUBS: frozenset[str] = frozenset(
    {
        "SALES_DATA",
        "SALES_DATA_PENDING",
        "SALES_DATA_NEED_REUPLOAD",
        "SALES_DATA_NEED_UPLOAD_SUPPORTING_DOCUMENT",
        "SALES_DATA_NEED_UPLOAD_AUDITED_SALES",
        "SALES_DATA_SUBMITTED",
        "BULK_UPLOAD",
        "MANUAL_ENTRY",
        "SUPPORTING_DOC",
        "ADMIN_REVIEW",
    }
)

_SUPPORTING_DOC_KEYS: frozenset[str] = frozenset(
    {
        "SALES_DATA_NEED_UPLOAD_SUPPORTING_DOCUMENT",
        "SALES_DATA_NEED_UPLOAD_AUDITED_SALES",
        "SUPPORTING_DOC",
        "NEED_UPLOAD_SUPPORTING_DOCUMENT",
        "NEED_UPLOAD_AUDITED_SALES",
    }
)

_ADMIN_KEYS: frozenset[str] = frozenset(
    {
        "SALES_DATA_SUBMITTED",
        # Tenant -> admin "uploaded" events. These are currently not
        # ActionRequired on the Go side (sales_data_usecase.go tenant branch)
        # so they don't surface in the agent inbox today, but if that ever
        # changes, Opening them must land the admin in review, not upload.
        "SALES_DATA_UPLOADED_SUPPORTING_DOCUMENT",
        "SALES_DATA_UPLOADED_AUDITED_SALES_DATA",
        "SALES_DATA_REUPLOADED",
        "ADMIN_REVIEW",
        "REVIEW_SALES_DATA",
    }
)

# Admin -> tenant approval notice. ActionRequired=true (same branch as the
# NEED_* keys) so it IS openable from the tenant's inbox, but it's purely
# informational -- Opening it must not seed an upload flow.
_APPROVAL_KEYS: frozenset[str] = frozenset({"SALES_DATA_APPROVED"})

_PERIOD_AS_OF_RE = re.compile(
    r"as of\s+(?:month\s+)?([A-Za-z]+(?:\s+\d{4})?)",
    re.IGNORECASE,
)
_PERIOD_FOR_MONTH_RE = re.compile(
    r"\b(?:for|of)\s+month\s+([A-Za-z]+(?:\s+\d{4})?)",
    re.IGNORECASE,
)
# Admin-facing review notifications ("SALES_DATA_SUBMITTED") use a template
# entirely different from the tenant "as of month X" wording above —
# "Please review the sales data for {brand·mall} corresponding to {period}" —
# and cenomi-api's own getYearMonthDate() picks ONE of three period shapes
# depending on the lease's reporting frequency (sales_data_sync_usecase.go):
#   monthly -> "month September"      (no year — always the current one)
#   yearly  -> "year 2026"
#   other   -> "5/9/2026"             (D/M/YYYY, NOT zero-padded)
# The monthly case is the common one and wasn't matched at all before, so
# period_hint/period_date came back empty for most real admin notifications
# and the whole lease history showed instead of the one month being reviewed.
_PERIOD_CORRESPONDING_TO_DATE_RE = re.compile(
    r"corresponding to\s+(\d{1,2})/(\d{1,2})/(\d{4})",
    re.IGNORECASE,
)
_PERIOD_CORRESPONDING_TO_MONTH_RE = re.compile(
    r"corresponding to\s+month\s+([A-Za-z]+)",
    re.IGNORECASE,
)
_PERIOD_CORRESPONDING_TO_YEAR_RE = re.compile(
    r"corresponding to\s+year\s+(\d{4})",
    re.IGNORECASE,
)
_MONTH_NAME_TO_NUM = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


def extract_period_date(text: str | None) -> str | None:
    """Year-month prefix ("2024-12") from an admin "corresponding to ..."
    notification — day-level precision isn't always available (the monthly-
    frequency case only ever names a month, no year), so callers should
    match rows by year-month, not by exact date.
    """
    raw = (text or "").strip()
    if not raw:
        return None

    match = _PERIOD_CORRESPONDING_TO_DATE_RE.search(raw)
    if match:
        day, month, year = match.groups()
        try:
            from datetime import date
            return date(int(year), int(month), int(day)).isoformat()[:7]
        except ValueError:
            return None

    match = _PERIOD_CORRESPONDING_TO_MONTH_RE.search(raw)
    if match:
        month_num = _MONTH_NAME_TO_NUM.get(match.group(1).strip().lower())
        if month_num:
            from datetime import date
            today = date.today()
            # cenomi-api's monthly-frequency case never names a year — the
            # notification is always about the most recent occurrence of
            # that month, i.e. this year unless that month hasn't happened
            # yet this year, in which case it was last year.
            year = today.year if month_num <= today.month else today.year - 1
            return f"{year:04d}-{month_num:02d}"

    match = _PERIOD_CORRESPONDING_TO_YEAR_RE.search(raw)
    if match:
        return match.group(1)

    return None


def is_sales_data_notification_ref(ref: dict[str, Any] | None) -> bool:
    """True when ``active_notification_id`` should hand off to sales_data_agent."""
    if not isinstance(ref, dict):
        return False
    sub = str(ref.get("sub_category") or "").strip().upper()
    if sub in SALES_DATA_OPEN_SUBS:
        return True
    ntype = str(ref.get("notification_type") or "").strip().upper()
    if ntype == "SALES_DATA" or ntype.startswith("SALES_DATA_"):
        return True
    key = str(ref.get("key") or "").strip().upper()
    return key.startswith("SALES_DATA_")


def extract_sales_period_hint(text: str | None) -> str:
    """Pull a short period hint from the EN notification body.

    Platform PENDING copy is ``Please submit sales data as of %s for %s.``
    ISO dates are not stored on the notification — only this phrase.
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    match = _PERIOD_AS_OF_RE.search(raw) or _PERIOD_FOR_MONTH_RE.search(raw)
    if not match:
        return ""
    token = match.group(1).strip()
    if token.lower().startswith("month"):
        return token
    return f"month {token}"


def sales_data_intent_for_ref(ref: dict[str, Any]) -> str:
    """Domain intent after OPEN_NOTIFICATION routing."""
    token = str(
        ref.get("key") or ref.get("sub_category") or ref.get("notification_type") or ""
    ).strip().upper()
    if token in _APPROVAL_KEYS:
        return "ACKNOWLEDGE_APPROVAL"
    if token in _SUPPORTING_DOC_KEYS or token.endswith(
        "NEED_UPLOAD_SUPPORTING_DOCUMENT"
    ) or token.endswith("NEED_UPLOAD_AUDITED_SALES"):
        return "UPLOAD_SUPPORTING_DOCUMENT"
    if token in _ADMIN_KEYS:
        return "REVIEW_SALES_DATA"
    if token in {"SALES_DATA_NEED_REUPLOAD", "NEED_REUPLOAD"}:
        return "UPLOAD_SALES_DATA"
    return "UPLOAD_SALES_DATA"


def build_sales_data_notification_context(ref: dict[str, Any]) -> dict[str, str]:
    """Handoff payload the Sales Data Agent reads from ``backend_refs``."""
    lease_code = str(ref.get("id") or ref.get("lease_code") or "").strip()
    raw_message = str(ref.get("message") or ref.get("en") or "")
    period_hint = str(ref.get("period_hint") or ref.get("period_label") or "").strip()
    if not period_hint:
        period_hint = extract_sales_period_hint(raw_message)
    ctx: dict[str, str] = {
        "lease_code": lease_code,
        "period_hint": period_hint,
        "source": "notification",
    }
    period_date = extract_period_date(raw_message)
    if period_date:
        ctx["period_date"] = period_date
    key = str(ref.get("key") or ref.get("sub_category") or "").strip()
    if key:
        ctx["key"] = key
    return ctx
