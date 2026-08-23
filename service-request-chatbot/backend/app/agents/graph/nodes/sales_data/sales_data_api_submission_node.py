"""Sales Data API Submission Node.

Calls POST /api/v1/sales-data with the payload built by
sales_data_payload_builder_node.

On HTTP 200 → routes to submission_success_node.
On HTTP 400 → routes to submission_error_node with per-row errors.
On network error → routes to submission_error_node with a generic message.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.services.sales_data_api_client import SalesDataApiClient
from app.core.request_context import get_current_bearer_token
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)


@trace_node("sales_data_api_submission", "AGENT")
async def sales_data_api_submission_node(state: ServiceRequestState) -> dict[str, Any]:
    """Submit sales data rows to POST /api/v1/sales-data."""
    backend_refs: dict = dict(state.get("backend_refs") or {})
    payload: list[dict] = backend_refs.get("submit_payload") or []

    if not payload:
        log.error("sales_data_api_submission.no_payload")
        return {
            "status": "WAITING_FOR_USER",
            "workflow_stage": "UPLOAD_INTENT",
            "response_message": (
                "No data to submit. Please re-upload your CSV file."
            ),
        }

    token = get_current_bearer_token()
    client = SalesDataApiClient(bearer_token=token)

    try:
        result = await client.submit_sales_data(payload)
    except Exception as exc:
        log.error("sales_data_api_submission.network_error", exc_info=True)
        backend_refs["submission_error_type"] = "network"
        backend_refs["submission_errors"] = [
            {"message": f"Network error: {exc}"}
        ]
        return {
            "backend_refs": backend_refs,
            "workflow_stage": "SUBMISSION_ERROR",
            "status": "WAITING_FOR_USER",
        }

    if result.get("success"):
        log.info("sales_data_api_submission.success", row_count=len(payload))
        backend_refs["submitted_rows"] = payload
        return {
            "backend_refs": backend_refs,
            "workflow_stage": "SALES_DATA_COMPLETE",
            "status": "COMPLETED",
        }
    else:
        errors = result.get("errors", [])
        log.warning("sales_data_api_submission.backend_400", error_count=len(errors))
        backend_refs["submission_errors"] = errors
        backend_refs["submission_error_type"] = "data"
        return {
            "backend_refs": backend_refs,
            "workflow_stage": "SUBMISSION_ERROR",
            "status": "WAITING_FOR_USER",
        }
