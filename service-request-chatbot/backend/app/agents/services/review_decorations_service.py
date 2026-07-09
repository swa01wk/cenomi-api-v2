"""Handover review UI decorations for graph nodes.

Builds ``response_decorations`` payloads consumed by orchestration
``_finalize_response_ui()`` (blind merge — no workflow logic in orchestration).

Stage/workflow rules live here and in callers (``sr_status_sync``, entry nodes).
"""

from __future__ import annotations

from typing import Any

from app.agents.schemas.handover_schema import STAGE_REGISTRY
from app.agents.services.permission_service import ROLE_PERMISSION_MAP
from app.agents.services.resource_picker_decorations import build_handover_inbox_picker
from app.types.chat import AuthContext

# Primary graph roles checked for stage entry guards and decoration targeting.
_GRAPH_ROLES: tuple[str, ...] = ("FM_MANAGER", "OPERATIONS", "DD_ENGINEER", "MALL_MANAGER", "ADMIN")

_DOC_LABELS: dict[str, str] = {
    "SR_HANDOVER_CHECKLIST": "Handover Checklist",
    "SR_HANDOVER_SITE_SURVEY": "Site Survey",
    "SR_COP_CHECKLIST_OTHER": "COP Checklist",
    "SR_HANDOVER_OTHER": "Other",
    "DR_SR_HANDOVER_REPORT": "Handover Meeting Report",
    "SR_REJECTED_HANDOVER_REPORT": "Rejected Fitout and Handover Report",
}

_FM_DOC_TYPES: frozenset[str] = frozenset({
    "SR_HANDOVER_CHECKLIST",
    "SR_HANDOVER_SITE_SURVEY",
    "SR_COP_CHECKLIST_OTHER",
    "SR_HANDOVER_OTHER",
})

_RDD_DOC_TYPES: frozenset[str] = frozenset({
    "DR_SR_HANDOVER_REPORT",
    "SR_REJECTED_HANDOVER_REPORT",
    "SR_HANDOVER_OTHER",
})


def derive_user_role(auth: AuthContext | None) -> str | None:
    """Map JWT roles to a primary graph role name for stage guards."""
    if auth is None or not auth.roles:
        return None
    roles_upper = {r.upper() for r in auth.roles}
    for role_name in _GRAPH_ROLES:
        if role_name in roles_upper:
            return role_name
    for role_name, perms in ROLE_PERMISSION_MAP.items():
        if roles_upper & perms:
            if role_name in _GRAPH_ROLES:
                return role_name
    return next(iter(auth.roles), None)


def is_handover_reviewer(auth: AuthContext | None) -> bool:
    """True when the caller can action Handover SR review stages."""
    if auth is None:
        return False
    reviewer_roles = {"FM_MANAGER", "OPERATIONS", "DD_ENGINEER", "ADMIN"}
    roles_upper = {r.upper() for r in auth.roles}
    if roles_upper & reviewer_roles:
        return True
    review_perms = {
        "CAN_FM_REVIEW_HANDOVER_SR",
        "CAN_APPROVE_FM_HANDOVER_SR",
        "CAN_RDD_REVIEW_HANDOVER_SR",
        "CAN_APPROVE_HANDOVER_SR",
    }
    return bool(roles_upper & review_perms)


def payload_to_collected_data(payload: dict[str, Any]) -> dict[str, Any]:
    """Map platform GET SR payload into collected_data field keys."""
    if not payload:
        return {}
    mapped: dict[str, Any] = {}
    for key in (
        "title", "description", "startDate", "endDate", "inspection_done_by",
        "unit_readiness_date", "expected_handover_date", "actual_handover_date",
        "fitout_start_date", "fitout_end_date", "trading_date", "comments",
        "guideLineLink", "document_status",
        "lease_code", "brand", "mall", "city", "unit_codes", "contracted_area",
        "lease_id", "brand_id", "property_id", "tenant_profile_id",
    ):
        if key in payload and payload[key] not in (None, ""):
            mapped[key] = payload[key]
    return mapped


def build_inbox_decorations(auth: AuthContext | None) -> dict[str, Any]:
    """Decorations when a reviewer opens chat without an active SR."""
    if not is_handover_reviewer(auth):
        return {}
    return build_handover_inbox_picker()


