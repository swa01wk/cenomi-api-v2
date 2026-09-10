"""Human confirmation before submission.

Responsibilities
----------------
* Check that all required fields for the current workflow stage are present
  in ``collected_data``.
* If complete: build a deterministic, stage-specific confirmation card and
  transition the graph into the READY_TO_SUBMIT / PENDING confirmation state.
* If incomplete: return empty so the missing-field node can handle collection.

Non-responsibilities
--------------------
* Does not call the LLM.
* Does not submit or build the API payload.
* Does not modify ``collected_data``.
* Does not process the user's yes/no response (that is the supervisor's job).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.handover_schema import OPTIONAL_FIELDS, get_missing_fields
from app.agents.services.display_formatters import (
    format_enum_label,
    format_field_display_value,
)
from app.agents.services.review_decorations_service import (
    _uploaded_doc_row,
    build_document_upload_ask_response,
    build_mm_optional_upload_continue_suggestions,
    is_fm_review_ready,
    MM_OPTIONAL_AWAITING_UPLOAD,
    MM_OPTIONAL_DONE,
    MM_OPTIONAL_UPLOAD_STATUS_KEY,
)
from app.observability.decorators import trace_node

logger = logging.getLogger(__name__)

# ── Display configuration — CREATE_SR ─────────────────────────────────────────

_FIELD_LABELS: dict[str, str] = {
    # CREATE_SR
    "lease_code": "Lease Code",
    "brand": "Brand",
    "mall": "Mall",
    "unit_codes": "Unit Codes",
    "city": "City",
    "contracted_area": "Contracted Area (sqm)",
    "title": "Title",
    "description": "Description",
    "startDate": "Inspection Start Date",
    "inspectionTime": "Inspection Start Time",
    "endDate": "Inspection End Date",
    "inspectionEndTime": "Inspection End Time",
    "inspection_done_by": "Inspection Done By",
    "notes": "Notes",
    "comments": "Comments",
    # FM_REVIEW
    "unit_readiness_date": "Unit Readiness Date",
    "expected_handover_date": "Expected Handover Date",
    # RDD_REVIEW
    "guideLineLink": "Guidelines Link",
    "document_status": "Document Status",
    "actual_handover_date": "Actual Handover Date",
    "fitout_start_date": "Fitout Start Date",
    "fitout_end_date": "Fitout End Date",
    "trading_date": "Trading Start Date (Auto)",
    # CREATE_HANDED_OVER_SR (Path B — Already Trading)
    "already_handovered": "Handover Type",
    "actual_shop_handover_date": "Actual Shop Handover Date",
    "actual_fitout_start_date": "Actual Fitout Start Date",
    "actual_fitout_end_date": "Actual Fitout End Date",
    "actual_trading_date": "Actual Trading Date",
}

# Fields the user may inline-edit on the CREATE_SR card.
# lease_code is editable so users can correct a wrong lease; merge clears
# resolved IDs and lease_lookup revalidates the new code.
_CREATE_SR_EDITABLE: frozenset[str] = frozenset(
    {
        "lease_code",
        "title",
        "description",
        "startDate",
        "inspectionTime",
        "endDate",
        "inspectionEndTime",
        "inspection_done_by",
        "notes",
        "comments",
    }
)

# Fields the user may inline-edit on the FM_REVIEW card.
_FM_REVIEW_EDITABLE: frozenset[str] = frozenset({"unit_readiness_date", "comments"})

# Fields the user may inline-edit on the RDD_REVIEW card.
# trading_date is intentionally excluded — it is auto-computed (read-only display).
# document_status renders as a <select> on the frontend (options emitted separately).
_RDD_REVIEW_EDITABLE: frozenset[str] = frozenset(
    {"guideLineLink", "document_status", "actual_handover_date",
     "fitout_start_date", "fitout_end_date", "comments"}
)

# Ordered display fields per stage (backend-only IDs excluded).
_CREATE_SR_DISPLAY_FIELDS: tuple[str, ...] = (
    "lease_code",
    "brand",
    "mall",
    "unit_codes",
    "city",
    "contracted_area",
    "title",
    "description",
    "startDate",
    "endDate",
    "inspection_done_by",
    "notes",
    "comments",
)

_FM_REVIEW_DISPLAY_FIELDS: tuple[str, ...] = (
    "lease_code",
    "brand",
    "mall",
    "unit_readiness_date",
    "expected_handover_date",
    "comments",
)

# Path B records what already happened, and the review card is where a
# mistyped date gets fixed — so every date the user supplied is editable here,
# as they are on the Path A card.
_HANDED_OVER_EDITABLE: frozenset[str] = frozenset(
    {
        "title",
        "actual_shop_handover_date",
        "actual_fitout_start_date",
        "actual_fitout_end_date",
        "actual_trading_date",
        "comments",
    }
)

_HANDED_OVER_DISPLAY_FIELDS: tuple[str, ...] = (
    "lease_code",
    "brand",
    "mall",
    "unit_codes",
    "city",
    "contracted_area",
    "already_handovered",
    "title",
    "actual_shop_handover_date",
    "actual_fitout_start_date",
    "actual_fitout_end_date",
    "actual_trading_date",
    "comments",
)

_RDD_REVIEW_DISPLAY_FIELDS: tuple[str, ...] = (
    "lease_code",
    "brand",
    "mall",
    "guideLineLink",
    "document_status",
    "actual_handover_date",
    "fitout_start_date",
    "fitout_end_date",
    "trading_date",    # read-only — auto-computed value shown here
    "comments",
)

# Backward-compat alias — imported by existing unit tests and external consumers.
_CONFIRMATION_DISPLAY_FIELDS: tuple[str, ...] = _CREATE_SR_DISPLAY_FIELDS

_STAGE_DISPLAY_CONFIG: dict[str, tuple[tuple[str, ...], frozenset[str], str]] = {
    "CREATE_SR": (
        _CREATE_SR_DISPLAY_FIELDS,
        _CREATE_SR_EDITABLE,
        "All required details have been collected. Please review and confirm the "
        "Handover Service Request, or let me know if you want to change anything.",
    ),
    "FM_REVIEW": (
        _FM_REVIEW_DISPLAY_FIELDS,
        _FM_REVIEW_EDITABLE,
        "FM review details are ready. Please confirm the readiness dates and "
        "ensure required documents are uploaded before proceeding.",
    ),
    "RDD_REVIEW": (
        _RDD_REVIEW_DISPLAY_FIELDS,
        _RDD_REVIEW_EDITABLE,
        "RDD review details are ready. Please confirm the dates, document status, "
        "guidelines link, and ensure the handover report is uploaded. "
        "Trading start date has been auto-calculated as fitout end + 8 days.",
    ),
    "CREATE_HANDED_OVER_SR": (
        _HANDED_OVER_DISPLAY_FIELDS,
        _HANDED_OVER_EDITABLE,
        "All required details have been collected for the Already Trading handover. "
        "Please review and confirm the Handed Over Service Request, or let me know "
        "if you want to change anything. "
        "You can also attach optional supporting documents (paperclip icon) before "
        "confirming — any files you upload will be submitted with the request.",
    ),
}

_REQUEST_TYPE_LABELS: dict[str, str] = {
    "CREATE_SR": "Handover Service Request",
    "FM_REVIEW": "FM Review — Save / Approve",
    "RDD_REVIEW": "RDD Report Submission",
    "CREATE_HANDED_OVER_SR": "Handed Over Service Request",
}

# ── Internal helpers ───────────────────────────────────────────────────────────


# Maps (workflow_stage, fm_action/rdd_action) → the confirmAction string the
# frontend must send back so the entry node sets the action AND confirms in
# one turn (avoids the multi-turn persistence problem).
_CONFIRM_ACTION_MAP: dict[tuple[str, str], str] = {
    ("FM_REVIEW", "save_progress"): "confirm_fm_save",
    ("FM_REVIEW", "approve"):       "confirm_fm_approve",
    ("RDD_REVIEW", "submit"):       "confirm_rdd_submit",
    ("RDD_REVIEW", "final_approve"): "confirm_rdd_final_approve",
}


# Rows the frontend should render with a native picker rather than a text box.
# The value on these rows is the raw machine format: YYYY-MM-DD and HH:MM.
_TYPED_INPUTS: dict[str, str] = {
    "startDate": "date",
    "endDate": "date",
    "inspectionTime": "time",
    "inspectionEndTime": "time",
    "actual_shop_handover_date": "date",
    "actual_fitout_start_date": "date",
    "actual_fitout_end_date": "date",
    "actual_trading_date": "date",
}

# The review cards' own date rows. Each holds a plain calendar date with no
# clock folded into it, so each can take a real picker — without one the row
# was a free-text box seeded with "27/09/2026", leaving the reviewer to retype
# a display string by hand to change a date.
#
# ``startDate`` / ``endDate`` are deliberately absent here: at review stages
# their value is the merged "06/09/2026 9:00 AM" display string, which a native
# date input cannot hold and would silently render blank.
_REVIEW_TYPED_INPUTS: dict[str, str] = {
    "unit_readiness_date": "date",
    "expected_handover_date": "date",
    "actual_handover_date": "date",
    "fitout_start_date": "date",
    "fitout_end_date": "date",
}

# Stages whose card is a review of an existing SR rather than a draft.
_REVIEW_CARD_STAGES: frozenset[str] = frozenset({"FM_REVIEW", "RDD_REVIEW"})

# What a native date input will accept, and nothing else.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# What the payload builder books when no clock was ever given. Shown on the
# card so the preview states the time the SR will actually carry.
_DEFAULT_INSPECTION_HHMM = "09:00"

# Clock rows sit directly under the date they belong to.
_INSPECTION_TIME_ROWS: tuple[tuple[str, str], ...] = (
    ("startDate", "inspectionTime"),
    ("endDate", "inspectionEndTime"),
)


def _lease_locked_on_card(workflow_stage: str, collected_data: dict[str, Any]) -> bool:
    """True once the lease is settled and must not be edited on the card.

    Asked in chat, the assistant already refuses: "Lease Code comes from the
    lease record and can't be edited here" (see missing_field_node). An
    editable box on the review card offered exactly what chat refuses, on the
    field every identifier on the request hangs off. Read-only once the lookup
    has resolved a lease_id; to use a different lease, start a new request.
    """
    from app.agents.schemas.handover_schema import AI_FILLED_STAGES, short_flow_enabled

    if not short_flow_enabled() or workflow_stage not in AI_FILLED_STAGES:
        return False
    return bool(collected_data.get("lease_id"))


def _typed_date_inputs(workflow_stage: str) -> bool:
    """True when the card's date rows should carry native pickers.

    Both create stages: Path A's inspection window and Path B's four actual
    dates are all fixed on this card, and a picker round-trips the raw value
    instead of asking the user to retype a display string.
    """
    from app.agents.schemas.handover_schema import AI_FILLED_STAGES, short_flow_enabled

    return short_flow_enabled() and workflow_stage in AI_FILLED_STAGES


def _picker_value(input_type: str, value: Any) -> Any | None:
    """*value* in the shape the native input speaks, or None to skip the picker.

    A date input accepts YYYY-MM-DD and nothing else — hand it anything else
    and it renders an empty ``dd/mm/yyyy`` with no complaint. The platform
    stores these dates as display strings ("28/09/2026"), so a reopened SR's
    card showed three blank pickers where its dates should have been.

    Returning None leaves the row as text, which is the safe failure: a value
    the picker cannot hold is still a value the reviewer must be able to see.
    """
    if value in (None, ""):
        # Nothing to bind; an empty picker is the correct empty state.
        return value
    if input_type != "date":
        return value
    from app.agents.graph.nodes.shared.merge_state_node import _to_iso_date

    iso = _to_iso_date(value)
    return iso if _ISO_DATE_RE.match(str(iso)) else None


def _typed_input_map(workflow_stage: str) -> dict[str, str]:
    """Which rows this stage's card renders with a native picker."""
    if workflow_stage in _REVIEW_CARD_STAGES:
        return _REVIEW_TYPED_INPUTS
    if _typed_date_inputs(workflow_stage):
        return _TYPED_INPUTS
    return {}


