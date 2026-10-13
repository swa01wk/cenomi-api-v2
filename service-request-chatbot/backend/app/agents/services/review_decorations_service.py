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
from app.agents.schemas.work_permit_schema import WP_REVIEW_ROLES, WP_REVIEW_STAGES
from app.agents.services.permission_service import ROLE_PERMISSION_MAP
from app.agents.services.resource_picker_decorations import build_handover_inbox_picker
from app.types.chat import AuthContext

# Primary graph roles checked for stage entry guards and decoration targeting.
_GRAPH_ROLES: tuple[str, ...] = (
    "FM_MANAGER",
    "OPERATIONS",
    "OPERATION_EXECUTIVES",
    "DD_ENGINEER",
    "MALL_MANAGER",
    "ADMIN",
)

_DOC_LABELS: dict[str, str] = {
    "SR_HANDOVER_CHECKLIST": "Handover Checklist",
    "SR_HANDOVER_SITE_SURVEY": "Site Survey",
    "SR_COP_CHECKLIST_OTHER": "Other",
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

# Roles allowed to ACT inside each review stage (upload documents, take the
# stage actions). Single source of truth for both the upload card and the
# action chips, and it must stay identical to the entry-node role guards
# (``_FM_ALLOWED_ROLES`` / ``_RDD_ALLOWED_ROLES``): anything we surface here
# that those guards reject becomes a dead end — the user clicks and is told
# they do not have permission. ADMIN is deliberately absent for that reason;
# the entry nodes deny it today.
_STAGE_ACTOR_ROLES: dict[str, frozenset[str]] = {
    "FM_REVIEW": frozenset({"FM_MANAGER", "OPERATIONS"}),
    "RDD_REVIEW": frozenset({"DD_ENGINEER"}),
    # Work Permit review hops — same roles as WP_REVIEW_ROLES (approve hop gate).
    **{stage: frozenset(roles) for stage, roles in WP_REVIEW_ROLES.items()},
}


def can_act_in_stage(stage: str, user_role: str | None) -> bool:
    """True when ``user_role`` may upload/act in ``stage``.

    An unknown stage or an unresolved role is permissive — same as the entry
    node guards, which only deny when a role is actually present.
    """
    allowed = _STAGE_ACTOR_ROLES.get(stage)
    if not allowed or not user_role:
        return True
    return user_role.upper() in allowed


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
        # The inspection clocks. The platform stores the times only in the LT
        # display strings — ``inspectionTime``/``inspectionEndTime`` come back
        # null — so without these an SR reopened for review arrives with no
        # clocks at all. On a same-day window that was a dead end: the form
        # showed "09/09/2026 7:27 PM" while the assistant insisted both times
        # were "not set" and refused to move on.
        #
        # The LT strings are the source, never the UTC ``startDate``: the same
        # 9:00 AM is stored as 03:30Z on one SR and 06:00Z on another, so
        # re-deriving the clock from UTC would show one time and submit a
        # different one. merge_state_node reads the clock straight off these.
        "startDateLT", "endDateLT", "inspectionTime", "inspectionEndTime",
        "unit_readiness_date", "expected_handover_date", "actual_handover_date",
        "fitout_start_date", "fitout_end_date", "trading_date", "comments",
        "notes",
        "guideLineLink", "document_status",
        # ── Path B (Already Trading / HANDED_OVER) ─────────────────────────────
        # payload_builder_service.build_handed_over_payload sends all four of
        # these; without them here an opened Path B SR loses the shop handover
        # and fitout end dates from its preview card, and ``already_handovered``
        # — the discriminator HandoverAgenticBridge reads to switch between the
        # HANDOVER and HANDED_OVER form skeletons — never reaches the frontend.
        # ``actual_fitout_start_date`` and ``actual_trading_date`` are already
        # mapped below as part of the Fitout Confirmation block.
        "already_handovered",
        "actual_shop_handover_date", "actual_fitout_end_date",
        "lease_code", "brand", "mall", "city", "unit_codes", "contracted_area",
        "lease_id", "brand_id", "property_id", "tenant_profile_id", "contract_id",
        # ── Fitout Confirmation workflow fields ────────────────────────────────
        "lease_brand_mall",
        "fitout_start_done", "actual_fitout_start_date",
        "planned_fitout_start_date", "planned_fitout_end_date", "planned_trading_date",
        "fitout_complete_done", "actual_fitout_complete_date", "planned_fitout_complete_date",
        "trading_done", "actual_trading_date",
        "delay_reason",
        # Base milestone dates from the lease contract — needed so that
        # missing_field_node can show "original date" on APPROVE_SR.
        "base_fitout_start_date", "base_fitout_end_date", "base_trading_date",
        # ── Fitout Progress workflow fields ────────────────────────────────────
        # The percentage the request was raised with, and the note beside it
        # (singular ``comment`` here — the confirmations use ``comments``).
        # Without these an approver opened their own SR and was told it had no
        # percentage recorded, with no "Approve 65%" to tap.
        "fitout_progress_percentage", "comment",
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
    # Recover the inspection clocks from the LT strings. merge_state_node only
    # syncs them on CREATE_SR, so a reopened SR would otherwise carry the LT
    # display but no ``inspectionTime``/``inspectionEndTime`` — which is what
    # left the reviewer stuck on "the end time (not set) has to be later than
    # the start time (not set)" while the form showed both.
    from app.agents.services.payload_builder_service import (
        extract_hhmm_from_datetime,
    )

    for lt_key, time_key in (
        ("startDateLT", "inspectionTime"),
        ("endDateLT", "inspectionEndTime"),
    ):
        if mapped.get(time_key):
            continue
        hhmm = extract_hhmm_from_datetime(str(mapped.get(lt_key) or ""))
        if hhmm:
            mapped[time_key] = hhmm

    # An SR that reaches us without a handover type still has one — its
    # sub-category says which. 509 of the 578 handover SRs on the platform
    # carry no ``already_handovered`` (all raised through the form, the newest
    # in July), and opening one of those left the type blank on the review card
    # and the discriminator missing for the bridge, which reads it to choose
    # between the HANDOVER and HANDED_OVER form skeletons — so an Already
    # Trading SR could open against the wrong skeleton entirely.
    #
    # Derived only when the payload is silent: a value the platform sent is
    # always kept, even one that disagrees with the sub-category, because that
    # is the SR's own record of what it is.
    if not str(mapped.get("already_handovered") or "").strip():
        derived = _handover_type_for_sub_category(payload.get("sub_category"))
        if derived:
            mapped["already_handovered"] = derived

    # Work Permit inner payload — copy portal form keys into collected_data.
    from app.agents.schemas.work_permit_schema import (
        VALID_WORK_PERMIT_TYPES,
        ddmmyyyy_to_iso,
    )

    for wp_key in (
        "contractor_name",
        "phone",
        "work_type",
        "work_location",
        "comments",
        "notes",
        "department",
        "unit_type",
        "owner",
        "trade_name",
        "responsible_person",
        "type_work",
        "list_equipment",
        "work_info",
        "demand_appendices",
        "special_conditions",
        "description",
        "title",
        "entry_gate",
        "permission_given_to",
    ):
        if wp_key in payload and payload[wp_key] not in (None, "") and not mapped.get(wp_key):
            mapped[wp_key] = payload[wp_key]
    for nested_key in (
        "planneddate",
        "workers",
        "measureofsafety",
        "typeofwork",
        "effective_starting",
        "firstperiod",
        "secondperiod",
    ):
        if nested_key in payload and payload[nested_key] not in (None, "", []) and not mapped.get(
            nested_key
        ):
            mapped[nested_key] = payload[nested_key]
    planned = payload.get("planneddate")
    if isinstance(planned, dict):
        if planned.get("worktimedate") and not mapped.get("start_date"):
            mapped["start_date"] = ddmmyyyy_to_iso(planned.get("worktimedate"))
        if planned.get("completionworkdate") and not mapped.get("end_date"):
            mapped["end_date"] = ddmmyyyy_to_iso(planned.get("completionworkdate"))
    sub = payload.get("sub_category") or payload.get("work_permit_type")
    if sub in VALID_WORK_PERMIT_TYPES:
        mapped["work_permit_type"] = sub
        mapped["sub_category"] = sub
    return mapped


# The sub-category is the SR's own statement of which handover path it is.
_SUB_CATEGORY_HANDOVER_TYPE: dict[str, str] = {
    "HANDOVER": "initiate_handover",
    "HANDED_OVER": "already_trading",
}


def _handover_type_for_sub_category(sub_category: Any) -> str | None:
    """``already_handovered`` implied by *sub_category*, or None if it implies none."""
    return _SUB_CATEGORY_HANDOVER_TYPE.get(
        str(sub_category or "").strip().upper()
    )


def build_inbox_decorations(auth: AuthContext | None) -> dict[str, Any]:
    """Decorations when a reviewer opens chat without an active SR."""
    if not is_handover_reviewer(auth):
        return {}
    return build_handover_inbox_picker()


def _uploaded_doc_row(
    documents: list[dict[str, Any]], doc_type: str
) -> dict[str, Any]:
    """The identifying half of a document row: its id and file name, if uploaded.

    A row without ``document_id`` cannot be deleted. The card reads
    ``documentId ?? document_id``, finds neither, and falls to its local-only
    branch: the row clears on screen, no DELETE is ever sent, and the next turn
    re-renders the document from state — it "comes back after a second". Zero
    DELETE requests reached the backend across a whole session of trying.
    """
    for doc in documents:
        dt = str(doc.get("document_type_id") or doc.get("document_type") or "")
        if dt != doc_type:
            continue
        document_id = str(doc.get("document_id") or "").strip()
        if not document_id:
            continue
        row: dict[str, Any] = {"document_id": document_id}
        # Three spellings reach us: the platform upload response says
        # ``filename``, the draft stores ``file_name``, and rows patched from
        # the client carry ``fileName``. Reading only two of them sent the card
        # out with an id and no name — and the client, seeing a named row go
        # nameless, overwrote the name it already had, so the tick and the file
        # name vanished from the chat card and the form at once.
        file_name = (
            doc.get("file_name") or doc.get("fileName") or doc.get("filename")
        )
        if file_name:
            row["file_name"] = file_name
        return row
    return {}


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


# The Handover Meeting Report and the Rejected Fitout and Handover Report are
# alternatives: the handover is either reported or rejected, never both.
_RDD_REPORT_ALTERNATIVES: dict[str, str] = {
    RDD_DOC_TYPE_MEETING: RDD_DOC_TYPE_REJECTED,
    RDD_DOC_TYPE_REJECTED: RDD_DOC_TYPE_MEETING,
}


def _rdd_upload_types(uploaded: set[str]) -> list[str]:
    """The RDD document types the upload card offers, given what is uploaded.

    Once one report of the either-or pair is on the SR, the other leaves the
    card — its row and its entry in the type dropdown alike, since the chat's
    dropdown is built from the rows still missing. It used to stay offered, so
    a reviewer who had filed the Meeting Report could still attach a Rejected
    report beside it. Deleting the uploaded report brings both back, because
    this is recomputed from the documents every turn. A pair that somehow holds
    both keeps both: a row with a file on it is never hidden.
    """
    types = [RDD_DOC_TYPE_MEETING, RDD_DOC_TYPE_REJECTED, RDD_DOC_TYPE_OTHER]
    for report, alternative in _RDD_REPORT_ALTERNATIVES.items():
        if report in uploaded and alternative not in uploaded:
            types.remove(alternative)
    return types


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
        relevant = _rdd_upload_types(uploaded)

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
            **_uploaded_doc_row(documents, doc_type),
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
        upload_context["allowedDocumentTypes"] = list(relevant)

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


# Terminal / post-approval RDD statuses — no stage actions left to take, so the
# card falls back to the read-only "View Service Request" chip.
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
    documents: list[dict[str, Any]],  # unused: kept so every call site reads the same
) -> bool:
    """True when FM can approve: the unit readiness date is set.

    None of the three FM documents is mandatory — the upload card already
    marks every row ``required: False`` — so approval must not wait on one.
    It used to, which stranded a reviewer with nothing to attach: the Approve
    Review chip never appeared and the turn kept repeating "Please upload at
    least one FM document" with no way past it.
    """
    return collected.get("unit_readiness_date") not in (None, "")


