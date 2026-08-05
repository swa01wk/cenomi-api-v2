"""Handover review UI decorations for graph nodes.

Builds ``response_decorations`` payloads consumed by orchestration
``_finalize_response_ui()`` (blind merge — no workflow logic in orchestration).

Stage/workflow rules live here and in callers (``sr_status_sync``, entry nodes).
"""

from __future__ import annotations

from typing import Any

from app.agents.schemas.handover_schema import (
    RDD_DOC_TYPE_MEETING,
    RDD_DOC_TYPE_OTHER,
    RDD_DOC_TYPE_REJECTED,
    RDD_REJECTED_STATUS,
    STAGE_REGISTRY,
    get_missing_fields,
)
from app.agents.services.permission_service import ROLE_PERMISSION_MAP
from app.agents.services.resource_picker_decorations import build_handover_inbox_picker
from app.types.chat import AuthContext

# Primary graph roles checked for stage entry guards and decoration targeting.
_GRAPH_ROLES: tuple[str, ...] = ("FM_MANAGER", "OPERATIONS", "DD_ENGINEER", "MALL_MANAGER", "ADMIN")

_DOC_LABELS: dict[str, str] = {
    "SR_HANDOVER_CHECKLIST": "Handover Checklist",
    "SR_HANDOVER_SITE_SURVEY": "Site Survey",
    "SR_COP_CHECKLIST_OTHER": "COP Checklist / Other",  # Fix 2: was SR_HANDOVER_OTHER
    "SR_HANDOVER_OTHER": "Other",
    "DR_SR_HANDOVER_REPORT": "Handover Meeting Report",
    "SR_REJECTED_HANDOVER_REPORT": "Rejected Fitout and Handover Report",
    "SR_HANDED_OVER_OTHER": "Other (Already Trading)",
}

