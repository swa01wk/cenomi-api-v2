"""Submission Success Node — shown after a successful POST /api/v1/sales-data."""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)


@trace_node("submission_success", "AGENT")
async def submission_success_node(state: ServiceRequestState) -> dict[str, Any]:
    """Surface a success confirmation card."""
    backend_refs: dict = state.get("backend_refs") or {}
    submitted_rows: list[dict] = backend_refs.get("submitted_rows") or []
    row_count = len(submitted_rows)

    message = (
        f"🎉 **Success!** Your sales data ({row_count} row{'s' if row_count != 1 else ''}) "
        "has been submitted successfully.\n\n"
        "The **Sales Data Team** has been notified and will review your submission.\n\n"
        "You can track the status on the Sales Data listing page."
    )

    log.info("submission_success.reported", row_count=row_count)

    return {
        "workflow_stage": "SALES_DATA_COMPLETE",
        "status": "COMPLETED",
        "response_message": message,
        "response_ui": {
            "type": "submission_success",
            "row_count": row_count,
            "message": message,
        },
    }