def is_fm_review_ready(
    collected: dict[str, Any],
    documents: list[dict[str, Any]],
) -> bool:
    """Public alias for FM approve readiness (unit readiness date present)."""
    return _fm_review_ready(collected, documents)


# The FM fields Save Progress actually PATCHes. Documents are deliberately not
# here: an upload has already reached the platform by the time it shows up in
# ``documents``, so it is never something still waiting to be saved.
_FM_SAVEABLE_FIELDS: tuple[str, ...] = ("unit_readiness_date", "comments", "notes")


def _saved_value(value: Any) -> str:
    """A session value and its platform twin, comparable without format noise.

    The same day arrives as ``2026-08-10`` from the session and
    ``2026-08-10T00:00:00Z`` from the platform GET.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) >= 10 and "T" in text and text[:10].count("-") == 2:
        return text[:10]
    return text


def fm_has_unsaved_changes(state: dict[str, Any]) -> bool:
    """True when FM is holding an edit the platform has not stored yet.

    Save Progress used to sit on every FM turn, including ones where the
    reviewer had changed nothing, so tapping it PATCHed the SR back to the
    values it already held. ``sr_status_sync_node`` refreshes
    ``backend_refs["create_payload"]`` from the platform GET on every turn with
    an ``sr_id``, which makes that snapshot the saved baseline to compare the
    session's answers against.

    Fails open: with no snapshot to compare against, keep the chip rather than
    hide a save the reviewer might genuinely need.
    """
    refs = state.get("backend_refs") or {}
    snapshot = (refs.get("create_payload") or {}).get("payload")
    if not isinstance(snapshot, dict) or not snapshot:
        return True
    saved = payload_to_collected_data(snapshot)
    collected = state.get("collected_data") or {}
    return any(
        _saved_value(collected.get(name)) != _saved_value(saved.get(name))
        for name in _FM_SAVEABLE_FIELDS
    )


_FM_UPLOAD_ASK_MESSAGE = (
    "Please share the unit readiness date to continue with the review. "
    "All FM documents are optional — attach the Handover Checklist, Site "
    "Survey, or Other below if you have them."
)
_FM_UPLOAD_REVIEW_MESSAGE = (
    "All FM documents are optional — upload the Handover Checklist, Site "
    "Survey, or Other if you have them, then tap Continue to review and "
    "choose an action."
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
    # Once FM has what it needs, the card stays open with a Continue chip so
    # the reviewer can add the Site Survey or an Other document before moving
    # on. The stage action chips (Save Progress / Approve Review) would
    # otherwise be the only way off this turn, and neither says "I am done
    # uploading".
    if stage == "FM_REVIEW":
        decorations = merge_decorations(
            decorations, {"suggestions": build_fm_upload_continue_suggestions()}
        )
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


def build_view_sr_suggestions(sr_id: str) -> dict[str, Any]:
    """Read-only chip that takes the user to the SR page on the platform.

    Shown when the SR is real but the viewer has no action left in this stage —
    a Mall Manager on an SR that has moved to FM review, or anyone looking at an
    already-approved SR. ``value`` carries the SR id so the frontend can deep
    link without a second lookup.
    """
    return {
        "kind": "labeled_options",
        "options": [
            {
                "label": "View Service Request",
                "value": str(sr_id),
                "action": "view_service_request",
            },
        ],
    }


def _action_suggestions(
    *,
    stage: str,
    user_role: str | None,
    rdd_status: str | None,
    collected: dict[str, Any] | None = None,
    documents: list[dict[str, Any]] | None = None,
    fm_dirty: bool = True,
) -> dict[str, Any] | None:
    """Return stage-specific action chips. Never mix FM and RDD actions.

    FM shows exactly one chip:
    - Approve Review when ready (advances toward RDD)
    - Save Progress while incomplete AND holding an unsaved edit
    - Continue otherwise — Save Progress on a turn with nothing to save was a
      button that re-sent the values the SR already held

    RDD Submit Report is shown only when ``_rdd_ready_to_submit`` — never while
    required data or the Meeting Report is still outstanding.
    """
    options: list[dict[str, str]] = []
    collected = collected or {}
    documents = documents or []
    # Normalised the same way the entry-node guards do, so chip visibility and
    # the guard that accepts the chip's action never disagree on casing.
    role = (user_role or "").upper()

    if stage == "FM_REVIEW" and role in _STAGE_ACTOR_ROLES["FM_REVIEW"]:
        if _fm_review_ready(collected, documents):
            options = [
                {
                    "label": "Approve Review",
                    "value": "approve_fm_review",
                    "action": "approve_fm_review",
                },
            ]
        elif fm_dirty:
            options = [dict(_SAVE_FM_PROGRESS_OPTION)]
        else:
            options = [dict(_CONTINUE_FM_UPLOAD_OPTION)]
    elif stage == "RDD_REVIEW" and role in _STAGE_ACTOR_ROLES["RDD_REVIEW"]:
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

    # Work Permit review: no FM/RDD upload chips — only View SR for non-actors.
    # No promote_preview: the full details card duplicates the form on the left.
    if stage in WP_REVIEW_STAGES:
        auth: AuthContext | None = state.get("auth_context")  # type: ignore[assignment]
        user_role = backend_refs.get("user_role") or derive_user_role(auth)
        decorations: dict[str, Any] = {}
        if not can_act_in_stage(stage, user_role):
            decorations["suggestions"] = build_view_sr_suggestions(sr_id)
        return decorations

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

    decorations = {"promote_preview": True}

    suggestions = _action_suggestions(
        stage=stage,
        user_role=user_role,
        rdd_status=rdd_status,
        collected=collected,
        documents=documents,
        fm_dirty=fm_has_unsaved_changes(state) if stage == "FM_REVIEW" else True,
    )
    if suggestions:
        decorations["suggestions"] = suggestions
    elif not can_act_in_stage(stage, user_role) or (
        stage == "RDD_REVIEW" and (rdd_status or "").upper() in _RDD_TERMINAL_STATUSES
    ):
        # Nothing actionable for this viewer — offer the SR page instead of a
        # card with no way forward.
        decorations["suggestions"] = build_view_sr_suggestions(sr_id)

    # After report submit, the mandatory RDD doc is already on the platform —
    # don't re-prompt uploads while offering Final Approve.
    # For RDD, show the doc upload card as soon as guideLineLink is captured;
    # dates come AFTER upload (their values are nested on the uploaded row).
    # No longer gated on the removed rdd_upload_doc_type chip.
    show_docs = not (stage == "RDD_REVIEW" and (rdd_status or "").upper() == "REPORT_SUBMITTED")
    if not can_act_in_stage(stage, user_role):
        # Read-only viewer for this stage: the entry node will refuse their
        # actions, so an upload/delete card would contradict that refusal.
        show_docs = False
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
# FM review keeps its upload card open until the reviewer says they are done,
# the way the MM pre-submission gate does. Without it the first document made
# the review "ready" and the flow jumped to the confirmation card while the
# reviewer was still picking the second — with no way back to the card.
FM_UPLOAD_GATE_KEY = "fm_upload_gate"
FM_UPLOAD_GATE_DONE = "done"

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


_SAVE_FM_PROGRESS_OPTION: dict[str, str] = {
    "label": "Save Progress",
    "value": "save_fm_progress",
    "action": "save_fm_progress",
}

_CONTINUE_FM_UPLOAD_OPTION: dict[str, str] = {
    "label": "Continue",
    "value": "continue",
    "action": "continue_fm_upload",
}


def build_fm_upload_continue_suggestions() -> dict[str, Any]:
    """The single chip while the FM upload card is open: Continue.

    Save Progress does not belong here. The card's own Save button already
    commits each document, and Continue carries the readiness date forward to
    the review — so a second Save chip beside them saved nothing the reviewer
    had not already saved, and read as a third, competing way off the card.
    """
    return {"kind": "labeled_options", "options": [dict(_CONTINUE_FM_UPLOAD_OPTION)]}


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
            **_uploaded_doc_row(documents, "SR_HANDOVER_OTHER"),
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