_FM_DOC_TYPES: frozenset[str] = frozenset({
    "SR_HANDOVER_CHECKLIST",
    "SR_HANDOVER_SITE_SURVEY",
    "SR_COP_CHECKLIST_OTHER",  # Fix 2: replaced SR_HANDOVER_OTHER
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
        "lease_id", "brand_id", "property_id", "tenant_profile_id", "contract_id",
        # ── Fitout Confirmation workflow fields ────────────────────────────────
        "lease_brand_mall",
        "fitout_start_done", "actual_fitout_start_date",
        "planned_fitout_start_date", "planned_fitout_end_date", "planned_trading_date",
        "fitout_complete_done", "actual_fitout_complete_date", "planned_fitout_complete_date",
        "trading_done", "actual_trading_date",
        "delay_reason",
    ):
        if key in payload and payload[key] not in (None, ""):
            mapped[key] = payload[key]
    # Handover nested payload stores the code as ``lease``, not ``lease_code``.
    if not mapped.get("lease_code") and payload.get("lease") not in (None, ""):
        mapped["lease_code"] = payload["lease"]
    # Detail GET omits top-level lease_id; create payload stores the same value
    # as contract_id (cenomi-api MapHandoverLeaseLookupRowToDTO sets both equal).
    if mapped.get("lease_id") in (None, "", 0) and mapped.get("contract_id") not in (
        None,
        "",
        0,
    ):
        mapped["lease_id"] = mapped["contract_id"]
    return mapped


def build_inbox_decorations(auth: AuthContext | None) -> dict[str, Any]:
    """Decorations when a reviewer opens chat without an active SR."""
    if not is_handover_reviewer(auth):
        return {}
    return build_handover_inbox_picker()


def _uploaded_type_ids(
    documents: list[dict[str, Any]],
    role_filter: str | tuple[str, ...] | None = None,
) -> set[str]:
    """Return the set of ``document_type_id`` values present in ``documents``.

    ``role_filter``: when set (e.g. ``"DD_ENGINEER"`` or a tuple), only count
    docs whose ``role``/``assigned_role`` matches — OR whose role is empty
    (session-uploaded via chat, not yet stamped with a role). Used by the RDD
    upload card so an SR_HANDOVER_OTHER uploaded by MM during CREATE_SR
    doesn't get shown as "Uploaded" in RDD's own upload gate.
    """
    if role_filter is None:
        allowed: tuple[str, ...] | None = None
    elif isinstance(role_filter, str):
        allowed = (role_filter.upper(),)
    else:
        allowed = tuple(r.upper() for r in role_filter)

    ids: set[str] = set()
    for doc in documents:
        dt = doc.get("document_type_id") or doc.get("document_type")
        if not dt:
            continue
        if allowed is not None:
            role_val = str(
                doc.get("role") or doc.get("assigned_role") or ""
            ).strip().upper()
            # Session-uploaded rows carry no role field; treat them as belonging
            # to whichever stage is currently active (the user just uploaded).
            if role_val and role_val not in allowed:
                continue
        ids.add(str(dt))
    return ids


def _document_requirement_block(
    *,
    stage: str,
    documents: list[dict[str, Any]],
    session_id: str | None,
    sr_id: str | None,
    collected: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    stage_def = STAGE_REGISTRY.get(stage)
    if not stage_def or not stage_def.required_documents:
        return None

    # RDD's card must only reflect DD_ENGINEER's own uploads. SR_HANDOVER_OTHER
    # is also allowed for MALL_MANAGER (uploaded during CREATE_SR) and shares
    # the same document_type_id — without a role filter, MM's Other would
    # show up as "Uploaded" in RDD's gate even though RDD never uploaded it.
    if stage == "RDD_REVIEW":
        uploaded = _uploaded_type_ids(documents, role_filter=("DD_ENGINEER",))
    elif stage == "FM_REVIEW":
        uploaded = _uploaded_type_ids(
            documents, role_filter=("FM_MANAGER", "OPERATIONS")
        )
    else:
        uploaded = _uploaded_type_ids(documents)
    collected = collected or {}
    doc_types = _FM_DOC_TYPES if stage == "FM_REVIEW" else _RDD_DOC_TYPES
    relevant = [d for d in stage_def.required_documents if d in doc_types]
    if not relevant:
        relevant = list(stage_def.required_documents)

    # RDD: show all three types in one card (matches FM UX). Meeting Report
    # and Rejected are marked required-pair (either one satisfies); Other is
    # optional. Keep the card visible even after the mandatory pair is uploaded
    # so the user can see the ✓ status and click the Continue chip to advance
    # to date collection (dates come AFTER the upload per the payload shape).
    if stage == "RDD_REVIEW":
        relevant = [
            RDD_DOC_TYPE_MEETING,
            RDD_DOC_TYPE_REJECTED,
            RDD_DOC_TYPE_OTHER,
        ]

    doc_rows = [
        {
            "key": doc_type,
            "label": _DOC_LABELS.get(doc_type, doc_type),
            "required": (
                # RDD: mark the two mandatory-pair types as required so the
                # FE badge shows the red asterisk. Other stays optional.
                doc_type in (RDD_DOC_TYPE_MEETING, RDD_DOC_TYPE_REJECTED)
                if stage == "RDD_REVIEW"
                else False  # FM needs any one of the three — none individually mandatory
            ),
            "status": "uploaded" if doc_type in uploaded else "missing",
        }
        for doc_type in relevant
    ]

    upload_context: dict[str, Any] = {
        "session_id": session_id or "",
        "sr_id": sr_id or "",
    }
    # For RDD, list every allowed type so the FE dropdown lets the user pick
    # which of the three they're attaching (matches FM's multi-type upload).
    if stage == "RDD_REVIEW":
        upload_context["allowedDocumentTypes"] = [
            RDD_DOC_TYPE_MEETING,
            RDD_DOC_TYPE_REJECTED,
            RDD_DOC_TYPE_OTHER,
        ]

    return {
        "documents": doc_rows,
        "uploadContext": upload_context,
    }


def maybe_reset_rdd_upload_slot(
    collected: dict[str, Any],
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    """Clear chosen type after optional Rejected/Other upload if Meeting Report missing.

    Keeps Submit gated on Meeting Report while allowing additional uploads first.
    """
    uploaded = _uploaded_type_ids(documents)
    if RDD_DOC_TYPE_MEETING in uploaded:
        return collected
    chosen = collected.get("rdd_upload_doc_type")
    if chosen not in (RDD_DOC_TYPE_REJECTED, RDD_DOC_TYPE_OTHER):
        return collected
    if chosen not in uploaded:
        return collected
    out = dict(collected)
    out.pop("rdd_upload_doc_type", None)
    if out.get("document_status") == RDD_REJECTED_STATUS:
        out.pop("document_status", None)
    return out


# Terminal / post-approval RDD statuses — no action chips (View SR deferred).
_RDD_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        "APPROVED",
        "APPROVED_WITH_CHANGES",
        "COMPLETED",
        "REJECTED",
        "CANCELLED",
    }
)


def _fm_review_ready(
    collected: dict[str, Any],
    documents: list[dict[str, Any]],
) -> bool:
    """True when FM can approve: readiness date set and at least one FM doc uploaded."""
    date_ok = collected.get("unit_readiness_date") not in (None, "")
    if not date_ok:
        return False
    uploaded = _uploaded_type_ids(documents)
    return bool(uploaded & _FM_DOC_TYPES)


def is_fm_review_ready(
    collected: dict[str, Any],
    documents: list[dict[str, Any]],
) -> bool:
    """Public alias for FM approve readiness (date + ≥1 FM document)."""
    return _fm_review_ready(collected, documents)


_FM_UPLOAD_ASK_MESSAGE = (
    "Please upload at least one FM document (Handover Checklist, Site Survey, "
    "or Other) to continue with the review."
)
_FM_UPLOAD_REVIEW_MESSAGE = (
    "Review the details below. You can upload FM documents (Handover Checklist, "
    "Site Survey, or Other), then choose an action."
)
_RDD_UPLOAD_ASK_MESSAGE = (
    "Please upload either the Handover Meeting Report or the Rejected Fitout and "
    "Handover Report to continue with the review. You can also attach an Other "
    "supporting document."
)

_RDD_UPLOAD_ASK_BY_TYPE: dict[str, str] = {
    RDD_DOC_TYPE_MEETING: (
        "Please upload the Handover Meeting Report to continue with the review."
    ),
    RDD_DOC_TYPE_REJECTED: (
        "Please upload the Rejected Fitout and Handover Report "
        "(status will be Disapproved)."
    ),
    RDD_DOC_TYPE_OTHER: (
        "Please upload the additional supporting document (Other)."
    ),
}


def build_document_upload_ask_response(state: dict[str, Any]) -> dict[str, Any]:
    """WAITING_FOR_USER payload that asks for required stage documents + upload card.

    Clears ``confirmation_status`` so ``build_review_decorations`` includes
    ``document_requirement`` (PENDING confirmation otherwise strips it).
    """
    stage: str = state.get("workflow_stage") or ""
    collected = dict(state.get("collected_data") or {})
    documents = list(state.get("documents") or [])
    if stage == "FM_REVIEW":
        message = (
            _FM_UPLOAD_REVIEW_MESSAGE
            if _fm_review_ready(collected, documents)
            else _FM_UPLOAD_ASK_MESSAGE
        )
    elif stage == "RDD_REVIEW":
        # New multi-type UI shows all three RDD doc rows at once; use the
        # generic ask message. The per-type messages (_RDD_UPLOAD_ASK_BY_TYPE)
        # are kept for backward compat but only fire if the legacy chip field
        # is still set on old sessions.
        chosen = collected.get("rdd_upload_doc_type")
        message = (
            _RDD_UPLOAD_ASK_BY_TYPE.get(str(chosen), _RDD_UPLOAD_ASK_MESSAGE)
            if chosen
            else _RDD_UPLOAD_ASK_MESSAGE
        )
    else:
        return {}

    deco_state = {**dict(state), "confirmation_status": None}
    decorations = build_review_decorations(deco_state)
    return {
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": {
            "type": "message",
            "message": message,
        },
        "response_decorations": decorations,
        "active_field": None,
        "confirmation_status": None,
        "confirmation_required": False,
    }


