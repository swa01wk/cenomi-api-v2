"""Tenant acknowledgement of a submitted Open / Close Ceiling Inspection report.

Reached from ``sr_status_sync`` when the SR is ``REPORT_SUBMITTED``
(``CEILING_REPORT_SUBMITTED``). The tenant confirms, and a PATCH with
``status = APPROVED`` completes the SR — cenomi-api treats a tenant APPROVED on
a REPORT_SUBMITTED ceiling inspection as the acknowledgement (see
service_request_usecase.go, "Tenant approval of REPORT_SUBMITTED SR").

Anyone else who opens the SR at this point is told it is waiting on the tenant.
The node is deterministic and does its own PATCH; it routes straight to
``response_generation``.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.nodes.fitout_confirm.fitout_confirm_entry_node import (
    _CONFIRM_PHRASES,
    _contains_phrase,
)
from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.ceiling_schema import (
    SERVICE_CATEGORY,
    STAGE_COMPLETED,
    is_tenant,
    sub_category_label,
    user_roles,
)
from app.agents.services.review_decorations_service import build_view_sr_suggestions
from app.agents.services.service_request_api_service import get_service_request_api_service
from app.observability.analytics_constants import TOOL_TYPE_SR_PATCH
from app.observability.decorators import trace_node
from app.observability.tool_capture import record_sr_api_call

logger = logging.getLogger(__name__)

_ACK_ACTIONS: frozenset[str] = frozenset({"confirm", "acknowledge_ceiling_report"})


def _message(text: str, **ui: Any) -> dict[str, Any]:
    return {
        "status": "WAITING_FOR_USER",
        "response_message": text,
        "response_ui": {"type": "message", "message": text, "verbatim": True, **ui},
    }


def build_ceiling_ack_payload(backend_refs: dict[str, Any], sub_category: str) -> dict[str, Any]:
    snapshot: dict[str, Any] = dict(backend_refs.get("create_payload") or {})
    inner: dict[str, Any] = dict(snapshot.get("payload") or {})
    sr_id = str(backend_refs.get("sr_id") or "")
    return {
        "payload": inner,
        "title": inner.get("title") or snapshot.get("title") or "",
        "tenant_profile_id": inner.get("tenant_profile_id", snapshot.get("tenant_profile_id")),
        "property_id": inner.get("property_id", snapshot.get("property_id")),
        "service_category": SERVICE_CATEGORY,
        "sub_category": sub_category,
        "lease_code": snapshot.get("lease_code") or inner.get("lease") or "",
        "lease_id": snapshot.get("lease_id") or inner.get("lease_id"),
        "service_request_id": sr_id,
        "status": "APPROVED",
    }


@trace_node("ceiling_ack_entry", "AGENT")
async def ceiling_ack_entry_node(state: ServiceRequestState) -> dict[str, Any]:
    sub_category = str(state.get("sub_category") or "")
    label = sub_category_label(sub_category)
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    sr_id = str(backend_refs.get("sr_id") or "")
    auth = state.get("auth_context")

    if user_roles(auth) and not is_tenant(auth):
        result = _message(
            f"The {label} report for **{sr_id}** is submitted and waiting for the "
            "tenant's acknowledgement."
        )
        result["response_decorations"] = {"suggestions": build_view_sr_suggestions(sr_id)}
        return result

    action = str(state.get("action_override") or "")
    confirmed = action in _ACK_ACTIONS or (
        state.get("confirmation_status") == "PENDING"
        and not action
        and _contains_phrase(str(state.get("user_message") or ""), _CONFIRM_PHRASES)
    )
    if not confirmed:
        result = _message(f"The {label} report for **{sr_id}** is submitted. Confirm acknowledgement.")
        result["confirmation_status"] = "PENDING"
        result["response_decorations"] = {
            "suggestions": {
                "kind": "labeled_options",
                "options": [
                    {
                        "label": "Acknowledge report",
                        "value": "Acknowledge report",
                        "action": "acknowledge_ceiling_report",
                    }
                ],
            }
        }
        return result

    payload = build_ceiling_ack_payload(backend_refs, sub_category)
    api_result = await get_service_request_api_service().patch_service_request(sr_id, payload)
    await record_sr_api_call(
        state,
        tool_name="service_request_api.ceiling_inspection.acknowledge",
        tool_type=TOOL_TYPE_SR_PATCH,
        request_payload=payload,
        result=api_result,
    )
    if api_result.error is not None:
        logger.warning("ceiling_ack_entry: PATCH failed sr_id=%s error=%s", sr_id, api_result.error)
        failed = _message(
            "I couldn't record the acknowledgement on the platform. "
            f"Please try again in a moment. (Detail: {api_result.error})"
        )
        failed["status"] = "FAILED"
        failed["confirmation_status"] = "PENDING"
        return failed

    done = _message(
        f"{label} **{sr_id}** is complete.",
        workflow_complete=True,
        workflow_stage=STAGE_COMPLETED,
    )
    done["workflow_stage"] = STAGE_COMPLETED
    done["confirmation_status"] = None
    return done
