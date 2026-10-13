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
from app.agents.services.deleted_documents import (
    deleted_document_ids,
    without_deleted,
)
from app.agents.services.review_decorations_service import (
    build_review_decorations,
    can_act_in_stage,
    derive_user_role,
    merge_decorations,
    payload_to_collected_data,
)
from app.agents.registries.workflow_config import get_workflow_config
from app.agents.services.display_formatters import format_enum_label
from app.agents.services.service_request_api_service import get_service_request_api_service
from app.agents.schemas.work_permit_schema import (
    STAGE_LABELS,
    WP_REVIEW_STAGES,
    is_work_permit_category,
    map_wp_operations_to_stage,
    resolve_wp_sub_category,
)
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)

# Role strings that indicate FM review in the operations list.
_FM_ROLES: frozenset[str] = frozenset({"FM_MANAGER", "OPERATIONS"})
_RDD_ROLES: frozenset[str] = frozenset({"DD_ENGINEER"})
# Platform GET often returns numeric assigned_role (role_id). Keep both forms.
_FM_ROLE_IDS: frozenset[str] = frozenset({"20", "81"})  # FM_MANAGER, OPERATIONS
_RDD_ROLE_IDS: frozenset[str] = frozenset({"12"})  # DD_ENGINEER
_TERMINAL_STATUSES: frozenset[str] = frozenset({"FINISHED", "APPROVED", "COMPLETED", "DONE"})

# Fitout confirmation sub-categories — their single approval step maps to APPROVE_SR.
_FITOUT_CONFIRM_SUB_CATEGORIES: frozenset[str] = frozenset({
    "FIT_OUT_CONFIRM_FOS",
    "FIT_OUT_CONFIRM_END",
    "FIT_OUT_CONFIRM_TRADING",
})

# Fitout progress also has a single approval step, but it can end REJECTED as
# well as APPROVED — REJECT is in its allowed_actions — so it needs its own
# mapping rather than the confirmations' "terminal means approved".
_FITOUT_PROGRESS_SUB_CATEGORY = "FITOUT_PROGRESS"
_FITOUT_PROGRESS_AGENT = "rdd_fitout_progress_agent"

# Map fitout sub-category → active_agent name for re-hydrating the agent on open-SR.
_FITOUT_SUB_CATEGORY_AGENTS: dict[str, str] = {
    "FIT_OUT_CONFIRM_FOS": "rdd_fitout_start_agent",
    "FIT_OUT_CONFIRM_END": "rdd_fitout_end_agent",
    "FIT_OUT_CONFIRM_TRADING": "rdd_trading_confirmation_agent",
}


def _normalize_operation_role(raw: Any) -> str:
    """Normalize platform assigned_role (name or id) to an uppercase string."""
    if raw is None:
        return ""
    return str(raw).strip().upper()


