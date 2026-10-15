"""Work Permit details in the chat — "Show the full request" / "Show the schedule".

Read-only: renders ``build_wp_review_details`` rows (optionally one section)
as a ``details_card`` and never touches confirmation, workflow or pending
review state, so a reviewer can look and then still approve or reject.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.nodes.work_permit.work_permit_confirmation_node import (
    _caller_can_act_on_review,
    _is_on_sr_page,
    build_wp_review_card_suggestions,
)
from app.agents.graph.nodes.work_permit.work_permit_review_context import (
    build_wp_review_context,
)
from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.work_permit_schema import WP_REVIEW_STAGES
from app.agents.services.action_resolver import resolve_actions
from app.agents.services.work_permit_company_service import with_wp_company
from app.agents.services.work_permit_review_summary import (
    FULL_REQUEST_CHIP_VALUE,
    WP_DETAILS_SECTIONS,
    build_wp_detail_files,
    build_wp_full_request_chips,
    build_wp_review_details,
    build_wp_review_summary,
    group_wp_detail_sections,
    wp_details_section,
)
from app.observability.decorators import trace_node

logger = logging.getLogger(__name__)

_SECTION_TITLES: dict[str, str] = {
    "schedule": "Planned work schedule",
    "safety": "Measure of safety",
    "work": "Work details",
}

_SECTION_NAMES: dict[str, str] = {
    "schedule": "planned work schedule",
    "safety": "measure of safety",
    "work": "work details",
}


def _suggestions(state: ServiceRequestState, section: str, subtype: str | None) -> dict[str, Any]:
    sr_id = (state.get("backend_refs") or {}).get("sr_id")
    workflow_stage = str(state.get("workflow_stage") or "")
    if workflow_stage in WP_REVIEW_STAGES and _caller_can_act_on_review(state, workflow_stage):
        ctx = build_wp_review_context(state, workflow_stage=workflow_stage)
        if resolve_actions(ctx):
            options = build_wp_review_card_suggestions(state, subtype=subtype)["options"]
        else:
            options = []
    else:
        options = build_wp_full_request_chips(sr_id, on_sr_page=_is_on_sr_page(state, sr_id))
    if section == "all":
        options = [o for o in options if o.get("value") != FULL_REQUEST_CHIP_VALUE]
    return {"kind": "labeled_options", "options": options} if options else {}


def build_wp_details_reply(state: ServiceRequestState, section: str) -> dict[str, Any]:
    collected: dict[str, Any] = dict(state.get("collected_data") or {})
    sr_id = (state.get("backend_refs") or {}).get("sr_id")
    workflow_stage = str(state.get("workflow_stage") or "CREATE_WORK_PERMIT")
    subtype = build_wp_review_context(state, workflow_stage=workflow_stage).subtype

    rows = build_wp_review_details(collected, subtype=subtype)
    permit_type = next((r["value"] for r in rows if r.get("key") == "work_permit_type"), "")
    wanted = WP_DETAILS_SECTIONS.get(section)
    if wanted is not None:
        rows = [r for r in rows if r.get("section") in wanted]
    fields = [{"label": r["label"], "value": r["value"]} for r in rows]
    files = (
        build_wp_detail_files(collected, state.get("documents"))
        if section in {"all", "work"}
        else []
    )

    subject = f"Work Permit SR **{sr_id}**" if sr_id else "this Work Permit draft"
    if section == "all":
        base = f"Work Permit SR {sr_id}" if sr_id else "Work Permit draft"
        title = f"{base} — {permit_type}" if permit_type else base
        message = (
            f"Here are the full details of {subject}."
            if fields or files
            else f"No details have been provided for {subject} yet."
        )
    else:
        title = _SECTION_TITLES[section]
        name = _SECTION_NAMES[section]
        message = (
            f"Here is the {name} for {subject}."
            if fields or files
            else f"The {name} hasn't been provided for {subject}."
        )

    ui: dict[str, Any] = {"type": "details_card", "title": title, "fields": fields, "verbatim": True}
    if section == "all":
        summary = build_wp_review_summary(collected, subtype=subtype)
        sections = group_wp_detail_sections(rows)
        if summary:
            ui["summary"] = summary
        if sections:
            ui["sections"] = sections
    if files:
        ui["files"] = files
    suggestions = _suggestions(state, section, subtype)
    if suggestions:
        ui["suggestions"] = suggestions
    return {
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": ui,
        # This reply owns its chips (sync's View SR chip must not merge back in).
        "response_decorations": {"suggestions": suggestions} if suggestions else {},
    }


@trace_node("work_permit_details", "AGENT")
async def work_permit_details_node(state: ServiceRequestState) -> dict[str, Any]:
    section = wp_details_section(state.get("user_message")) or "all"
    logger.info(
        "work_permit_details_node: section=%s stage=%s",
        section,
        state.get("workflow_stage"),
    )
    # Only the full view shows the location rows (and so Company).
    if section != "all":
        return build_wp_details_reply(state, section)
    collected: dict[str, Any] = state.get("collected_data") or {}
    enriched = await with_wp_company(collected)
    result = build_wp_details_reply({**state, "collected_data": enriched}, section)
    if enriched is not collected:
        result["collected_data"] = enriched
    return result
