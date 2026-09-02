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
        "ADMIN_REVIEW",
        "REVIEW_SALES_DATA",
    }
)

_PERIOD_AS_OF_RE = re.compile(
    r"as of\s+(?:month\s+)?([A-Za-z]+(?:\s+\d{4})?)",
    re.IGNORECASE,
)
_PERIOD_FOR_MONTH_RE = re.compile(
    r"\b(?:for|of)\s+month\s+([A-Za-z]+(?:\s+\d{4})?)",
    re.IGNORECASE,
)


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
    period_hint = str(ref.get("period_hint") or ref.get("period_label") or "").strip()
    if not period_hint:
        period_hint = extract_sales_period_hint(
            str(ref.get("message") or ref.get("en") or "")
        )
    ctx: dict[str, str] = {
        "lease_code": lease_code,
        "period_hint": period_hint,
        "source": "notification",
    }
    key = str(ref.get("key") or ref.get("sub_category") or "").strip()
    if key:
        ctx["key"] = key
    return ctx