def _map_operations_to_stage(
    operations: list[dict[str, Any]],
    current_stage: str | None,
    *,
    sub_category: str | None = None,
    platform_status: str | None = None,
    category: str | None = None,
) -> str | None:
    """Derive workflow_stage from ``service_request_operations`` list.

    Priority: WORK_PERMIT > HANDED_OVER early-exit > fitout-confirm early-exit >
              RDD_REVIEW > FM_REVIEW > SR_COMPLETED > unchanged.

    Work Permit must run before the Handover DD/FM mapping: a WP
    ``DD_ENGINEER IN_PROGRESS`` is ``WP_DD_REVIEW``, not ``RDD_REVIEW``.
    """
    # General Inquiry is reviewed by Customer Relations Team. The live workflow
    # row may still assign level 1 to Mall Manager (role 11), which the handover
    # mapper treats as FM_REVIEW. Opening that SR must stay on the inquiry path.
    if _is_general_inquiry(category, sub_category):
        status = (platform_status or "").upper()
        if status in {"APPROVED", "CLOSED"}:
            return "GI_APPROVED"
        if status == "REJECTED":
            return "GI_REJECTED"
        return "GI_CRT_REVIEW"

    if is_work_permit_category(category, sub_category):
        return map_wp_operations_to_stage(
            operations,
            sub_category=sub_category,
            platform_status=platform_status,
            current_stage=current_stage,
        )

    # Path B early exit: HANDED_OVER + APPROVED → terminal
    if sub_category == "HANDED_OVER" and (platform_status or "").upper() == "APPROVED":
        return "SR_COMPLETED"

    # Fitout progress: single approval step, two ways to finish.
    if sub_category == _FITOUT_PROGRESS_SUB_CATEGORY:
        status = (platform_status or "").upper()
        if status == "REJECTED":
            return "SR_REJECTED"
        if status in _TERMINAL_STATUSES:
            return "SR_APPROVED"
        for op in operations:
            if (op.get("status") or "").upper() == "IN_PROGRESS":
                return "APPROVE_SR"
        return current_stage

    # Fitout confirm early exit: single approval step — any in-progress → APPROVE_SR,
    # terminal → SR_APPROVED.
    if sub_category in _FITOUT_CONFIRM_SUB_CATEGORIES:
        if (platform_status or "").upper() in _TERMINAL_STATUSES:
            return "SR_APPROVED"
        for op in operations:
            if (op.get("status") or "").upper() == "IN_PROGRESS":
                return "APPROVE_SR"
        return current_stage  # no in-progress ops yet (e.g. just created)

    if not operations:
        return current_stage

    in_progress_roles: set[str] = set()
    any_terminal = True

    for op in operations:
        status = (op.get("status") or "").upper()
        # Platform returns "assigned_role" as name or role_id; fall back to "role".
        role = _normalize_operation_role(op.get("assigned_role") or op.get("role"))
        if status == "IN_PROGRESS":
            in_progress_roles.add(role)
            any_terminal = False
        elif status not in _TERMINAL_STATUSES:
            any_terminal = False

    if (_RDD_ROLES | _RDD_ROLE_IDS) & in_progress_roles:
        return "RDD_REVIEW"
    if (_FM_ROLES | _FM_ROLE_IDS) & in_progress_roles:
        return "FM_REVIEW"
    if any_terminal:
        return "SR_COMPLETED"

    return current_stage


def _is_general_inquiry(category: str | None, sub_category: str | None) -> bool:
    """True for INQUIRY / GENERAL, including a missing sub-category."""
    if (category or "").upper() != "INQUIRY":
        return False
    return (sub_category or "GENERAL").upper() in {"", "GENERAL"}


