"""Manual Entry Lease Validation Node — Layer 2 for typed entry.

Same logic as lease_validation_node but for a single row built from
collected_data (lease_code, sales_period_start, sales_period_end).
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.graph.nodes.sales_data.period_utils import (
    format_year_month,
    year_month_from_start,
)
from app.agents.graph.state import ServiceRequestState
from app.agents.services.sales_data_api_client import (
    SalesDataApiClient,
    partition_rows_by_lease_access,
)
from app.core.request_context import get_current_bearer_token
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

_OPEN_STATUSES = ("PENDING", "NEED_REUPLOAD")
_MAX_SUGGESTIONS = 3


def _norm(code: str) -> str:
    return str(code or "").replace("-", "").replace(" ", "").upper()


def _reply(message: str, collected: dict) -> dict[str, Any]:
    """Keep what the user already gave; the next reply only has to fix one thing."""
    return {
        "workflow_stage": "MANUAL_ENTRY",
        "status": "WAITING_FOR_USER",
        "collected_data": collected,
        "response_message": message,
        "response_ui": {"type": "message", "verbatim": True, "message": message},
    }


def _open_months(rows: list[dict[str, Any]], lease_code: str) -> list[str]:
    months = {
        ym
        for r in rows
        if _norm(r.get("lease_code")) == _norm(lease_code)
        and str(r.get("status") or "").upper() in _OPEN_STATUSES
        and (ym := year_month_from_start(str(r.get("sales_period_start") or "")))
    }
    return sorted(months)


def _open_leases(rows: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for r in rows:
        code = str(r.get("lease_code") or "")
        if code and code not in seen and str(r.get("status") or "").upper() in _OPEN_STATUSES:
            seen.append(code)
    return seen[:_MAX_SUGGESTIONS]


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
        return _reply(
            "I couldn't reach the sales data service, so nothing has been "
            "submitted. Your details are saved; please say **try again** in a "
            "moment." + detail,
            collected,
        )

    row = {"lease_code": lease_code, "sales_period_start": start, "sales_period_end": end}
    result = partition_rows_by_lease_access([row], backend_rows)

    no_access = result["no_access"]
    no_pending = result["no_pending_row"]
    valid = result["valid"]

    if no_access:
        collected.pop("lease_code", None)
        suggestions = _open_leases(backend_rows)
        message = f"Lease **{lease_code}** isn't linked to your account."
        if suggestions:
            message += (
                " Leases with pending sales data include "
                + ", ".join(f"**{c}**" for c in suggestions)
                + ". Which lease is this for?"
            )
        else:
            message += " Please check the lease code and share it again."
        return _reply(message, collected)

    if no_pending:
        asked = year_month_from_start(start)
        asked_label = format_year_month(asked) if asked else f"{start} – {end}"
        open_months = _open_months(backend_rows, lease_code)
        collected.pop("sales_period_start", None)
        collected.pop("sales_period_end", None)
        if open_months:
            labels = ", ".join(f"**{format_year_month(m)}**" for m in open_months[-4:])
            message = (
                f"Lease **{lease_code}** has no pending submission for **{asked_label}**; "
                "it may already have been submitted. "
                f"Pending months for this lease: {labels}. Which month is this for?"
            )
        else:
            message = (
                f"Lease **{lease_code}** has no pending sales data at the moment, so "
                f"there is nothing to submit for **{asked_label}**. If you meant a "
                "different lease, please share the lease code."
            )
            collected.pop("lease_code", None)
        return _reply(message, collected)

    # Valid — enrich with lease_id from backend row
    enriched = valid[0]
    matched_backend_row = next(
        (
            r for r in backend_rows
            if r.get("lease_code") == enriched.get("lease_code")
            and str(r.get("status") or "").upper() in ("PENDING", "NEED_REUPLOAD")
        ),
        None,
    )
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
        typed_start=start,
        typed_end=end,
        enriched_start=collected["sales_period_start"],
        enriched_end=collected["sales_period_end"],
        raw_backend_start=(matched_backend_row or {}).get("sales_period_start"),
        raw_backend_end=(matched_backend_row or {}).get("sales_period_end"),
    )
    return {
        "collected_data": collected,
        "backend_refs": backend_refs,
        "workflow_stage": "AWAITING_MANUAL_CONFIRM",
        "status": "IN_PROGRESS",
    }
