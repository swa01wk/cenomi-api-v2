"""Build the platform API payload for General Inquiry SRs.

Produces the exact shape expected by ``POST /service-requests`` for
``service_category = INQUIRY``, ``sub_category = GENERAL``, engine V1.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.general_inquiry_schema import apply_lease_title_and_description
from app.observability.decorators import trace_node

logger = logging.getLogger(__name__)

# Same labels the classic form shows after translating the role code.
_ROLE_LABELS: dict[str, str] = {
    "PRIMARY_TENANT_CONTACT": "Primary Tenant Contact",
    "BILLING_CONTACT": "Billing Contact",
    "LEASING_ADMIN": "Leasing Admin",
    "FINANCIAL_CONTROLLER": "Financial Controller",
}


def _contact_label(row: dict[str, Any]) -> tuple[str, Any, str]:
    """Match the form dropdown: ``First Last - Role``."""
    user = row.get("user_info") if isinstance(row.get("user_info"), dict) else {}
    role_info = row.get("role_info") if isinstance(row.get("role_info"), dict) else {}
    role = str(role_info.get("role_name") or "").strip()
    name = f"{user.get('first_name') or ''} {user.get('last_name') or ''}".strip()
    role_label = _ROLE_LABELS.get(role, role.replace("_", " ").title() if role else "")
    text = f"{name} - {role_label}".strip(" -") if name or role_label else ""
    return text, user.get("user_id"), role


async def lookup_primary_tenant_contact(tenant_profile_id: Any) -> tuple[str, Any]:
    """Primary tenant contact for the company, else the first contact.

    The Work Permit form stores this on the payload and never asks for it.
    General Inquiry uses the same contact list. A failed lookup leaves the
    field blank rather than blocking submit.
    """
    tenant_id = str(tenant_profile_id or "").strip()
    if not tenant_id or tenant_id == "0":
        return "", None
    try:
        from app.agents.services.platform_api_client import get_platform_client

        rows = await get_platform_client().list_tenant_contacts(tenant_id)
    except Exception:
        logger.info(
            "general_inquiry_payload_builder: tenant contact lookup skipped tenant_profile_id=%s",
            tenant_id,
        )
        return "", None
    if not rows:
        return "", None
    chosen = next(
        (row for row in rows if _contact_label(row)[2] == "PRIMARY_TENANT_CONTACT"),
        rows[0],
    )
    label, user_id, _role = _contact_label(chosen)
    return label, user_id


def _document_ids(
    backend_refs: dict[str, Any],
    documents: list[dict[str, Any]],
) -> list[str]:
    """Ids from the upload buckets plus any General Inquiry file on the draft.

    ``document_upload_node`` only buckets handover types, so an SR_GEN_INQ
    file saved on the draft never lands in ``uploaded_documents``.
    """
    ids: list[str] = []
    for item in backend_refs.get("uploaded_documents") or []:
        doc_id = item.get("document_id") if isinstance(item, dict) else item
        text = str(doc_id or "").strip()
        if text and text not in ids:
            ids.append(text)
    for doc in documents:
        if str(doc.get("document_type_id") or doc.get("document_type") or "") != "SR_GEN_INQ":
            continue
        text = str(doc.get("document_id") or "").strip()
        if text and text not in ids:
            ids.append(text)
    return ids


def _inner_payload(
    collected: dict[str, Any],
    backend_refs: dict[str, Any],
    documents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    doc_ids = _document_ids(backend_refs, list(documents or []))
    tenant_profile_id = collected.get("tenant_profile_id")
    return {
        "mall": collected.get("mall", ""),
        "brand": collected.get("brand", ""),
        "lease": collected.get("lease_code") or collected.get("lease") or "",
        "title": collected.get("title", ""),
        "description": collected.get("description", ""),
        "notes": collected.get("notes", ""),
        "file": doc_ids[0] if doc_ids else "",
        "documents_ids": doc_ids,
        "lease_brand_mall": collected.get("lease_brand_mall", ""),
        "company_name": str(tenant_profile_id or ""),
        "tenant_contact": collected.get("tenant_contact") or "",
        "unit_codes": collected.get("unit_codes") or [],
        "city": collected.get("city", ""),
        "brand_id": collected.get("brand_id"),
        "tenant_profile_id": tenant_profile_id,
        "contract_id": collected.get("contract_id"),
        "property_id": collected.get("property_id"),
    }


def _review_body(
    action: str,
    collected: dict[str, Any],
    inner: dict[str, Any],
) -> dict[str, Any]:
    comment = str(collected.get("review_comment") or "").strip()
    if action == "reject":
        # Same body the form sends: status, the comment, and the inquiry payload.
        return {
            "status": "REJECTED",
            "sub_category": "GENERAL",
            "service_category": "INQUIRY",
            "comment": comment,
            "title": inner.get("title") or "",
            "payload": inner,
        }
    if action == "ask_more_info":
        return {
            "status": "ACTION_REQUIRED",
            "sub_category": "GENERAL",
            "comment": comment,
            "payload": inner,
        }
    if action == "add_comment":
        return {
            "status": "COMMENT",
            "sub_category": "GENERAL",
            "comment": comment,
            "payload": inner,
        }
    if action == "reassign":
        return {
            "status": "REASSIGN_TO",
            "sub_category": "GENERAL",
            "assign_to": str(collected.get("assign_to") or "").strip(),
            "comment": comment,
            "payload": inner,
        }
    return {
        "status": "APPROVED",
        "sub_category": "GENERAL",
        "comment": comment,
        "payload": inner,
    }


@trace_node("general_inquiry_payload_builder", "AGENT")
async def general_inquiry_payload_builder_node(
    state: ServiceRequestState,
) -> dict[str, Any]:
    """Build the create POST body, or the review PATCH body.

    Create matches ``POST /service-requests`` for INQUIRY / GENERAL.
    Review matches the platform PATCH statuses used by the approval actions.
    """
    collected = apply_lease_title_and_description(dict(state.get("collected_data") or {}))
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    documents: list[dict[str, Any]] = list(state.get("documents") or [])
    workflow_stage = state.get("workflow_stage") or ""

    contact = str(collected.get("tenant_contact") or "").strip()
    created_for = collected.get("created_for")
    if not contact:
        contact, created_for = await lookup_primary_tenant_contact(
            collected.get("tenant_profile_id")
        )
        if contact:
            collected["tenant_contact"] = contact
        if created_for:
            collected["created_for"] = created_for

    existing = backend_refs.get("create_payload") or {}
    stored_inner = existing.get("payload") if isinstance(existing.get("payload"), dict) else None
    if workflow_stage == "GI_CRT_REVIEW":
        inner = dict(stored_inner or _inner_payload(collected, backend_refs, documents))
    else:
        inner = _inner_payload(collected, backend_refs, documents)
    inner["company_name"] = str(collected.get("tenant_profile_id") or inner.get("company_name") or "")
    inner["tenant_contact"] = contact or inner.get("tenant_contact") or ""

    if workflow_stage == "GI_CRT_REVIEW":
        action = str(
            backend_refs.get("gi_action") or collected.get("gi_action") or "approve"
        )
        review_payload = _review_body(action, collected, inner)
        backend_refs["create_payload"] = review_payload
        backend_refs.pop("gi_pending_action", None)
        logger.info(
            "general_inquiry_payload_builder: review payload action=%s",
            action,
        )
        return {
            "backend_refs": backend_refs,
            "collected_data": collected,
        }

    create_payload: dict[str, Any] = {
        "payload": inner,
        "title": collected.get("title", ""),
        "tenant_profile_id": collected.get("tenant_profile_id") or "",
        "property_id": collected.get("property_id"),
        "service_category": "INQUIRY",
        "sub_category": "GENERAL",
        "lease_code": collected.get("lease_code", ""),
        "lease_id": collected.get("lease_id"),
        "service_request_id": "",
    }
    if created_for:
        create_payload["created_for"] = created_for

    logger.info("general_inquiry_payload_builder: payload built for INQUIRY/GENERAL")

    return {
        "backend_refs": {**backend_refs, "create_payload": create_payload},
        "collected_data": collected,
        "status": "READY_TO_SUBMIT",
    }
