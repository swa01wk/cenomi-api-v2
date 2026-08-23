"""Admin Status Update Node.

Calls PATCH /api/v1/sales-data to apply an admin action
(APPROVED / NEED_REUPLOAD / NEED_UPLOAD_SUPPORTING_DOCUMENT).
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.services.sales_data_api_client import SalesDataApiClient
from app.core.request_context import get_current_bearer_token
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)


@trace_node("admin_status_update", "AGENT")
async def admin_status_update_node(state: ServiceRequestState) -> dict[str, Any]:
    """Apply admin status update via PATCH /api/v1/sales-data."""
    collected: dict = state.get("collected_data") or {}

    lease_id = collected.get("lease_id")
    lease_code = collected.get("lease_code", "")
    action = collected.get("admin_action", "")
    comment = collected.get("comment_text", "")
    start = collected.get("sales_period_start", "")
    end = collected.get("sales_period_end", "")

    if not lease_id or not action:
        return {
            "status": "WAITING_FOR_USER",
            "response_message": "Missing lease ID or action. Please try again.",
        }

    payload: dict = {
        "lease_id": lease_id,
        "lease_code": lease_code,
        "sales_period_start": start,
        "sales_period_end": end,
        "status": action,
    }
    if comment:
        payload["comment_text"] = comment

    token = get_current_bearer_token()
    client = SalesDataApiClient(bearer_token=token)

    try:
        result = await client.update_sales_data_status(payload)
    except Exception as exc:
        log.error("admin_status_update.failed", exc_info=True)
        return {
            "status": "WAITING_FOR_USER",
            "response_message": f"⚠️ Update failed: {exc}. Please try again.",
        }

    if result.get("success"):
        action_labels = {
            "APPROVED": "approved",
            "NEED_REUPLOAD": "marked for re-upload",
            "NEED_UPLOAD_SUPPORTING_DOCUMENT": "flagged for supporting document",
            "NEED_UPLOAD_AUDITED_SALES": "flagged for audited sales",
        }
        label = action_labels.get(action, action.lower())
        message = (
            f"✅ Lease **{lease_code}** has been **{label}** successfully."
            + (f'\nComment sent to tenant: "{comment}"' if comment else "")
        )
        log.info("admin_status_update.success", lease_code=lease_code, action=action)
        return {
            "workflow_stage": "ADMIN_REVIEW",
            "collected_data": {},
            "confirmation_status": None,
            "status": "WAITING_FOR_USER",
            "response_message": message,
            "response_ui": {
                "type": "admin_action_success",
                "action": action,
                "lease_code": lease_code,
                "message": message,
            },
        }
    else:
        return {
            "status": "WAITING_FOR_USER",
            "response_message": f"⚠️ Update failed: {result.get('error', 'Unknown error')}.",
        }
