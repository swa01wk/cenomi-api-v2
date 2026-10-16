"""Work Permit status view — "show this SR" when no review step waits on the caller.

Reached from ``_route_after_sync`` (sent back / terminal / unknown stage) and
from the supervisor's preview / status-check intents. Replaces the Handover
``preview`` node for Work Permit: same compact card as the review hop
(message + ``summary`` + collapsible ``details``) but with no workflow actions.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.nodes.work_permit.work_permit_confirmation_node import _is_on_sr_page
from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.work_permit_schema import STAGE_LABELS, resolve_wp_sub_category
from app.agents.services.display_formatters import format_enum_label
from app.agents.services.work_permit_company_service import with_wp_company
from app.agents.services.work_permit_review_summary import (
    build_wp_full_request_chips,
    build_wp_review_details,
    build_wp_review_summary,
    compose_wp_status_message,
)
from app.observability.decorators import trace_node
from app.types.chat import AuthContext

logger = logging.getLogger(__name__)


def _is_tenant(auth: AuthContext | None) -> bool:
    if auth is None:
        return False
    return any(str(r).upper().startswith("TENANT") for r in auth.roles)


def _status_label(workflow_stage: str, platform_status: str | None, sr_id: Any) -> str:
    if not sr_id:
        return "Draft"
    label = STAGE_LABELS.get(workflow_stage)
    if label and workflow_stage != "CREATE_WORK_PERMIT":
        return label
    return str(format_enum_label(platform_status) or "Status")


def build_wp_status_card(state: ServiceRequestState) -> dict[str, Any]:
    collected: dict[str, Any] = dict(state.get("collected_data") or {})
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    sr_id = backend_refs.get("sr_id")
    workflow_stage = str(state.get("workflow_stage") or "")
    platform_status = backend_refs.get("platform_sr_status")
    subtype = resolve_wp_sub_category(state.get("sub_category"), collected)
    auth: AuthContext | None = state.get("auth_context")  # type: ignore[assignment]

    card: dict[str, Any] = {
        "type": "confirmation_card",
        "requestType": (
            f"Work Permit — {_status_label(workflow_stage, platform_status, sr_id)}"
        ),
        "message": compose_wp_status_message(
            collected,
            sr_id=str(sr_id) if sr_id else None,
            subtype=subtype,
            workflow_stage=workflow_stage,
            platform_status=platform_status,
            is_tenant=_is_tenant(auth),
        ),
        "summary": build_wp_review_summary(collected, subtype=subtype),
        "details": build_wp_review_details(collected, subtype=subtype),
        "actions": [],
    }
    if sr_id:
        card["suggestions"] = {
            "kind": "labeled_options",
            "options": build_wp_full_request_chips(
                sr_id, on_sr_page=_is_on_sr_page(state, sr_id)
            ),
        }
    return card


@trace_node("work_permit_status", "AGENT")
async def work_permit_status_node(state: ServiceRequestState) -> dict[str, Any]:
    """Read-only Work Permit card: status message, summary, details, no actions."""
    collected: dict[str, Any] = state.get("collected_data") or {}
    enriched = await with_wp_company(collected)
    card = build_wp_status_card({**state, "collected_data": enriched})
    logger.info(
        "work_permit_status_node: stage=%s platform_status=%s",
        state.get("workflow_stage"),
        (state.get("backend_refs") or {}).get("platform_sr_status"),
    )
    result: dict[str, Any] = {
        "status": "WAITING_FOR_USER",
        "response_ui": card,
        # None keeps the card's own bolded wording (no LLM rewrite).
        "response_message": None,
        # This reply owns its chips.
        "response_decorations": (
            {"suggestions": card["suggestions"]} if "suggestions" in card else {}
        ),
    }
    if enriched is not collected:
        result["collected_data"] = enriched
    # A draft preview must not void a pending create confirmation.
    if (state.get("backend_refs") or {}).get("sr_id"):
        result["confirmation_required"] = False
        result["confirmation_status"] = None
    return result
