"""Manual Entry Lease Validation Node — Layer 2 for typed entry.

Same logic as lease_validation_node but for a single row built from
collected_data (lease_code, sales_period_start, sales_period_end).
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.services.sales_data_api_client import (
    SalesDataApiClient,
    partition_rows_by_lease_access,
)
from app.core.request_context import get_current_bearer_token
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)


@trace_node("manual_entry_lease_validation", "AGENT")
async def manual_entry_lease_validation_node(state: ServiceRequestState) -> dict[str, Any]:
    """Layer 2 validation for a single manually-entered row."""
    collected: dict = dict(state.get("collected_data") or {})
    backend_refs: dict = dict(state.get("backend_refs") or {})

    lease_code = collected.get("lease_code", "")
    start = collected.get("sales_period_start", "")
    end = collected.get("sales_period_end", "")
    sales_amt = collected.get("sales_amt")

    if not all([lease_code, start, end, sales_amt]):
        return {
            "status": "IN_PROGRESS",
            "workflow_stage": "MANUAL_ENTRY",
        }

    token = get_current_bearer_token()
    client = SalesDataApiClient(bearer_token=token)

    try:
        backend_rows = await client.get_sales_data(disable_pagination=True)
    except Exception as exc:
        import os
        api_url = os.getenv("SALES_DATA_API_BASE_URL", "http://localhost:8001")
        is_dev = os.getenv("ENVIRONMENT", "development") == "development"
        # Non-prod: surface the real exception so a connectivity/timeout/4xx/5xx
        # failure doesn't all collapse into the same unhelpful generic message.
        detail = f" _(Dev detail: {type(exc).__name__}: {exc})_" if is_dev else ""
        log.error("manual_entry_lease_validation.get_failed", api_url=api_url, error=str(exc))
        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                "⚠️ I couldn't reach the sales data service right now. "
                "Please try again in a moment." + detail
            ),
        }

    row = {"lease_code": lease_code, "sales_period_start": start, "sales_period_end": end}
    result = partition_rows_by_lease_access([row], backend_rows)

    no_access = result["no_access"]
    no_pending = result["no_pending_row"]
    valid = result["valid"]

    if no_access:
        return {
            "workflow_stage": "MANUAL_ENTRY",
            "status": "WAITING_FOR_USER",
            "response_message": (
                f"❌ You don't have access to lease **{lease_code}**. "
                "Please check the lease code or contact your property manager."
            ),
        }

    if no_pending:
        return {
            "workflow_stage": "MANUAL_ENTRY",
            "status": "WAITING_FOR_USER",
            "response_message": (
                f"⚠️ No pending submission found for lease **{lease_code}** "
                f"in the period {start} – {end}.\n\n"
                "This data may have already been submitted, or the period may not match "
                "your template. Please check the Sales Data listing page."
            ),
        }

    # Valid — enrich with lease_id from backend row
    enriched = valid[0]
    collected["lease_id"] = enriched.get("lease_id")
    collected["sales_period_start"] = enriched["sales_period_start"]
    collected["sales_period_end"] = enriched["sales_period_end"]
    collected["current_status"] = enriched.get("current_status", "PENDING")
    collected["brand"] = (enriched.get("brand") or "").strip()
    collected["property"] = (enriched.get("property") or "").strip()

    log.info(
        "manual_entry_lease_validation.valid",
        lease_code=lease_code,
        brand=collected["brand"],
        property=collected["property"],
    )
    return {
        "collected_data": collected,
        "backend_refs": backend_refs,
        "workflow_stage": "AWAITING_MANUAL_CONFIRM",
        "status": "IN_PROGRESS",
    }
