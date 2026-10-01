"""Build the create payload for an Open / Close Ceiling Inspection.

The inner ``payload`` mirrors the platform form's ``intialState`` for both
sub-categories (cenomi-api migration 1733723567_add_document_types_rejected_reports):

    common: mall, brand, lease, notes, title, endDate, comments, documents,
            startDate, unit_codes, attachments, description, documents_ids,
            inspectionDoneBy, lease_brand_mall, document_status_map
    admin:  company_name, tenant_contact

plus the lease-derived keys the LeaseBrandMall field fills (city,
contracted_area, brand_id, tenant_profile_id). Handover-only keys
(already_handovered, unit_readiness_date, expected_handover_date,
guideLineLink) are never sent.

Non-responsibilities
--------------------
* Does not call the LLM.
* Does not submit the payload (``ceiling_api_submission_node`` does).
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.ceiling_schema import (
    DRAFT_REPORT_STATUS_KEY,
    OTHER_DOC_TYPE,
    SERVICE_CATEGORY,
    draft_report_doc_type,
    inspection_done_by_role_id,
    is_resolved_sub_category,
)
from app.agents.services.payload_builder_service import (
    format_inspection_datetime_pair,
    resolve_start_end_hhmm,
)
from app.observability.decorators import trace_node

logger = logging.getLogger(__name__)

# Keys a create cannot go without — all come from the lease lookup or the user.
_REQUIRED_KEYS: tuple[str, ...] = (
    "lease_code",
    "lease_id",
    "title",
    "description",
    "startDate",
    "endDate",
    "comments",
)


def build_create_ceiling_payload(
    collected: dict[str, Any],
    sub_category: str,
    documents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the ``POST /service-requests`` body for a ceiling inspection.

    Raises ``ValueError`` when the sub-category is unresolved or a required
    key is empty — the confirmation card is never shown in that state, so this
    only fires on a programming error.
    """
    if not is_resolved_sub_category(sub_category):
        raise ValueError(f"Unresolved ceiling sub_category: {sub_category!r}")
    missing = [k for k in _REQUIRED_KEYS if collected.get(k) in (None, "", [])]
    if missing:
        raise ValueError(f"Missing required ceiling fields: {missing}")

    # dateTimePicker fields: UTC ISO plus the portal's local display string,
    # built exactly as the handover create does.
    start_time, end_time = resolve_start_end_hhmm(collected)
    start_iso, start_lt = format_inspection_datetime_pair(
        collected["startDate"], collected.get("startDateLT"), start_time
    )
    end_iso, end_lt = format_inspection_datetime_pair(
        collected["endDate"], collected.get("endDateLT"), end_time
    )

    # RDD PM create: the draft report goes with its status, in the same
    # document_status_map shape the level-1 review sends, so the platform can
    # auto-approve RDD PM's level. Other creators send no statuses.
    draft_type = draft_report_doc_type(sub_category)
    draft_status = str(collected.get(DRAFT_REPORT_STATUS_KEY) or "")

    doc_ids: list[str] = []
    doc_rows: list[dict[str, Any]] = []
    status_map: list[dict[str, str]] = []
    for doc in documents or []:
        doc_id = str(doc.get("document_id") or "").strip()
        if not doc_id or doc_id in doc_ids:
            continue
        doc_type = doc.get("document_type_id") or OTHER_DOC_TYPE
        doc_ids.append(doc_id)
        doc_rows.append(
            {
                # The form's per-document Comment, sent with the upload.
                "comment": str(doc.get("comment") or ""),
                "document_id": doc_id,
                "document_type_id": doc_type,
            }
        )
        if draft_status:
            status_map.append(
                {"id": doc_id, "document_status": draft_status if doc_type == draft_type else ""}
            )

    lease_code = str(collected["lease_code"])
    tenant_profile_id = collected.get("tenant_profile_id")
    inner: dict[str, Any] = {
        # ── form intialState.common ──
        "mall": collected.get("mall") or "",
        "brand": collected.get("brand") or "",
        "lease": lease_code,
        "notes": collected.get("notes") or "",
        "title": collected["title"],
        "endDate": end_iso,
        "comments": collected["comments"],
        "documents": doc_rows,
        "startDate": start_iso,
        "unit_codes": collected.get("unit_codes") or [],
        "attachments": "",
        "description": collected["description"],
        "documents_ids": doc_ids,
        "inspectionDoneBy": inspection_done_by_role_id(collected),
        "inspection_done_by": inspection_done_by_role_id(collected),
        "lease_brand_mall": collected.get("lease_brand_mall") or lease_code,
        "document_status_map": status_map,
        # ── form intialState.admin ──
        "company_name": str(tenant_profile_id) if tenant_profile_id is not None else "",
        "tenant_contact": "",
        # ── LeaseBrandMall fieldsUpdate + ids ──
        "city": collected.get("city") or "",
        "contracted_area": collected.get("contracted_area") or "",
        "brand_id": collected.get("brand_id"),
        "tenant_profile_id": tenant_profile_id,
        "contract_id": collected.get("contract_id"),
        "property_id": collected.get("property_id"),
        # ── portal display strings for the date pickers ──
        "startDateLT": start_lt,
        "endDateLT": end_lt,
        "user_action": None,
    }

    return {
        "payload": inner,
        "title": collected["title"],
        "tenant_profile_id": tenant_profile_id,
        "property_id": collected.get("property_id"),
        "service_category": SERVICE_CATEGORY,
        "sub_category": sub_category,
        "lease_code": lease_code,
        "lease_id": collected["lease_id"],
        # A PENDING create does not open the approval operations (PRD §2).
        "status": "IN_PROCESS",
        "service_request_id": "",
    }


@trace_node("ceiling_payload_builder", "TOOL")
async def ceiling_payload_builder_node(state: ServiceRequestState) -> dict[str, Any]:
    """Build the create payload and store it in ``backend_refs.create_payload``."""
    collected: dict[str, Any] = state.get("collected_data") or {}
    sub_category: str = state.get("sub_category") or ""
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    try:
        payload = build_create_ceiling_payload(
            collected, sub_category, list(state.get("documents") or [])
        )
    except ValueError as exc:
        logger.warning("ceiling_payload_builder: build failed — %s", exc)
        return {
            "status": "FAILED",
            "response_message": (
                "I couldn't prepare the inspection request — some details are "
                "missing. Please review the request and try again."
            ),
        }
    backend_refs["create_payload"] = payload
    logger.info(
        "ceiling_payload_builder: built sub_category=%s lease_code=%s",
        sub_category,
        collected.get("lease_code"),
    )
    return {"backend_refs": backend_refs}