def _uploaded_type_ids(documents: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for doc in documents:
        dt = doc.get("document_type_id") or doc.get("document_type")
        if dt:
            ids.add(str(dt))
    return ids


def _document_requirement_block(
    *,
    stage: str,
    documents: list[dict[str, Any]],
    session_id: str | None,
    sr_id: str | None,
) -> dict[str, Any] | None:
    stage_def = STAGE_REGISTRY.get(stage)
    if not stage_def or not stage_def.required_documents:
        return None

    uploaded = _uploaded_type_ids(documents)
    doc_types = _FM_DOC_TYPES if stage == "FM_REVIEW" else _RDD_DOC_TYPES
    relevant = [d for d in stage_def.required_documents if d in doc_types]
    if not relevant:
        relevant = list(stage_def.required_documents)

    # FM: at least one upload required — show all options until any is uploaded.
    if stage == "FM_REVIEW" and uploaded & _FM_DOC_TYPES:
        return None

    # RDD: mandatory report — hide panel once report uploaded.
    if stage == "RDD_REVIEW" and "DR_SR_HANDOVER_REPORT" in uploaded:
        return None

    doc_rows = [
        {
            "key": doc_type,
            "label": _DOC_LABELS.get(doc_type, doc_type),
            "required": doc_type in ("SR_HANDOVER_CHECKLIST", "DR_SR_HANDOVER_REPORT"),
            "status": "uploaded" if doc_type in uploaded else "missing",
        }
        for doc_type in relevant
    ]
    if all(r["status"] == "uploaded" for r in doc_rows):
        return None

    return {
        "documents": doc_rows,
        "uploadContext": {
            "session_id": session_id or "",
            "sr_id": sr_id or "",
        },
    }


def _action_suggestions(
    *,
    stage: str,
    user_role: str | None,
    rdd_status: str | None,
) -> dict[str, Any] | None:
    options: list[dict[str, str]] = []

    if stage == "FM_REVIEW" and user_role in ("FM_MANAGER", "OPERATIONS", "ADMIN"):
        options = [
            {"label": "Save Progress", "value": "save_fm_progress", "action": "save_fm_progress"},
            {"label": "Approve Review", "value": "approve_fm_review", "action": "approve_fm_review"},
        ]
    elif stage == "RDD_REVIEW" and user_role in ("DD_ENGINEER", "ADMIN"):
        if rdd_status == "REPORT_SUBMITTED":
            options = [
                {
                    "label": "Final Approve",
                    "value": "approve_rdd_final",
                    "action": "approve_rdd_final",
                },
            ]
        else:
            options = [
                {
                    "label": "Submit Report",
                    "value": "submit_rdd_report",
                    "action": "submit_rdd_report",
                },
            ]

    if not options:
        return None
    return {"kind": "labeled_options", "options": options}


def build_review_decorations(state: dict[str, Any]) -> dict[str, Any]:
    """Build ``response_decorations`` from graph state after SR sync / review entry."""
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    sr_id: str | None = backend_refs.get("sr_id")
    if not sr_id:
        return {}

    stage: str = state.get("workflow_stage") or ""
    if stage not in ("FM_REVIEW", "RDD_REVIEW"):
        return {}

    if state.get("confirmation_status") == "PENDING":
        return {"promote_preview": True}

    auth: AuthContext | None = state.get("auth_context")  # type: ignore[assignment]
    user_role = backend_refs.get("user_role") or derive_user_role(auth)
    documents: list[dict[str, Any]] = list(state.get("documents") or [])
    session_id: str | None = state.get("session_id")
    rdd_status: str | None = backend_refs.get("rdd_status")

    decorations: dict[str, Any] = {"promote_preview": True}

    suggestions = _action_suggestions(stage=stage, user_role=user_role, rdd_status=rdd_status)
    if suggestions:
        decorations["suggestions"] = suggestions

    doc_req = _document_requirement_block(
        stage=stage,
        documents=documents,
        session_id=session_id,
        sr_id=sr_id,
    )
    if doc_req:
        decorations["document_requirement"] = doc_req

    if len(decorations) <= 1 and not suggestions and not doc_req:
        decorations["promote_preview"] = True

    return decorations


def merge_decorations(
    existing: dict[str, Any] | None,
    new: dict[str, Any],
) -> dict[str, Any]:
    """Shallow merge decoration dicts (later keys win)."""
    merged = dict(existing or {})
    merged.update(new)
    return merged