def _typed_inspection_inputs(workflow_stage: str) -> bool:
    """True when the card splits the inspection window into date and time rows.

    Path A only — Path B's dates carry no clock.
    """
    return _typed_date_inputs(workflow_stage) and workflow_stage == "CREATE_SR"


def _with_inspection_time_rows(display_keys: list[str]) -> list[str]:
    """Insert each clock row immediately after its date row."""
    keys = list(display_keys)
    for date_key, time_key in _INSPECTION_TIME_ROWS:
        if date_key in keys and time_key not in keys:
            keys.insert(keys.index(date_key) + 1, time_key)
    return keys


# Provenance of short-flow generated values, written by merge_state_node.
_AI_FIELD_META_KEY = "_ai_field_meta"

# What each generated source means to the person reading the card.
_PROVENANCE_HINTS: dict[str, str] = {
    "template": "Filled in for you — edit if you want different wording.",
    "ai": "Drafted by the assistant from your answers — please check it.",
}

# Stages that show who raised the request and who reviews it next.
_ACCOUNTABILITY_STAGES: frozenset[str] = frozenset(
    {"CREATE_SR", "CREATE_HANDED_OVER_SR"}
)

_ROLE_LABELS: dict[str, str] = {
    "MALL_MANAGER": "Mall Manager",
    "FM_MANAGER": "FM Manager",
    "OPERATIONS": "Operations",
    "DD_ENGINEER": "RDD Engineer",
    "ADMIN": "Admin",
}


