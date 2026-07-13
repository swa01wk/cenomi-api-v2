"""Sync platform SR status when an existing SR ID is present in session state.

Responsibilities
----------------
- When ``backend_refs["sr_id"]`` is populated, call GET /service-requests/{sr_id}
  via the SR API service to fetch the current platform status.
- Map ``service_request_operations`` to a ``workflow_stage`` value:
    - Any FM_MANAGER or OPERATIONS operation with IN_PROGRESS  → "FM_REVIEW"
    - Any DD_ENGINEER operation with IN_PROGRESS               → "RDD_REVIEW"
    - All operations FINISHED/APPROVED                         → "SR_COMPLETED"
    - No operations / unknown                                  → unchanged
- Write results back to ``backend_refs``:
    - ``platform_sr_status``     — raw status string from the API
    - ``sr_operations``          — the operations list
- Update ``workflow_stage`` in state when a new stage is detected.
- Hydrate ``collected_data`` from the GET payload when available.
- Set ``response_decorations`` for review UI (via review_decorations_service).

Non-responsibilities
--------------------
- MUST NOT call the LLM.
- MUST NOT submit or modify any SR data.
- Skipped when ``sr_id`` is absent (pure CREATE_SR path with no existing SR).
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.services.review_decorations_service import (
    build_review_decorations,
    derive_user_role,
    merge_decorations,
    payload_to_collected_data,
)
from app.agents.services.service_request_api_service import get_service_request_api_service
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)

# Role strings that indicate FM review in the operations list.
_FM_ROLES: frozenset[str] = frozenset({"FM_MANAGER", "OPERATIONS"})
_RDD_ROLES: frozenset[str] = frozenset({"DD_ENGINEER"})
_TERMINAL_STATUSES: frozenset[str] = frozenset({"FINISHED", "APPROVED", "COMPLETED", "DONE"})


def _map_operations_to_stage(
    operations: list[dict[str, Any]],
    current_stage: str | None,
) -> str | None:
    """Derive workflow_stage from ``service_request_operations`` list.

    Priority: RDD_REVIEW > FM_REVIEW > SR_COMPLETED > unchanged.
    """
    if not operations:
        return current_stage

    in_progress_roles: set[str] = set()
    any_terminal = True

    for op in operations:
        status = (op.get("status") or "").upper()
        # Platform returns "assigned_role"; fall back to "role" for flexibility.
        role = (op.get("assigned_role") or op.get("role") or "").upper()
        if status == "IN_PROGRESS":
            in_progress_roles.add(role)
            any_terminal = False
        elif status not in _TERMINAL_STATUSES:
            any_terminal = False

    if _RDD_ROLES & in_progress_roles:
        return "RDD_REVIEW"
    if _FM_ROLES & in_progress_roles:
        return "FM_REVIEW"
    if any_terminal:
        return "SR_COMPLETED"

    return current_stage


def _attachments_to_documents(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map platform ``service_request_attachments`` into chat ``documents`` rows."""
    docs: list[dict[str, Any]] = []
    for att in attachments:
        doc_id = att.get("document_id") or att.get("id")
        doc_type = att.get("document_type_id") or att.get("document_type")
        if not doc_id and not doc_type:
            continue
        docs.append(
            {
                "document_id": str(doc_id) if doc_id else "",
                "document_type_id": str(doc_type) if doc_type else "",
                "file_name": att.get("file_name") or att.get("filename") or "",
            }
        )
    return docs