def _rdd_ready_to_submit(
    collected: dict[str, Any],
    documents: list[dict[str, Any]],
) -> bool:
    """True when Submit Report may be offered: fields complete + Meeting Report uploaded.

    Must stay false while the bot is still asking for dates, type, status, or
    the mandatory handover meeting report — otherwise the Submit chip appears
    beside upload / field questions.
    """
    if RDD_DOC_TYPE_MEETING not in _uploaded_type_ids(documents):
        return False
    return not get_missing_fields("RDD_REVIEW", collected)


def _action_suggestions(
    *,
    stage: str,
    user_role: str | None,
    rdd_status: str | None,
    collected: dict[str, Any] | None = None,
    documents: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return stage-specific action chips. Never mix FM and RDD actions.

    FM shows exactly one chip:
    - Save Progress while incomplete (persists data, does not approve)
    - Approve Review when ready (advances toward RDD)

    RDD Submit Report is shown only when ``_rdd_ready_to_submit`` — never while
    required data or the Meeting Report is still outstanding.
    """
    options: list[dict[str, str]] = []
    collected = collected or {}
    documents = documents or []

    if stage == "FM_REVIEW" and user_role in ("FM_MANAGER", "OPERATIONS", "ADMIN"):
        if _fm_review_ready(collected, documents):
            options = [
                {
                    "label": "Approve Review",
                    "value": "approve_fm_review",
                    "action": "approve_fm_review",
                },
            ]
        else:
            options = [
                {
                    "label": "Save Progress",
                    "value": "save_fm_progress",
                    "action": "save_fm_progress",
                },
            ]
    elif stage == "RDD_REVIEW" and user_role in ("DD_ENGINEER", "ADMIN"):
        status = (rdd_status or "").upper()
        if status in _RDD_TERMINAL_STATUSES:
            # Post-approval: hide action suggestions (View SR deferred).
            options = []
        elif status == "REPORT_SUBMITTED":
            options = [
                {
                    "label": "Final Approve",
                    "value": "approve_rdd_final",
                    "action": "approve_rdd_final",
                },
            ]
        elif _rdd_ready_to_submit(collected, documents):
            options = [
                {
                    "label": "Submit Report",
                    "value": "submit_rdd_report",
                    "action": "submit_rdd_report",
                },
            ]
        else:
            # Still collecting fields / documents — no Submit chip.
            options = []
    # CREATE_SR / SR_COMPLETED / unknown stage → no review action chips

    if not options:
        return None
    return {"kind": "labeled_options", "options": options}


_RDD_DATE_FIELDS_FOR_DOCS: tuple[str, ...] = (
    "actual_handover_date",
    "fitout_start_date",
    "fitout_end_date",
)

# Fields that must be collected BEFORE showing the document upload card.
# With the reordered RDD flow: guideline + doc type first, then dates.
_RDD_PRE_UPLOAD_FIELDS: tuple[str, ...] = (
    "guideLineLink",
    "rdd_upload_doc_type",
)


def _rdd_required_dates_complete(collected: dict[str, Any]) -> bool:
    """True when the pre-upload RDD fields (guideline + doc type) are present.

    Per the updated field order, document upload is gated on guideLineLink and
    rdd_upload_doc_type being collected — not on the date fields (which are
    asked after doc type).
    """
    for field in _RDD_PRE_UPLOAD_FIELDS:
        value = collected.get(field)
        if value is None or value == "":
            return False
    return True


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
    collected: dict[str, Any] = dict(state.get("collected_data") or {})

    decorations: dict[str, Any] = {"promote_preview": True}

    suggestions = _action_suggestions(
        stage=stage,
        user_role=user_role,
        rdd_status=rdd_status,
        collected=collected,
        documents=documents,
    )
    if suggestions:
        decorations["suggestions"] = suggestions

    # After report submit, the mandatory RDD doc is already on the platform —
    # don't re-prompt uploads while offering Final Approve.
    # For RDD, show the doc upload card as soon as guideLineLink is captured;
    # dates come AFTER upload (their values are nested on the uploaded row).
    # No longer gated on the removed rdd_upload_doc_type chip.
    show_docs = not (stage == "RDD_REVIEW" and (rdd_status or "").upper() == "REPORT_SUBMITTED")
    if stage == "RDD_REVIEW" and not collected.get("guideLineLink"):
        # Wait until the guideline link is provided before offering uploads.
        show_docs = False
    if show_docs:
        doc_req = _document_requirement_block(
            stage=stage,
            documents=documents,
            session_id=session_id,
            sr_id=sr_id,
            collected=collected,
        )
        if doc_req:
            decorations["document_requirement"] = doc_req
            # RDD: once DD_ENGINEER's mandatory pair is satisfied, surface a
            # Continue chip so the user can advance to date collection (dates
            # come after the doc uploads per the platform's nested-doc payload
            # shape). Filter by role so MM's SR_HANDOVER_OTHER doesn't count.
            if stage == "RDD_REVIEW":
                rdd_uploaded = _uploaded_type_ids(
                    documents, role_filter=("DD_ENGINEER",)
                )
                if rdd_uploaded & {RDD_DOC_TYPE_MEETING, RDD_DOC_TYPE_REJECTED}:
                    decorations["suggestions"] = build_rdd_upload_continue_suggestions()

    if len(decorations) <= 1 and not suggestions and not decorations.get("document_requirement"):
        decorations["promote_preview"] = True

    return decorations


# CREATE_SR optional MM upload offer (Yes → show card; No/Continue → hide).
MM_OPTIONAL_UPLOAD_STATUS_KEY = "mm_optional_upload_status"
MM_OPTIONAL_AWAITING_CHOICE = "awaiting_choice"
MM_OPTIONAL_AWAITING_UPLOAD = "awaiting_upload"
MM_OPTIONAL_DONE = "done"

_MM_OPTIONAL_OFFER_MESSAGE = (
    "Lease found. Would you like to upload an optional supporting document "
    "for this handover request?"
)
_MM_OPTIONAL_UPLOAD_MESSAGE = (
    "Upload an optional supporting document below, or tap Continue when you "
    "are done (you can also continue without uploading)."
)


def build_mm_optional_upload_offer_suggestions() -> dict[str, Any]:
    """Yes/No chips before showing the CREATE_SR optional upload card."""
    return {
        "kind": "labeled_options",
        "options": [
            {
                "label": "Yes",
                "value": "yes",
                "action": "accept_optional_upload",
            },
            {
                "label": "No",
                "value": "no",
                "action": "skip_optional_upload",
            },
        ],
    }


def build_mm_optional_upload_continue_suggestions() -> dict[str, Any]:
    """Continue chip while the optional upload card is visible."""
    return {
        "kind": "labeled_options",
        "options": [
            {
                "label": "Continue",
                "value": "continue",
                "action": "continue_optional_upload",
            },
        ],
    }


def build_rdd_upload_continue_suggestions() -> dict[str, Any]:
    """Continue chip shown after RDD's Meeting Report or Rejected Report is uploaded.

    Lets the user optionally attach Other and then click Continue to advance to
    the date-collection step. Without this chip the flow stalls at the upload
    card because no missing field triggers the next question.
    """
    return {
        "kind": "labeled_options",
        "options": [
            {
                "label": "Continue",
                "value": "continue",
                "action": "continue_rdd_upload",
            },
        ],
    }


def build_mm_optional_upload_offer_decorations() -> dict[str, Any]:
    """Suggestions-only decorations for the Yes/No optional-upload offer."""
    return {"suggestions": build_mm_optional_upload_offer_suggestions()}


def build_create_sr_attachment_decorations(state: dict[str, Any]) -> dict[str, Any]:
    """Optional MM attachment card during CREATE_SR / SR_CREATED after user accepts the offer.

    Shown only when ``mm_optional_upload_status == awaiting_upload``.
    Does not block confirmation — attachments remain optional.
    """
    stage: str = state.get("workflow_stage") or "CREATE_SR"
    if stage not in ("CREATE_SR", "SR_CREATED"):
        return {}

    collected: dict[str, Any] = dict(state.get("collected_data") or {})
    if collected.get("lease_id") in (None, "", 0):
        return {}
    if collected.get(MM_OPTIONAL_UPLOAD_STATUS_KEY) != MM_OPTIONAL_AWAITING_UPLOAD:
        return {}

    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    session_id: str | None = state.get("session_id")
    documents: list[dict[str, Any]] = list(state.get("documents") or [])
    uploaded = _uploaded_type_ids(documents)

    doc_rows = [
        {
            "key": "SR_HANDOVER_OTHER",
            "label": _DOC_LABELS.get("SR_HANDOVER_OTHER", "Other"),
            "required": False,
            "status": "uploaded" if "SR_HANDOVER_OTHER" in uploaded else "missing",
        }
    ]

    decorations: dict[str, Any] = {
        "document_requirement": {
            "documents": doc_rows,
            "uploadContext": {
                "session_id": session_id or "",
                "sr_id": backend_refs.get("sr_id") or "",
            },
            "message": _MM_OPTIONAL_UPLOAD_MESSAGE,
        },
        "suggestions": build_mm_optional_upload_continue_suggestions(),
    }
    return decorations


def build_mm_optional_upload_waiting_response(
    state: dict[str, Any],
    *,
    collected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """WAITING_FOR_USER payload for offer or upload-wait (CREATE_SR)."""
    data = dict(collected if collected is not None else (state.get("collected_data") or {}))
    status = data.get(MM_OPTIONAL_UPLOAD_STATUS_KEY)
    base_state = {**dict(state), "collected_data": data}

    if status == MM_OPTIONAL_AWAITING_CHOICE:
        return {
            "collected_data": data,
            "status": "WAITING_FOR_USER",
            "response_message": _MM_OPTIONAL_OFFER_MESSAGE,
            "response_ui": {
                "type": "message",
                "message": _MM_OPTIONAL_OFFER_MESSAGE,
            },
            "response_decorations": merge_decorations(
                state.get("response_decorations"),
                build_mm_optional_upload_offer_decorations(),
            ),
        }

    if status == MM_OPTIONAL_AWAITING_UPLOAD:
        return {
            "collected_data": data,
            "status": "WAITING_FOR_USER",
            "response_message": _MM_OPTIONAL_UPLOAD_MESSAGE,
            "response_ui": {
                "type": "message",
                "message": _MM_OPTIONAL_UPLOAD_MESSAGE,
            },
            "response_decorations": merge_decorations(
                state.get("response_decorations"),
                build_create_sr_attachment_decorations(base_state),
            ),
        }

    return {"collected_data": data}


def merge_decorations(
    existing: dict[str, Any] | None,
    new: dict[str, Any],
) -> dict[str, Any]:
    """Shallow merge decoration dicts (later keys win)."""
    merged = dict(existing or {})
    merged.update(new)
    return merged
