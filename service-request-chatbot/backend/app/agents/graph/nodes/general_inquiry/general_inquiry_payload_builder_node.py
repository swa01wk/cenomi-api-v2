"""Build the platform API payload for General Inquiry SRs.

Produces the exact shape expected by ``POST /service-requests`` for
``service_category = INQUIRY``, ``sub_category = GENERAL``, engine V1.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.observability.decorators import trace_node

logger = logging.getLogger(__name__)


@trace_node("general_inquiry_payload_builder", "AGENT")
async def general_inquiry_payload_builder_node(
    state: ServiceRequestState,
) -> dict[str, Any]:
    """Deterministically build the submission payload from ``collected_data``.

    The shape matches the platform's ``ServiceRequestDTO`` for
    INQUIRY / GENERAL (V1 engine, workflow 1, single-level Mall Manager).
    """
    collected = state.get("collected_data") or {}
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})

    uploaded_docs = backend_refs.get("uploaded_documents") or []
    doc_ids = [d["document_id"] for d in uploaded_docs if d.get("document_id")]

    inner_payload: dict[str, Any] = {
        "mall": collected.get("mall", ""),
        "brand": collected.get("brand", ""),
        "lease": collected.get("lease_code") or collected.get("lease") or "",
        "title": collected.get("title", ""),
        "description": collected.get("description", ""),
        "notes": collected.get("notes", ""),
        "file": doc_ids[0] if doc_ids else "",
        "documents_ids": doc_ids,
        "lease_brand_mall": collected.get("lease_brand_mall", ""),
        "unit_codes": collected.get("unit_codes") or [],
        "city": collected.get("city", ""),
        "brand_id": collected.get("brand_id"),
        "tenant_profile_id": collected.get("tenant_profile_id"),
        "contract_id": collected.get("contract_id"),
        "property_id": collected.get("property_id"),
    }

    create_payload: dict[str, Any] = {
        "payload": inner_payload,
        "title": collected.get("title", ""),
        "tenant_profile_id": collected.get("tenant_profile_id") or "",
        "property_id": collected.get("property_id"),
        "service_category": "INQUIRY",
        "sub_category": "GENERAL",
        "lease_code": collected.get("lease_code", ""),
        "lease_id": collected.get("lease_id"),
        "service_request_id": "",
    }

    logger.info("general_inquiry_payload_builder: payload built for INQUIRY/GENERAL")

    return {
        "backend_refs": {**backend_refs, "create_payload": create_payload},
        "status": "READY_TO_SUBMIT",
    }
