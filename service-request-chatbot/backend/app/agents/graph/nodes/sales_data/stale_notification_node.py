"""Stale Notification Node.

Reached when lease_validation finds ZERO PENDING/NEED_REUPLOAD rows for any
of the lease codes in the uploaded CSV.

This happens when:
  (a) The tenant submitted the data via another channel (portal, direct API)
      after the notification was sent.
  (b) The notification context is outdated (period already processed).
  (c) The tenant does not have access to the mentioned leases.

Note: This node is a Sales Data Agent internal node.  The Notification Agent
(separate repo) is responsible for opening the chat and handing off context.
This node handles the case *after* that handoff where no actionable data exists.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)


@trace_node("stale_notification", "AGENT")
async def stale_notification_node(state: ServiceRequestState) -> dict[str, Any]:
    """Inform the user that no pending submissions were found."""
    backend_refs: dict = state.get("backend_refs") or {}
    layer2_errors: list = backend_refs.get("layer2_errors") or []

    # Check if the issue is access vs. no pending row
    no_access_count = sum(1 for e in layer2_errors if "don't have access" in (e.get("_error") or ""))
    no_pending_count = len(layer2_errors) - no_access_count

    if no_access_count > 0 and no_pending_count == 0:
        message = (
            "**No access to these leases** — none of the lease codes in your file "
            "are linked to your current company. If they belong to another of your "
            "companies, switch to that company and upload again; otherwise, please "
            "check the lease codes.\n\n"
            "If you believe this is an error, please contact your property manager."
        )
    elif no_pending_count > 0:
        message = (
            "**No pending submissions found** — there is no pending sales data "
            "for the leases and periods in your file.\n\n"
            "This may mean:\n"
            "• The data was already submitted via another channel\n"
            "• The notification you received is for a different period\n"
            "• Your submission deadline has passed\n\n"
            "Please check the **Sales Data** page for the current status, "
            "or contact your property manager if you believe data is still due."
        )
    else:
        message = (
            "**No pending submissions found** — there is no pending sales data "
            "for your leases. Please check the **Sales Data** page for the current status."
        )

    log.info(
        "stale_notification.reported",
        no_access=no_access_count,
        no_pending=no_pending_count,
    )

    return {
        "status": "WAITING_FOR_USER",
        "workflow_stage": "UPLOAD_INTENT",
        "response_message": message,
        "response_ui": {
            "type": "stale_notification",
            "message": message,
            "show_listing_link": True,
            # Without this, response_generation's LLM rewrite pulls in stale
            # state (e.g. the original notification's period/lease) and
            # produces a specific-sounding sentence that doesn't match what
            # the tenant actually just uploaded — this message is already
            # accurate and generic on purpose; show it as-is.
            "verbatim": True,
        },
    }
