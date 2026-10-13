"""Admin Status Update Node.

Calls PATCH /api/v1/sales-data once per admin_target
(APPROVED / NEED_REUPLOAD / NEED_UPLOAD_SUPPORTING_DOCUMENT).
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.sales_data_schema import ADMIN_ACTION_LABELS
from app.agents.services.permission_service import graph_permission_guard
from app.agents.services.sales_data_api_client import SalesDataApiClient
from app.core.request_context import get_current_bearer_token
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

_ACTION_LABELS = {
    "APPROVED": "approved",
    "NEED_REUPLOAD": "asked to re-upload sales data",
    "NEED_UPLOAD_SUPPORTING_DOCUMENT": "asked to upload a supporting document",
    "NEED_UPLOAD_AUDITED_SALES": "asked to upload audited sales data",
}

# Plain sentences for the result bubble. The counted form ("1 submission(s)
# asked to re-upload sales data: LC-1234.") read like a database log; these
# say who was told what, in the order a person would say it.
_RESULT_SENTENCES = {
    "APPROVED": ("Approved", "the tenant has been notified"),
    "NEED_REUPLOAD": (
        "Re-upload requested for",
        "the tenant has been asked to send corrected sales data",
    ),
    "NEED_UPLOAD_SUPPORTING_DOCUMENT": (
        "Supporting document requested for",
        "the tenant has been asked to attach one",
    ),
    "NEED_UPLOAD_AUDITED_SALES": (
        "Audited sales data requested for",
        "the tenant has been asked to attach it",
    ),
}


def _legacy_targets(collected: dict[str, Any]) -> list[dict[str, Any]]:
    if collected.get("lease_id"):
        return [{
            "lease_id": collected.get("lease_id"),
            "lease_code": collected.get("lease_code", ""),
            "sales_period_start": collected.get("sales_period_start", ""),
            "sales_period_end": collected.get("sales_period_end", ""),
        }]
    return []


@trace_node("admin_status_update", "AGENT")
async def admin_status_update_node(state: ServiceRequestState) -> dict[str, Any]:
    """Apply admin status updates via sequential PATCH /api/v1/sales-data."""
    collected: dict = state.get("collected_data") or {}

    action = collected.get("admin_action", "")
    comment = collected.get("comment_text", "")
    targets: list[dict[str, Any]] = list(collected.get("admin_targets") or [])
    if not targets:
        targets = _legacy_targets(collected)

    if not action or not targets:
        return {
            "status": "WAITING_FOR_USER",
            "response_message": "I couldn't identify the submission to update. Please select it again from the review list.",
        }

    payloads: list[dict[str, Any]] = []
    for t in targets:
        if not t.get("lease_id"):
            continue
        payload: dict[str, Any] = {
            "lease_id": t["lease_id"],
            "lease_code": t.get("lease_code", ""),
            "sales_period_start": t.get("sales_period_start", ""),
            "sales_period_end": t.get("sales_period_end", ""),
            "status": action,
        }
        if comment:
            payload["comment_text"] = comment
        payloads.append(payload)

    if not payloads:
        return {
            "status": "WAITING_FOR_USER",
            "response_message": "I couldn't identify the submission to update. Please select it again from the review list.",
        }

    denied = graph_permission_guard(state, "APPROVE_SALES_DATA")
    if denied:
        log.warning("admin_status_update.permission_denied")
        return denied

    token = get_current_bearer_token()
    client = SalesDataApiClient(bearer_token=token)
    results = await client.update_sales_data_statuses(payloads)

    succeeded = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]
    label = _ACTION_LABELS.get(action, action.lower())
    codes_ok = [str(r.get("lease_code") or "") for r in succeeded]
    codes_fail = [str(r.get("lease_code") or "") for r in failed]

    verb, follow_up = _RESULT_SENTENCES.get(
        action, (f"{label.capitalize()} for", "the tenant has been notified")
    )

    if succeeded and not failed:
        listed = ", ".join(f"**{c}**" for c in codes_ok)
        message = (
            f"{verb} {listed} — {follow_up}."
            + (f'\n\nYour note to the tenant: "{comment}"' if comment else "")
        )
    elif succeeded and failed:
        fail_bits = [
            f"**{r.get('lease_code')}** ({r.get('error') or 'unknown error'})"
            for r in failed
        ]
        message = (
            f"{verb} " + ", ".join(f"**{c}**" for c in codes_ok)
            + f" — {follow_up}.\n\nI couldn't update "
            + "; ".join(fail_bits)
            + "."
        )
    else:
        message = (
            "I couldn't update "
            + ", ".join(f"**{c}**" for c in codes_fail)
            + f": {failed[0].get('error') or 'Unknown error'}."
        )

    log.info(
        "admin_status_update.done",
        action=action,
        succeeded=len(succeeded),
        failed=len(failed),
    )
    first_code = codes_ok[0] if codes_ok else (codes_fail[0] if codes_fail else "")
    backend_refs = dict(state.get("backend_refs") or {})
    backend_refs.pop("admin_review_rows", None)
    return {
        "workflow_stage": "ADMIN_REVIEW",
        "collected_data": {},
        "confirmation_status": None,
        "backend_refs": backend_refs,
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": {
            "type": "admin_action_success" if succeeded and not failed else "admin_action_result",
            "action": action,
            "action_label": ADMIN_ACTION_LABELS.get(action, action),
            "lease_code": first_code,
            "lease_codes": codes_ok,
            "failed_lease_codes": codes_fail,
            "succeeded": len(succeeded),
            "failed": len(failed),
            "message": message,
            # Precise, exact-count wording matters here (partial vs full
            # success, which leases failed and why) — the LLM rewrite would
            # otherwise vary or soften it on every turn.
            "verbatim": True,
        },
    }
