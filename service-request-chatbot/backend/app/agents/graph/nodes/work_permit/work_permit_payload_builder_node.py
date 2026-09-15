"""Build API payloads for Work Permit create (POST) and review (PATCH)."""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.work_permit_schema import (
    WP_REVIEW_STAGES,
    iso_to_ddmmyyyy,
    resolve_wp_sub_category,
)
from app.observability.decorators import trace_node

logger = logging.getLogger(__name__)

_REQUIRED_CREATE_KEYS: frozenset[str] = frozenset(
    {"lease_code", "lease_id", "work_permit_type", "start_date", "end_date"}
)


def _inner_payload_from_collected(data: dict[str, Any]) -> dict[str, Any]:
    """Best-effort nested payload from chatbot collected_data."""
    inner: dict[str, Any] = {
        "mall": data.get("mall", ""),
        "brand": data.get("brand", ""),
        "lease": data.get("lease_code") or data.get("lease") or "",
        "lease_code": data.get("lease_code") or "",
        "lease_brand_mall": data.get("lease_brand_mall") or "",
        "contractor_name": data.get("contractor_name") or "",
        "comments": data.get("comments") or "",
        "description": data.get("description") or "",
        "phone": data.get("phone") or "",
        "work_type": data.get("work_type") or "",
        "work_location": data.get("work_location") or "",
        "unit_codes": data.get("unit_codes") or [],
        "planneddate": {
            "worktimedate": iso_to_ddmmyyyy(data.get("start_date")),
            "completionworkdate": iso_to_ddmmyyyy(data.get("end_date")),
            "worktimefromLT": data.get("worktimefromLT") or "",
            "worktimetoLT": data.get("worktimetoLT") or "",
        },
    }
    workers = data.get("workers")
    if workers:
        inner["workers"] = workers
    return inner


def build_create_work_permit_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Build the nested CREATE work permit API payload.

    Raises ``ValueError`` when required keys are absent.
    """
    missing = [k for k in _REQUIRED_CREATE_KEYS if not data.get(k)]
    if missing:
        raise ValueError(f"Work permit payload missing required keys: {missing}")

    sub_category = resolve_wp_sub_category(None, data)
    title = data.get("title") or f"Work Permit - {sub_category}"
    return {
        "payload": _inner_payload_from_collected(data),
        "title": title,
        "tenant_profile_id": data.get("tenant_profile_id"),
        "property_id": data.get("property_id"),
        "service_category": "WORK_PERMIT",
        "sub_category": sub_category,
        "lease_code": data.get("lease_code"),
        "lease_id": data.get("lease_id"),
        "service_request_id": "",
    }


def build_review_work_permit_payload(
    *,
    sub_category: str,
    inner_payload: dict[str, Any],
    approve: bool,
    comment: str = "",
) -> dict[str, Any]:
    """Build PATCH body for a WP review hop (approve requires payload)."""
    if not approve:
        body: dict[str, Any] = {
            "status": "REJECTED",
            "sub_category": sub_category,
        }
        if comment:
            body["comment"] = comment
        return body
    return {
        "status": "APPROVED",
        "sub_category": sub_category,
        "comment": comment,
        "payload": inner_payload,
    }


@trace_node("work_permit_payload_builder", "TOOL")
async def work_permit_payload_builder_node(state: ServiceRequestState) -> dict[str, Any]:
    """Build the work permit POST or PATCH payload and store it in backend_refs."""
    collected_data: dict[str, Any] = state.get("collected_data") or {}
    workflow_stage: str = state.get("workflow_stage") or ""
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})

    if workflow_stage in WP_REVIEW_STAGES:
        existing = backend_refs.get("create_payload") or {}
        inner = existing.get("payload") if isinstance(existing.get("payload"), dict) else {}
        if not inner:
            inner = _inner_payload_from_collected(collected_data)
        sub_category = resolve_wp_sub_category(
            state.get("sub_category") or existing.get("sub_category"),
            collected_data,
        )
        approve = backend_refs.get("wp_action") != "reject"
        comment = str(collected_data.get("comments") or "")
        payload = build_review_work_permit_payload(
            sub_category=sub_category,
            inner_payload=inner,
            approve=approve,
            comment=comment,
        )
        backend_refs["create_payload"] = payload
        logger.info(
            "work_permit_payload_builder_node: review payload stage=%s approve=%s sub=%s",
            workflow_stage,
            approve,
            sub_category,
        )
        return {"backend_refs": backend_refs}

    if workflow_stage != "CREATE_WORK_PERMIT":
        logger.info(
            "work_permit_payload_builder_node: skipped for stage=%s", workflow_stage
        )
        return {}

    try:
        payload = build_create_work_permit_payload(collected_data)
    except ValueError as exc:
        logger.warning("work_permit_payload_builder_node: %s", exc)
        return {"status": "FAILED"}

    backend_refs["create_payload"] = payload
    logger.info(
        "work_permit_payload_builder_node: create payload lease_code=%s permit_type=%s",
        collected_data.get("lease_code"),
        payload.get("sub_category"),
    )
    return {"backend_refs": backend_refs, "sub_category": payload.get("sub_category")}