def _merge_documents(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Union session draft docs with platform attachments (never drop uploads).

    Chat uploads are written to the draft first; platform GET can lag or return
    only older FM attachments. Replacing the list wiped RDD reports and caused
    a Missing-document loop after a successful Attach.
    """
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_types: set[str] = set()

    def _add(doc: dict[str, Any]) -> None:
        doc_id = str(doc.get("document_id") or "").strip()
        doc_type = str(
            doc.get("document_type_id") or doc.get("document_type") or doc.get("type") or ""
        ).strip()
        if doc_id and doc_id in seen_ids:
            return
        # Keep first occurrence per type when id is missing.
        if not doc_id and doc_type and doc_type in seen_types:
            return
        if doc_id:
            seen_ids.add(doc_id)
        if doc_type:
            seen_types.add(doc_type)
        normalized = dict(doc)
        if doc_type and not normalized.get("document_type_id"):
            normalized["document_type_id"] = doc_type
        if "file_name" not in normalized and normalized.get("filename"):
            normalized["file_name"] = normalized["filename"]
        merged.append(normalized)

    for doc in existing:
        if isinstance(doc, dict):
            _add(doc)
    for doc in incoming:
        if isinstance(doc, dict):
            _add(doc)
    return merged


def _apply_document_refs(
    backend_refs: dict[str, Any],
    documents: list[dict[str, Any]],
) -> None:
    """Refresh uploaded_documents / rdd_document_id from the merged doc list."""
    ids = [str(d.get("document_id")) for d in documents if d.get("document_id")]
    if ids:
        backend_refs["uploaded_documents"] = ids
    rdd_report = next(
        (
            d
            for d in documents
            if (d.get("document_type_id") or d.get("document_type") or "")
            == "DR_SR_HANDOVER_REPORT"
            and d.get("document_id")
        ),
        None,
    )
    if rdd_report:
        backend_refs["rdd_document_id"] = str(rdd_report["document_id"])


@trace_node("sr_status_sync", "TOOL")
async def sr_status_sync_node(state: ServiceRequestState) -> dict[str, Any]:
    """Fetch platform SR status and update workflow_stage in state.

    Reads from state
    ----------------
    ``backend_refs``  — must contain ``sr_id`` to trigger a sync.
    ``workflow_stage`` — current stage; updated when platform status differs.

    Writes to state
    ---------------
    ``backend_refs``   — enriched with ``platform_sr_status`` and ``sr_operations``.
    ``workflow_stage`` — updated to FM_REVIEW, RDD_REVIEW, or SR_COMPLETED when
                         the platform operations warrant it.
    ``collected_data`` — hydrated from platform payload when present.
    ``response_decorations`` — review UI chrome for FM/RDD stages.
    """
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    sr_id: str | None = backend_refs.get("sr_id")

    if not sr_id:
        logger.debug("sr_status_sync_node: skipped — no sr_id in backend_refs")
        return {}

    backend_refs["sync_attempted"] = True
    current_stage: str | None = state.get("workflow_stage")
    svc = get_service_request_api_service()

    def _sync_failed(error: str, status_code: int | None = None) -> dict[str, Any]:
        backend_refs["sync_error"] = error
        log.warning(
            "sr_status_sync_node: GET sr_id=%s error=%s status_code=%s — continuing",
            sr_id,
            error,
            status_code,
        )
        msg = (
            f"I could not load service request **{sr_id}** from the platform "
            f"({error}). Please check that you have access to this SR, or pick it "
            "from the inbox list below."
        )
        return {
            "backend_refs": backend_refs,
            "response_message": msg,
            "status": "WAITING_FOR_USER",
        }

    try:
        result = await svc.get_service_request(sr_id)
    except Exception as exc:
        logger.warning(
            "sr_status_sync_node: get_service_request raised %s — continuing with cached stage",
            exc,
            exc_info=True,
        )
        return _sync_failed(str(exc))

    if result.error:
        return _sync_failed(result.error, result.status_code)

    backend_refs.pop("sync_error", None)

    # Support both ServiceRequestGetResult (has .status / .service_request_operations)
    # and the legacy ServiceRequestCreationResult (stores data in .response_payload).
    platform_status: str | None = getattr(result, "status", None)
    operations: list[dict[str, Any]] = list(getattr(result, "service_request_operations", None) or [])

    if not platform_status or not operations:
        # Fall back to response_payload for legacy / test mock shapes that use
        # _sr_status and _service_request_operations as envelope keys.
        response_payload: dict[str, Any] = getattr(result, "response_payload", None) or {}
        if not platform_status:
            platform_status = (
                response_payload.get("_sr_status")
                or response_payload.get("status")
            )
        if not operations:
            operations = (
                response_payload.get("_service_request_operations")
                or response_payload.get("service_request_operations")
                or []
            )

    new_stage = _map_operations_to_stage(operations, current_stage)

    backend_refs["platform_sr_status"] = platform_status
    backend_refs["sr_operations"] = operations

    auth = state.get("auth_context")
    user_role = derive_user_role(auth)
    if user_role:
        backend_refs["user_role"] = user_role

    log.info(
        "sr_status_sync_node.done",
        sr_id=sr_id,
        platform_status=platform_status,
        current_stage=current_stage,
        new_stage=new_stage,
        operations_count=len(operations),
    )

    updates: dict[str, Any] = {"backend_refs": backend_refs}
    effective_stage = new_stage or current_stage
    if new_stage and new_stage != current_stage:
        updates["workflow_stage"] = new_stage

    # When syncing an RDD-stage SR, propagate the platform status as rdd_status.
    _RDD_STATUSES: frozenset[str] = frozenset({"REPORT_SUBMITTED", "APPROVED", "APPROVED_WITH_CHANGES"})
    if effective_stage == "RDD_REVIEW" and platform_status and platform_status in _RDD_STATUSES:
        backend_refs["rdd_status"] = platform_status

    attachments: list[dict[str, Any]] = list(
        getattr(result, "service_request_attachments", None) or []
    )
    existing_docs: list[dict[str, Any]] = list(state.get("documents") or [])
    hydrated_docs = _attachments_to_documents(attachments) if attachments else []
    if existing_docs or hydrated_docs:
        merged_docs = _merge_documents(existing_docs, hydrated_docs)
        updates["documents"] = merged_docs
        _apply_document_refs(backend_refs, merged_docs)

    payload: dict[str, Any] = getattr(result, "payload", None) or {}
    if payload:
        # Snapshot for FM/RDD builders (open-existing path never ran create payload builder).
        inner = dict(payload)
        backend_refs["create_payload"] = {
            "payload": inner,
            "title": inner.get("title") or "",
            "tenant_profile_id": inner.get("tenant_profile_id"),
            "property_id": inner.get("property_id"),
            "service_category": "FIT_OUT_AND_HANDOVER",
            "sub_category": "HANDOVER",
            "lease_code": inner.get("lease_code") or inner.get("lease") or "",
            "lease_id": inner.get("lease_id") or inner.get("contract_id"),
            "service_request_id": sr_id,
        }
        existing_collected: dict[str, Any] = dict(state.get("collected_data") or {})
        hydrated = payload_to_collected_data(payload)
        if hydrated:
            updates["collected_data"] = {**existing_collected, **hydrated}

    # Ensure active_agent for handover review routing when opening existing SR.
    if effective_stage in ("FM_REVIEW", "RDD_REVIEW") and not state.get("active_agent"):
        updates["active_agent"] = "rdd_agent"
        updates["service_category"] = "FIT_OUT_AND_HANDOVER"
        updates["sub_category"] = "HANDOVER"

    merged_state = {**dict(state), **updates}
    review_deco = build_review_decorations(merged_state)
    if review_deco:
        updates["response_decorations"] = merge_decorations(
            state.get("response_decorations"),
            review_deco,
        )

    if not state.get("response_message") and sr_id and effective_stage in ("FM_REVIEW", "RDD_REVIEW"):
        updates["response_message"] = (
            f"Opening Handover SR **{sr_id}** ({effective_stage.replace('_', ' ').title()}). "
            "Review the details below, upload any required documents, then choose an action."
        )

    return updates
