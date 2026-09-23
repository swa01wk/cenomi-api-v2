"""Shared serialization for notification inbox payloads.

One source of truth so the conversational path (``list_notifications_node``)
and the read route (``GET /api/v1/notifications/inbox``) emit byte-identical
rows and groups.  Contract fields per the reconciled spec §4.2.
"""

from __future__ import annotations

import re
from typing import Any

from app.agents.schemas.work_permit_schema import VALID_WORK_PERMIT_TYPES
from app.agents.services.notification_inbox_service import (
    SUB_CATEGORY_LABELS,
    NotificationInboxResult,
)
from app.agents.services.notification_sales_data import SALES_DATA_SUB_CATEGORY

# Lease-activation template: "Contract t0112426 is now active. ..." — fixed
# wording from the platform's GetNotificationContentWithIDOnly.
_LEASE_CODE_RE = re.compile(
    r"Contract\s+([A-Za-z0-9\-]+)\s+is now active", re.IGNORECASE
)

_LEASE_ACTIVATION_TYPE = "ACTIVATED_LEASE_NOTIFICATION"


def row_payload(item: Any) -> dict[str, Any]:
    """Serialize one inbox row, distinguishing SR rows from lease-activation.

    On ACTIVATED_LEASE_NOTIFICATION the platform's ``text_content.id`` is a
    LEASE id — surfacing it as ``sr_id`` would open an unrelated SR.  Those
    rows get ``kind: "lease_activation"`` + the lease reference instead, and
    the FE renders "Create Service Request" (the handover has not been raised
    yet — that is what the notification asks the Mall Manager to do).
    """
    is_activation = item.notification_type == _LEASE_ACTIVATION_TYPE
    is_sales = (
        item.sub_category == SALES_DATA_SUB_CATEGORY
        or str(item.notification_type or "").upper() == "SALES_DATA"
        or str(item.template_key or "").upper().startswith("SALES_DATA_")
    )
    if is_sales:
        kind = "sales_data"
    elif is_activation:
        kind = "lease_activation"
    else:
        kind = "service_request"
    row: dict[str, Any] = {
        "notification_id": item.notification_id,
        "kind": kind,
        "sr_id": None if (is_activation or is_sales) else item.sr_id,
        # Row-level per the reconciled spec — the Open handler reads the row,
        # never the parent group.
        "sub_category": item.sub_category,
        "message": item.message,
        "message_ar": item.message_ar,
        "created_at": item.created_at,
        "read": bool(item.read_at),
        "action_required": item.action_required,
        "lease_id": item.lease_id,
        "unique_property_id": item.unique_property_id,
    }
    if item.template_key:
        row["key"] = item.template_key
    if is_sales:
        # text_content.id is the lease code (PMSTenantCode), not an SR id.
        row["lease_code"] = item.sr_id
        row["notification_type"] = item.notification_type or "SALES_DATA"
    if is_activation:
        match = _LEASE_CODE_RE.search(item.message or "")
        row["lease_code"] = match.group(1) if match else None
        # text_content.id was the lease id — keep it when the platform row
        # lacks lease_id.
        if not row["lease_id"] and item.sr_id:
            row["lease_id"] = item.sr_id
    return row


def _service_category_for_sub(sub_category: str) -> str:
    if sub_category == SALES_DATA_SUB_CATEGORY:
        return "SALES_DATA"
    if sub_category in VALID_WORK_PERMIT_TYPES:
        return "WORK_PERMIT"
    return "FIT_OUT_AND_HANDOVER"


def serialize_groups(result: NotificationInboxResult) -> list[dict[str, Any]]:
    return [
        {
            # Reconciled spec: service_category + action_required_count are
            # group-level contract fields; the count is authoritative here —
            # the FE must not recompute it from the rows.
            "service_category": _service_category_for_sub(group.sub_category),
            "sub_category": group.sub_category,
            "label": SUB_CATEGORY_LABELS.get(group.sub_category, group.sub_category),
            "total_count": group.total_count,
            # The whole outstanding count, NOT this page's rows: the platform
            # tools filter to action_required = TRUE server-side, so every row
            # in the group qualifies and total_count is the honest badge value.
            # Counting group.items instead made the badge say 5 while
            # "load more" kept producing rows.
            "action_required_count": group.total_count,
            "notifications": [row_payload(item) for item in group.items],
            "page": group.page,
            "limit": group.limit,
            "has_more": group.has_more,
        }
        for group in result.groups
    ]


def group_summary(result: NotificationInboxResult) -> str:
    """"3 Handover, 1 Trading Confirmation" — used in the assistant's reply."""
    parts = [
        f"{group.total_count} {SUB_CATEGORY_LABELS.get(group.sub_category, group.sub_category)}"
        for group in result.groups
    ]
    return ", ".join(parts)
