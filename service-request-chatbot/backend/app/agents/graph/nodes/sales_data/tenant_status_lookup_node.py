"""Tenant Status Lookup Node.

Calls GET /api/v1/sales-data (user-scoped) and returns a summary of
the tenant's current submission status grouped by status type.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.sales_data_schema import STATUS_LABELS
from app.agents.services.sales_data_api_client import SalesDataApiClient
from app.core.request_context import get_current_bearer_token
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)


@trace_node("tenant_status_lookup", "AGENT")
async def tenant_status_lookup_node(state: ServiceRequestState) -> dict[str, Any]:
    """Fetch and summarise the tenant's sales data submission status."""
    token = get_current_bearer_token()
    client = SalesDataApiClient(bearer_token=token)

    try:
        rows = await client.get_sales_data(disable_pagination=True)
    except Exception:
        log.error("tenant_status_lookup.failed", exc_info=True)
        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                "⚠️ I couldn't retrieve your sales data status right now. "
                "Please try again in a moment or visit the Sales Data listing page."
            ),
        }

    if not rows:
        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                "No sales data submissions found for your account. "
                "Would you like to submit data now?"
            ),
        }

    # Group by status
    grouped: dict[str, list] = {}
    for row in rows:
        status = row.get("status", "UNKNOWN")
        grouped.setdefault(status, []).append(row)

    # Build summary
    lines = ["Here is your current sales data submission status:\n"]
    for status, items in sorted(grouped.items()):
        label = STATUS_LABELS.get(status, status)
        lines.append(f"**{label}** — {len(items)} lease(s):")
        for item in items[:3]:  # Show max 3 per group
            lc = item.get("lease_code", "?")
            period = f"{item.get('sales_period_start', '')} – {item.get('sales_period_end', '')}"
            amt = item.get("sales_amt")
            amt_str = f" | SAR {float(amt):,.2f}" if amt else ""
            lines.append(f"  • {lc} ({period}){amt_str}")
        if len(items) > 3:
            lines.append(f"  _(and {len(items) - 3} more)_")
        lines.append("")

    action_required = [r for r in rows if r.get("status") in ("PENDING", "NEED_REUPLOAD")]
    if action_required:
        lines.append(
            f"🔔 **{len(action_required)} submission(s) require your attention.** "
            "Would you like to upload data now?"
        )

    message = "\n".join(lines)
    log.info("tenant_status_lookup.complete", total_rows=len(rows))

    return {
        "status": "WAITING_FOR_USER",
        "workflow_stage": "STATUS_LOOKUP",
        "response_message": message,
        "response_ui": {
            "type": "status_summary",
            "rows": rows,
            "grouped": {k: len(v) for k, v in grouped.items()},
            "message": message,
        },
    }
