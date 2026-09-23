"""Manual Entry Submission Node.

Builds a single-row payload from collected_data and calls
POST /api/v1/sales-data.  Shares the same error handling as
sales_data_api_submission_node.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.services.permission_service import graph_permission_guard
from app.agents.services.sales_data_api_client import SalesDataApiClient
from app.core.request_context import get_current_bearer_token
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)


@trace_node("manual_entry_submission", "AGENT")
async def manual_entry_submission_node(state: ServiceRequestState) -> dict[str, Any]:
    """Submit a single manually-entered row to POST /api/v1/sales-data."""
    collected: dict = state.get("collected_data") or {}
    backend_refs: dict = dict(state.get("backend_refs") or {})

    payload = [{
        "lease_code": str(collected.get("lease_code", "")).strip(),
        "sales_period_start": str(collected.get("sales_period_start", "")).strip(),
        "sales_period_end": str(collected.get("sales_period_end", "")).strip(),
        "sales_amt": float(collected.get("sales_amt", 0)),
    }]

    denied = graph_permission_guard(state, "SUBMIT_SALES_DATA")
    if denied:
        log.warning("manual_entry_submission.permission_denied")
        return denied

    token = get_current_bearer_token()
    client = SalesDataApiClient(bearer_token=token)

    try:
        result = await client.submit_sales_data(payload)
    except httpx.HTTPStatusError as exc:
        # A genuine 5xx response from cenomi-api — not a network/connectivity
        # failure. Reporting this as "network" is misleading: the request
        # reached the server and the server itself errored out.
        log.error(
            "manual_entry_submission.backend_5xx",
            status_code=exc.response.status_code,
            exc_info=True,
        )
        backend_refs["submission_error_type"] = "server_error"
        backend_refs["submission_errors"] = [
            {"message": f"Backend returned {exc.response.status_code}"}
        ]
        return {
            "backend_refs": backend_refs,
            "workflow_stage": "SUBMISSION_ERROR",
            "status": "WAITING_FOR_USER",
        }
    except Exception as exc:
        log.error("manual_entry_submission.network_error", exc_info=True)
        backend_refs["submission_errors"] = [{"message": f"Network error: {exc}"}]
        backend_refs["submission_error_type"] = "network"
        return {
            "backend_refs": backend_refs,
            "workflow_stage": "SUBMISSION_ERROR",
            "status": "WAITING_FOR_USER",
        }

    if result.get("success"):
        log.info("manual_entry_submission.success")
        backend_refs["submitted_rows"] = payload
        return {
            "backend_refs": backend_refs,
            "workflow_stage": "SALES_DATA_COMPLETE",
            "status": "COMPLETED",
        }
    else:
        errors = result.get("errors", [])
        backend_refs["submission_errors"] = errors
        backend_refs["submission_error_type"] = "data"
        return {
            "backend_refs": backend_refs,
            "workflow_stage": "SUBMISSION_ERROR",
            "status": "WAITING_FOR_USER",
        }