def _provenance_for(
    field_key: str,
    raw_value: Any,
    ai_meta: dict[str, Any],
) -> dict[str, Any]:
    """Label a row as generated — unless the user has since changed it.

    The meta records the exact text that was generated, so an edit (typed in
    chat or on this card) is detected by comparison rather than by trusting a
    flag that nothing clears.
    """
    entry = ai_meta.get(field_key)
    if not isinstance(entry, dict):
        return {}
    if str(entry.get("value") or "") != str(raw_value or ""):
        # The user replaced it — their words, their row.
        return {"source": "user"}
    source = str(entry.get("source") or "template")
    row: dict[str, Any] = {"generated": True, "source": source}
    hint = _PROVENANCE_HINTS.get(source)
    if hint:
        row["hint"] = hint
    # Only a genuinely uncertain value earns a number; a deterministic template
    # is correct by construction and a percentage next to it means nothing.
    confidence = entry.get("confidence")
    if source != "template" and isinstance(confidence, (int, float)):
        row["confidence"] = float(confidence)
    return row


def _accountability_rows(
    workflow_stage: str,
    collected_data: dict[str, Any],
    backend_refs: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Read-only 'raised by' / 'reviewed next by' rows for the CREATE stages.

    Roles, not names: the chat backend authenticates a subject id and its roles,
    and inventing a display name it has never been told would be worse than
    naming the role accurately.
    """
    from app.agents.schemas.handover_schema import short_flow_enabled

    # Part of the short flow's review step. With the flag off the card keeps
    # exactly the rows it has always had.
    if not short_flow_enabled() or workflow_stage not in _ACCOUNTABILITY_STAGES:
        return []

    rows: list[dict[str, Any]] = []
    creator_role = str((backend_refs or {}).get("user_role") or "").strip().upper()
    if creator_role:
        rows.append(
            {
                "key": "created_by_role",
                "label": "Raised By",
                "value": _ROLE_LABELS.get(creator_role, format_enum_label(creator_role)),
                "editable": False,
            }
        )

    checker = str(collected_data.get("inspection_done_by") or "").strip().upper()
    if checker:
        rows.append(
            {
                "key": "checker_role",
                "label": "Reviewed Next By",
                "value": _ROLE_LABELS.get(checker, format_enum_label(checker)),
                "editable": False,
            }
        )
    return rows


def _build_confirmation_card(
    collected_data: dict[str, Any],
    workflow_stage: str,
    backend_refs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, stage-specific confirmation card from *collected_data*.

    The card is purely derived from the input — identical inputs always
    produce an identical card, making it safe to regenerate on retry.

    Parameters
    ----------
    collected_data:
        The current draft field values.
    workflow_stage:
        Determines which fields and labels are shown on the card.
    backend_refs:
        Optional backend_refs dict; used to embed ``confirmAction`` for
        FM/RDD stages so the frontend sends the correct single-turn confirm.

    Returns
    -------
    dict
        ``response_ui`` payload with ``type = "confirmation_card"``.
    """
    display_fields, editable_fields, message = _STAGE_DISPLAY_CONFIG.get(
        workflow_stage,
        (_CREATE_SR_DISPLAY_FIELDS, _CREATE_SR_EDITABLE, _STAGE_DISPLAY_CONFIG["CREATE_SR"][2]),
    )

    # Lock guideline (and other RDD fields) after report submit / final approve.
    editable = set(editable_fields)
    rdd_status = ((backend_refs or {}).get("rdd_status") or "").upper()
    if workflow_stage == "RDD_REVIEW" and rdd_status in {
        "REPORT_SUBMITTED",
        "APPROVED",
        "APPROVED_WITH_CHANGES",
        "COMPLETED",
    }:
        editable.discard("guideLineLink")

    if _lease_locked_on_card(workflow_stage, collected_data):
        editable.discard("lease_code")

    # Enum fields that must render as a <select> dropdown on the frontend.
    _ENUM_OPTIONS: dict[str, list[str]] = {
        "document_status": [
            "APPROVED",
            "APPROVED_WITH_CHANGES",
            "DISAPPROVED",
        ],
        "inspection_done_by": [
            "FM_MANAGER",
            "OPERATIONS",
        ],
    }

    # Provenance for the fields the short flow generated (see sr_autofill_service).
    ai_meta: dict[str, Any] = dict(collected_data.get(_AI_FIELD_META_KEY) or {})

    display_keys = list(display_fields)
    # Hide document_status on the card unless Meeting Report was uploaded (or
    # a value is already set from a prior turn). Detect via the uploaded doc
    # types on state.documents rather than the removed rdd_upload_doc_type
    # chip field.
    if workflow_stage == "RDD_REVIEW":
        uploaded_types = {
            str(d.get("document_type_id") or d.get("document_type") or "")
            for d in ((backend_refs or {}).get("documents") or [])
            if d.get("document_type_id") or d.get("document_type")
        }
        # `backend_refs.documents` isn't always populated; also inspect the
        # legacy chip field for backward compat during rollout.
        legacy_chip = collected_data.get("rdd_upload_doc_type")
        meeting_uploaded = "DR_SR_HANDOVER_REPORT" in uploaded_types
        if (
            not meeting_uploaded
            and legacy_chip != "DR_SR_HANDOVER_REPORT"
            and not collected_data.get("document_status")
        ):
            display_keys = [k for k in display_keys if k != "document_status"]

        # A disapproved handover records no dates — the RDD form has no fields
        # for them and the flow no longer asks — so the card must not list four
        # empty rows the reviewer cannot fill. Mirrors
        # RDD_REJECTED_UNASKED_FIELDS, with trading_date alongside them since
        # it is only ever derived from the fit-out end.
        from app.agents.schemas.handover_schema import (
            RDD_REJECTED_STATUS,
            RDD_REJECTED_UNASKED_FIELDS,
        )

        if (
            str(collected_data.get("document_status") or "").strip().upper()
            == RDD_REJECTED_STATUS
        ):
            hidden = RDD_REJECTED_UNASKED_FIELDS | {"trading_date"}
            display_keys = [k for k in display_keys if k not in hidden]

    # Start and end times are independent. inspectionTime is the start clock;
    # endDateLT keeps an end-specific time when the user gave one.
    from app.agents.services.payload_builder_service import (  # local import avoids circular deps
        _normalize_inspection_datetime,
        resolve_start_end_hhmm,
    )
    start_hhmm, end_hhmm = resolve_start_end_hhmm(collected_data)

    # Short flow: the window is never typed out question-by-question, so the
    # review card is where a wrong date or clock gets fixed. Give each one its
    # own row with a real date / time picker instead of folding the clock into
    # the date's display string, which can only be edited as free text.
    typed_inputs = _typed_input_map(workflow_stage)
    if _typed_inspection_inputs(workflow_stage):
        display_keys = _with_inspection_time_rows(display_keys)
        collected_data = {
            **collected_data,
            "inspectionTime": start_hhmm or _DEFAULT_INSPECTION_HHMM,
            "inspectionEndTime": end_hhmm or start_hhmm or _DEFAULT_INSPECTION_HHMM,
        }
        _date_lt_overrides: dict[str, Any] = {}
    else:
        _date_lt_overrides = {}
        for _date_key, _lt_key, _hhmm in (
            ("startDate", "startDateLT", start_hhmm),
            ("endDate", "endDateLT", end_hhmm),
        ):
            _raw = collected_data.get(_date_key)
            if _raw and _hhmm:
                _, _lt = _normalize_inspection_datetime(
                    _raw, collected_data.get(_lt_key), inspection_time=_hhmm
                )
                if _lt:
                    _date_lt_overrides[_date_key] = _lt

    fields: list[dict[str, Any]] = []
    for field_key in display_keys:
        raw_value = _date_lt_overrides.get(field_key, collected_data.get(field_key))
        is_editable = field_key in editable
        # Keep raw ISO / enum codes for editable inputs (selects + round-trip).
        # Format only for read-only display rows.
        display_value = (
            raw_value
            if is_editable
            else format_field_display_value(field_key, raw_value)
        )
        # `already_handovered` is humanized only here (review card). Do NOT
        # humanize in `format_field_display_value`, or the FE bridge will
        # write the label into Formik and the HANDOVER↔HANDED_OVER swap
        # observer (which matches literal codes) will silently break.
        if field_key == "already_handovered" and not is_editable:
            display_value = format_enum_label(display_value)
        row: dict[str, Any] = {
            "key": field_key,
            "label": _FIELD_LABELS.get(field_key, field_key),
            "value": display_value,
            "editable": is_editable,
        }
        if field_key in _ENUM_OPTIONS:
            row["options"] = _ENUM_OPTIONS[field_key]
        if is_editable and field_key in typed_inputs:
            # Tells the frontend to render a native date / time picker and to
            # round-trip the raw value instead of its display text.
            _typed = typed_inputs[field_key]
            _bound = _picker_value(_typed, display_value)
            if _bound is not None:
                row["inputType"] = _typed
                row["value"] = _bound
        row.update(_provenance_for(field_key, raw_value, ai_meta))
        fields.append(row)

    fields.extend(_accountability_rows(workflow_stage, collected_data, backend_refs))

    card: dict[str, Any] = {
        "type": "confirmation_card",
        "requestType": _REQUEST_TYPE_LABELS.get(workflow_stage, "Handover Service Request"),
        "stage": workflow_stage,
        "fields": fields,
        "message": message,
    }

    # Embed the stage-specific confirmAction so the frontend knows which action
    # string to send back instead of the generic "confirm".
    # The primary "Confirm & Submit" button on the review card always means
    # APPROVE/SUBMIT — Save Progress is a separate explicit chip. Always emit
    # the approve/submit confirmAction here so a stale ``fm_action = "save_progress"``
    # left over from a prior session cannot downgrade the primary button into
    # a Save call. If the user actually wants Save Progress, they click the
    # dedicated Save Progress chip which sets the action explicitly upstream.
    stage_action: str = ""
    if workflow_stage == "FM_REVIEW":
        stage_action = "approve"
    elif workflow_stage == "RDD_REVIEW":
        stage_action = (backend_refs or {}).get("rdd_action") or "submit"
    confirm_action = _CONFIRM_ACTION_MAP.get((workflow_stage, stage_action))
    if confirm_action:
        card["confirmAction"] = confirm_action

    return card


# ── Node ───────────────────────────────────────────────────────────────────────


@trace_node("confirmation", "AGENT")
async def confirmation_node(state: ServiceRequestState) -> dict[str, Any]:
    """Generate a stage-specific confirmation card when all required fields are present.

    The node is a no-op (returns ``{}``) in three situations:
    - Required fields are still missing (missing_field_node handles collection).
    - ``confirmation_status`` is already ``"CONFIRMED"`` — the user has already
      confirmed on this turn (set by the entry node); overwriting with
      ``"PENDING"`` would undo the confirmation and block submission.
    - ``confirmation_status`` is ``"REJECTED"`` with no new corrections — the
      graph routes to response_generation to ask what to change.

    Reads from state
    ----------------
    ``collected_data``       — draft field values to confirm.
    ``workflow_stage``       — determines which required fields and display
                               fields apply (defaults to ``"CREATE_SR"``).
    ``confirmation_status``  — existing status; CONFIRMED/REJECTED are preserved.

    Writes to state
    ---------------
    ``confirmation_required``  — ``True`` when all required fields are present.
    ``confirmation_status``    — ``"PENDING"`` (awaiting explicit user response).
    ``status``                 — ``"READY_TO_SUBMIT"``.
    ``response_ui``            — stage-specific confirmation card for the frontend.
    """
    existing_status: str | None = state.get("confirmation_status")
    if existing_status == "CONFIRMED":
        return {}
    if existing_status == "REJECTED":
        extracted = state.get("extracted_fields") or {}
        corrected = state.get("corrected_fields") or {}
        if not extracted and not corrected:
            return {}

    collected_data: dict[str, Any] = state.get("collected_data") or {}
    workflow_stage: str = state.get("workflow_stage") or "CREATE_SR"
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})

    missing = get_missing_fields(workflow_stage, collected_data)
    # Only block on non-optional missing fields.  Optional fields (notes, comments,
    # inspectionTime, description) may remain None after being asked once — the user
    # chose not to provide them.  Mirroring fitout_confirm_confirmation_node behaviour.
    non_optional_missing = [f for f in missing if f not in OPTIONAL_FIELDS]

    if non_optional_missing:
        logger.info(
            "confirmation_node: skipped — %d required field(s) still missing for stage=%s: %s",
            len(non_optional_missing),
            workflow_stage,
            non_optional_missing,
        )
        return {}

    # FM: never auto-open the confirmation card after field collection.
    # CREATE-stage docs (e.g. SR_HANDOVER_OTHER) can satisfy readiness, which
    # previously skipped the upload UI. Show document_requirement + action chips
    # until the user explicitly chooses Save Progress or Approve Review.
    if workflow_stage == "FM_REVIEW":
        documents = list(state.get("documents") or [])
        fm_action_raw = backend_refs.get("fm_action")
        if not fm_action_raw:
            if not is_fm_review_ready(collected_data, documents):
                logger.info(
                    "confirmation_node: FM fields complete — showing upload/review turn "
                    "(no fm_action yet, docs not ready)"
                )
                return build_document_upload_ask_response(state)
            # Ready, but the reviewer may have more to attach. Hold on the
            # upload card — which now carries a Continue chip — until they say
            # they are done. The first document used to make the review
            # "ready" and drop them straight onto the confirmation card while
            # they were still choosing the second, with no way back.
            from app.agents.services.review_decorations_service import (
                FM_UPLOAD_GATE_DONE,
                FM_UPLOAD_GATE_KEY,
            )

            if collected_data.get(FM_UPLOAD_GATE_KEY) != FM_UPLOAD_GATE_DONE:
                logger.info(
                    "confirmation_node: FM docs ready — holding on the upload "
                    "card until the reviewer continues"
                )
                return build_document_upload_ask_response(state)
            # All conditions met; proceed to show the confirmation card.
            # _build_confirmation_card defaults confirmAction to confirm_fm_approve
            # so the next turn sets fm_action=approve via the single-turn confirm path.
            logger.info(
                "confirmation_node: FM fields + docs ready, no fm_action — "
                "showing confirmation card (confirm_fm_approve will set it)"
            )
        else:
            fm_action = str(fm_action_raw).lower()
            if fm_action == "approve" and not is_fm_review_ready(collected_data, documents):
                logger.info(
                    "confirmation_node: FM approve blocked — missing documents; asking for upload"
                )
                return build_document_upload_ask_response(state)

    # Pre-submission optional upload gate — Path A CREATE_SR and Path B
    # CREATE_HANDED_OVER_SR both offer the MM one supporting-document
    # upload (SR_HANDOVER_OTHER) before the review card. Emits a
    # document_requirement UI with a single row + a Continue chip. Upload
    # writes into collected_data.documents[] which the payload builder
    # picks up. Continue / "continue" / "skip" flips mm_optional_upload_status
    # to done and the next turn falls through to the confirmation card.
    if (
        workflow_stage in ("CREATE_SR", "CREATE_HANDED_OVER_SR")
        and collected_data.get(MM_OPTIONAL_UPLOAD_STATUS_KEY) != MM_OPTIONAL_DONE
    ):
        documents = list(state.get("documents") or [])
        uploaded_ids = {
            str(d.get("document_type_id") or d.get("document_type") or "")
            for d in documents
            if d.get("document_type_id") or d.get("document_type")
        }
        already_uploaded = "SR_HANDOVER_OTHER" in uploaded_ids
        session_id_val = str(state.get("session_id") or "")
        sr_id_val = str(backend_refs.get("sr_id") or "")
        is_path_b = workflow_stage == "CREATE_HANDED_OVER_SR"
        gate_message = (
            "You can attach an optional supporting document (Other) for "
            + ("this Already Trading handover" if is_path_b else "this handover request")
            + ", or tap Continue to review and submit without uploading."
        )
        gate_label = "Other (Already Trading)" if is_path_b else "Other"
        doc_requirement_ui: dict[str, Any] = {
            "type": "document_requirement",
            "message": gate_message,
            "documents": [
                {
                    "key": "SR_HANDOVER_OTHER",
                    "label": gate_label,
                    "required": False,
                    "status": "uploaded" if already_uploaded else "missing",
                    # Without the id the card cannot delete what it shows, and
                    # without the name it renders "Uploaded  Other" — the tick
                    # and file name vanish the moment this card is rebuilt over
                    # the one the client filled in locally after the upload.
                    **_uploaded_doc_row(documents, "SR_HANDOVER_OTHER"),
                }
            ],
            "uploadContext": {
                "session_id": session_id_val,
                "sr_id": sr_id_val,
                # Only SR_HANDOVER_OTHER is accepted for the MM pre-submission
                # supporting doc. The FE uploader pre-selects this type and
                # locks the dropdown.
                "allowedDocumentTypes": ["SR_HANDOVER_OTHER"],
                "defaultDocumentType": "SR_HANDOVER_OTHER",
            },
        }
        collected_with_status = dict(collected_data)
        collected_with_status[MM_OPTIONAL_UPLOAD_STATUS_KEY] = MM_OPTIONAL_AWAITING_UPLOAD
        return {
            "status": "WAITING_FOR_USER",
            "collected_data": collected_with_status,
            "response_ui": doc_requirement_ui,
            "response_decorations": {
                "suggestions": build_mm_optional_upload_continue_suggestions(),
            },
            "response_message": None,
            "missing_fields": [],
            "active_field": None,
        }

    logger.info(
        "confirmation_node: all required fields present for stage=%s — generating confirmation card",
        workflow_stage,
    )

    confirmation_card = _build_confirmation_card(collected_data, workflow_stage, backend_refs)

    result: dict[str, Any] = {
        "confirmation_required": True,
        "confirmation_status": "PENDING",
        "status": "READY_TO_SUBMIT",
        "response_ui": confirmation_card,
        "response_message": None,
        # Clear missing_fields so response_generation does not generate a
        # "I still need X" message alongside the confirmation card.
        "missing_fields": [],
        "active_field": None,
    }

    return result