def _attachments_to_documents(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map platform ``service_request_attachments`` into chat ``documents`` rows.

    Preserves ``role`` and ``attached_by`` from the platform attachment so
    stage-specific upload UIs (FM / RDD document_requirement) can filter
    ownership. Without ``role``, an SR_HANDOVER_OTHER attached by MM during
    CREATE_SR shows up as "Uploaded" on RDD's own upload card even though the
    RDD reviewer never uploaded it (the type ID is shared across roles).
    """
    docs: list[dict[str, Any]] = []
    for att in attachments:
        doc_id = att.get("document_id") or att.get("id")
        doc_type = att.get("document_type_id") or att.get("document_type")
        if not doc_id and not doc_type:
            continue
        role = str(att.get("role") or att.get("assigned_role") or "").strip()
        attached_by = str(att.get("attached_by") or "").strip()
        row: dict[str, Any] = {
            "document_id": str(doc_id) if doc_id else "",
            "document_type_id": str(doc_type) if doc_type else "",
            "file_name": att.get("file_name") or att.get("filename") or "",
        }
        if role:
            row["role"] = role
        if attached_by:
            row["attached_by"] = attached_by
        docs.append(row)
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


# Owned by the documents path (upload / delete), never kept as a form edit.
_NON_FORM_EDIT_KEYS: frozenset[str] = frozenset(
    {
        "documents",
        "document_id",
        "document_type_id",
        "documents_ids",
        "document_ids",
        "document_saved",
        "deleted_document_ids",
        "fileName",
        "file_name",
        "filename",
    }
)


def _keep_form_edits(
    state: ServiceRequestState,
    updates: dict[str, Any],
    backend_refs: dict[str, Any],
    *,
    hydrated: dict[str, Any],
    stage: str | None,
    agent: str | None,
) -> None:
    """Keep the requester's unsaved form edits over the platform's saved values.

    On a form-edit stage (a Work Permit sent back to the tenant) the form bridge
    patches each edit to the chat, but every turn rehydrates from the platform,
    whose values predate the edit; the reply then wrote them back over the form.
    An edit leaves ``form_edits`` once the platform returns the same value, and
    all of them go once the SR leaves the stage (resubmitted).
    """
    cfg = get_workflow_config(agent)
    if cfg is None or stage not in cfg.form_edit_stages:
        backend_refs.pop("form_edits", None)
        return
    stored = backend_refs.get("form_edits")
    edits = {
        **(stored if isinstance(stored, dict) else {}),
        **(state.get("corrected_fields") or {}),
    }
    edits = {
        key: value
        for key, value in edits.items()
        if not key.startswith("_")
        and key not in _NON_FORM_EDIT_KEYS
        and (key not in hydrated or hydrated[key] != value)
    }
    if not edits:
        backend_refs.pop("form_edits", None)
        return
    base = updates.get("collected_data")
    if base is None:
        base = dict(state.get("collected_data") or {})
    updates["collected_data"] = {**base, **edits}
    backend_refs["form_edits"] = edits


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

    # Resolve category / sub_category from GET (detail uses ``category``).
    payload_tmp: dict[str, Any] = getattr(result, "payload", None) or {}
    sr_category: str | None = (
        state.get("service_category")
        or payload_tmp.get("category")
        or payload_tmp.get("service_category")
    )
    payload_sub = payload_tmp.get("sub_category")
    sr_sub_category: str | None = state.get("sub_category")
    if payload_sub and str(payload_sub).upper() not in {"", "WORK_PERMIT", "HANDOVER"}:
        sr_sub_category = payload_sub
    elif not sr_sub_category:
        sr_sub_category = payload_sub or payload_tmp.get("already_handovered")

    # When the backend doesn't return sub_category explicitly, infer it from the
    # fitout-specific fields stored in the inner payload.  These fields are unique
    # to their respective fitout confirm sub-categories and cannot appear in
    # standard handover SRs.
    if not sr_sub_category:
        if "fitout_start_done" in payload_tmp:
            sr_sub_category = "FIT_OUT_CONFIRM_FOS"
        elif "fitout_complete_done" in payload_tmp:
            sr_sub_category = "FIT_OUT_CONFIRM_END"
        elif "trading_done" in payload_tmp:
            sr_sub_category = "FIT_OUT_CONFIRM_TRADING"
        elif "fitout_progress_percentage" in payload_tmp:
            sr_sub_category = _FITOUT_PROGRESS_SUB_CATEGORY

    new_stage = _map_operations_to_stage(
        operations,
        current_stage,
        sub_category=sr_sub_category,
        platform_status=platform_status,
        category=sr_category,
    )

    # Never regress from RDD_REVIEW back to FM_REVIEW: platform lag can keep
    # the FM operation visible briefly after fm_api_submission_node has already
    # advanced the workflow.  Trust the locally-committed stage.
    if new_stage == "FM_REVIEW" and current_stage == "RDD_REVIEW":
        log.info(
            "sr_status_sync_node: suppressing FM_REVIEW regression (current=%s) for sr_id=%s",
            current_stage,
            sr_id,
        )
        new_stage = "RDD_REVIEW"

    backend_refs["platform_sr_status"] = platform_status
    backend_refs["sr_operations"] = operations
    history = list(getattr(result, "service_request_history", None) or [])
    if history:
        backend_refs["sr_history"] = history
    creator = {
        key: value
        for key, value in (
            ("name", getattr(result, "created_by", None)),
            ("role", getattr(result, "created_user_role", None)),
            ("created_at", getattr(result, "created_at", None)),
        )
        if isinstance(value, str) and value.strip()
    }
    if creator:
        backend_refs["sr_creator"] = creator
    extension = getattr(result, "work_permit_extension", None)
    if isinstance(extension, dict):
        backend_refs["work_permit_extension"] = extension

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
        # A platform GET that lags the delete must not bring the file back.
        merged_docs = without_deleted(merged_docs, deleted_document_ids(backend_refs))
        updates["documents"] = merged_docs
        _apply_document_refs(backend_refs, merged_docs)

    payload: dict[str, Any] = getattr(result, "payload", None) or {}
    wp_sr = is_work_permit_category(sr_category, sr_sub_category)
    gi_sr = _is_general_inquiry(sr_category, sr_sub_category)
    hydrated: dict[str, Any] = {}
    if payload:
        # Snapshot for builders (open-existing path never ran create payload builder).
        inner = dict(payload)
        wp_sub = resolve_wp_sub_category(sr_sub_category, inner) if wp_sr else None
        if wp_sr:
            snapshot_category = "WORK_PERMIT"
            snapshot_sub = wp_sub
        elif gi_sr:
            snapshot_category = "INQUIRY"
            snapshot_sub = "GENERAL"
        else:
            snapshot_category = "FIT_OUT_AND_HANDOVER"
            snapshot_sub = sr_sub_category or inner.get("sub_category") or "HANDOVER"
        backend_refs["create_payload"] = {
            "payload": inner,
            "title": inner.get("title") or "",
            "tenant_profile_id": inner.get("tenant_profile_id"),
            "property_id": inner.get("property_id"),
            "service_category": snapshot_category,
            "sub_category": snapshot_sub,
            "lease_code": inner.get("lease_code") or inner.get("lease") or "",
            "lease_id": inner.get("lease_id") or inner.get("contract_id"),
            "service_request_id": sr_id,
        }
        if (sr_sub_category or "") == _FITOUT_PROGRESS_SUB_CATEGORY:
            # The approve PATCH must echo this payload back key for key —
            # ValidateEditableFields refuses any change outside
            # [fitout_progress_percentage, comment] — so keep the platform's own
            # copy rather than one rebuilt from collected_data.
            backend_refs["stored_payload"] = dict(payload)

        existing_collected: dict[str, Any] = dict(state.get("collected_data") or {})
        hydrated = payload_to_collected_data(payload)
        if hydrated:
            # Fitout/Trading APPROVE: session answers must win.  Re-hydrating the
            # platform create payload on every turn was overwriting trading_done=no
            # (and cleared actual dates) back to the original yes/actual values.
            if (
                effective_stage == "APPROVE_SR"
                and (
                    (sr_sub_category or "") in _FITOUT_CONFIRM_SUB_CATEGORIES
                    or (sr_sub_category or "") == _FITOUT_PROGRESS_SUB_CATEGORY
                )
                and existing_collected
            ):
                updates["collected_data"] = {**hydrated, **existing_collected}
            else:
                updates["collected_data"] = {**existing_collected, **hydrated}

    _keep_form_edits(
        state,
        updates,
        backend_refs,
        hydrated=hydrated,
        stage=effective_stage,
        agent="work_permit_agent" if wp_sr else state.get("active_agent"),
    )

    # Ensure active_agent for review routing when opening existing SR.
    if wp_sr:
        updates["active_agent"] = "work_permit_agent"
        updates["service_category"] = "WORK_PERMIT"
        if sr_sub_category:
            updates["sub_category"] = sr_sub_category
    elif gi_sr:
        updates["active_agent"] = "general_inquiry_agent"
        updates["service_category"] = "INQUIRY"
        updates["sub_category"] = "GENERAL"
    elif not state.get("active_agent"):
        if effective_stage in ("FM_REVIEW", "RDD_REVIEW"):
            updates["active_agent"] = "rdd_handover_agent"
            updates["service_category"] = "FIT_OUT_AND_HANDOVER"
            updates["sub_category"] = sr_sub_category or "HANDOVER"
        elif (
            effective_stage == "APPROVE_SR"
            and sr_sub_category == _FITOUT_PROGRESS_SUB_CATEGORY
        ):
            # Both this workflow and the fitout confirmations declare APPROVE_SR
            # in stage_sync_nodes, so _route_after_sync's "scan every config"
            # fallback could pick either. Naming the agent is what makes the
            # routing deterministic.
            updates["active_agent"] = _FITOUT_PROGRESS_AGENT
            updates["service_category"] = "FIT_OUT_AND_HANDOVER"
            updates["sub_category"] = sr_sub_category
        elif effective_stage == "APPROVE_SR" and sr_sub_category in _FITOUT_CONFIRM_SUB_CATEGORIES:
            # Restore the fitout confirm agent so _route_after_sync uses the correct
            # stage_sync_nodes and validation/missing-field nodes behave correctly.
            fitout_agent = _FITOUT_SUB_CATEGORY_AGENTS.get(sr_sub_category or "")
            if fitout_agent:
                updates["active_agent"] = fitout_agent
                updates["service_category"] = "FIT_OUT_AND_HANDOVER"
                updates["sub_category"] = sr_sub_category

    merged_state = {**dict(state), **updates}
    review_deco = build_review_decorations(merged_state)
    if review_deco:
        updates["response_decorations"] = merge_decorations(
            state.get("response_decorations"),
            review_deco,
        )

    if not state.get("response_message") and sr_id and effective_stage == "GI_CRT_REVIEW":
        from app.agents.services.general_inquiry_review_summary import (
            build_gi_review_card,
        )

        collected = updates.get("collected_data") or payload_to_collected_data(payload)
        card = build_gi_review_card(
            collected,
            sr_id=str(sr_id),
            lease_label=payload.get("lease_brand_mall"),
        )
        updates["response_message"] = card["message"]
        updates["response_ui"] = {**card, "verbatim": True}
    elif not state.get("response_message") and sr_id and effective_stage in (
        "GI_APPROVED",
        "GI_REJECTED",
    ):
        if effective_stage == "GI_APPROVED":
            lead = f"General Inquiry SR **{sr_id}** is approved."
        else:
            lead = f"General Inquiry SR **{sr_id}** was rejected."
        updates["response_message"] = lead
        updates["response_ui"] = {"type": "message", "verbatim": True}
    elif not state.get("response_message") and sr_id and effective_stage in (
        "FM_REVIEW",
        "RDD_REVIEW",
        "APPROVE_SR",
        *WP_REVIEW_STAGES,
    ):
        if effective_stage in WP_REVIEW_STAGES:
            label = STAGE_LABELS.get(effective_stage or "", "Work Permit Review")
            user_role = backend_refs.get("user_role") or derive_user_role(auth)

            if user_role and not can_act_in_stage(effective_stage or "", user_role):
                updates["response_message"] = (
                    f"Opening Work Permit SR **{sr_id}** ({label}). "
                    "This hop is awaiting another reviewer — you can view the Service Request "
                    "status below."
                )
            else:
                updates["response_message"] = (
                    f"Opening Work Permit SR **{sr_id}** ({label}). "
                    "Review the details below, then approve or reject this hop."
                )
        elif (
            effective_stage == "APPROVE_SR"
            and sr_sub_category == _FITOUT_PROGRESS_SUB_CATEGORY
        ):
            # Usually replaced in the same turn by the percentage question, which
            # says the same thing with the number in it; this is what speaks when
            # the flow has nothing to ask.
            updates["response_message"] = (
                f"Opening Fitout Progress SR **{sr_id}** for approval."
            )
        elif effective_stage == "APPROVE_SR" and sr_sub_category in _FITOUT_CONFIRM_SUB_CATEGORIES:
            _FITOUT_OPEN_LABELS = {
                "FIT_OUT_CONFIRM_FOS": "Fitout Start Confirmation",
                "FIT_OUT_CONFIRM_END": "Fitout End Confirmation",
                "FIT_OUT_CONFIRM_TRADING": "Trading Confirmation",
            }
            label = _FITOUT_OPEN_LABELS.get(sr_sub_category or "", "Fitout Confirmation")
            updates["response_message"] = (
                f"Opening {label} SR **{sr_id}** for approval. "
                "I'll walk you through the yes/no confirmation and any dates that need "
                "updating, then show a preview before you approve."
            )
        elif effective_stage == "RDD_REVIEW":
            collected = dict(merged_state.get("collected_data") or {})
            dates_ready = all(
                collected.get(f) not in (None, "")
                for f in (
                    "actual_handover_date",
                    "fitout_start_date",
                    "fitout_end_date",
                )
            )
            if dates_ready:
                updates["response_message"] = (
                    f"Opening Handover SR **{sr_id}** ({format_enum_label(effective_stage)}). "
                    "Review the details below, upload any required documents, then choose an action."
                )
            else:
                updates["response_message"] = (
                    f"Opening Handover SR **{sr_id}** ({format_enum_label(effective_stage)}). "
                    "Please provide the required dates first "
                    "(actual handover, fitout start, and fitout end). "
                    "Document upload will be available after the dates are set."
                )
        elif effective_stage in ("FM_REVIEW", "RDD_REVIEW"):
            updates["response_message"] = (
                f"Opening Handover SR **{sr_id}** ({format_enum_label(effective_stage)}). "
                "Review the details below, upload any required documents, then choose an action."
            )

    return updates
