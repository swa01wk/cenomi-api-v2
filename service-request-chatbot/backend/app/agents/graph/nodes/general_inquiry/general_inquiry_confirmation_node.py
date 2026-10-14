"""Human confirmation before General Inquiry SR submission.

Responsibilities
----------------
- Check that all required fields for CREATE_SR are present in ``collected_data``.
- If complete: build a deterministic confirmation card and set PENDING.
- If incomplete: return empty so the missing-field node can handle collection.

Non-responsibilities
--------------------
- Does not call the LLM.
- Does not submit or build the API payload.
- Does not modify ``collected_data``.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.general_inquiry_schema import (
    OPTIONAL_FIELDS,
    apply_lease_title_and_description,
    get_missing_fields,
)
from app.agents.services.display_formatters import format_field_display_value
from app.observability.decorators import trace_node

logger = logging.getLogger(__name__)

_FIELD_LABELS: dict[str, str] = {
    "lease_code": "Lease Code",
    "brand": "Brand",
    "mall": "Mall",
    "unit_codes": "Unit Codes",
    "city": "City",
    "tenant_contact": "Tenant Contact",
    "title": "Title",
    "description": "Description",
    "notes": "Notes",
}

_DISPLAY_FIELDS: tuple[str, ...] = (
    "lease_code",
    "brand",
    "mall",
    "unit_codes",
    "city",
    "tenant_contact",
    "title",
    "description",
    "notes",
)

_EDITABLE_FIELDS: frozenset[str] = frozenset(
    {"title", "description", "notes"}
)

# Sent on draft_preview every turn so the form boxes fill as each answer
# lands. The confirmation card used to be the first payload that carried
# these, which left Title and Description empty until the review step.
_FORM_SYNC_FIELDS: tuple[tuple[str, str], ...] = (
    ("lease_id", "Lease"),
    *((key, _FIELD_LABELS[key]) for key in _DISPLAY_FIELDS),
)

_CONFIRMATION_MESSAGE = (
    "All required details have been collected. Please review your General Inquiry "
    "below and confirm to submit, or let me know if you'd like to change anything."
)

# Optional attachment. Not a required field — the user can upload or skip.
# Stored on collected_data so it survives the next turn, the same way the
# handover optional-upload choice does.
GI_OPTIONAL_UPLOAD_KEY = "gi_optional_upload_status"
GI_UPLOAD_AWAITING_CHOICE = "awaiting_choice"
GI_UPLOAD_AWAITING_UPLOAD = "awaiting_upload"
GI_UPLOAD_DONE = "done"
GI_DOCUMENT_TYPE = "SR_GEN_INQ"

_UPLOAD_OFFER_MESSAGE = (
    "Would you like to attach a document to this inquiry? "
    "It is optional — you can upload one or skip."
)
_UPLOAD_CARD_MESSAGE = (
    "Upload a document below, or tap Skip to continue without one."
)

# After the lease fills the title and description, the user accepts them or
# writes both. Stored on collected_data so the choice survives the next turn.
GI_TITLE_REVIEW_KEY = "gi_title_review_status"
GI_TITLE_REVIEW_PENDING = "pending"
GI_TITLE_REVIEW_ACCEPTED = "accepted"
GI_TITLE_REVIEW_CUSTOM = "custom"
GI_TITLE_JUST_ASKED_KEY = "gi_title_just_asked"
# title → description → done. Done is the document upload, and a later form
# sync must not ask for the description again.
GI_CUSTOM_STEP_KEY = "gi_custom_step"

_TITLE_YES = frozenset({"yes", "y", "yeah", "yep", "yup"})
_TITLE_NO = frozenset({"no", "n", "nope"})

_TITLE_QUESTION = "What is the subject of your inquiry? Please provide a short title."
_DESCRIPTION_QUESTION = "Please describe your inquiry in detail."

_ACCEPT_UPLOAD_ACTIONS = frozenset({"accept_optional_upload"})
_SKIP_UPLOAD_ACTIONS = frozenset({"skip_optional_upload", "continue_optional_upload"})


def general_inquiry_draft_fields(collected: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows the form bridge writes while the guide is still collecting."""
    rows: list[dict[str, Any]] = []
    for key, label in _FORM_SYNC_FIELDS:
        val = collected.get(key)
        if val is None or val == "" or val == []:
            continue
        if isinstance(val, list):
            text = ", ".join(str(item) for item in val if item not in (None, ""))
            if not text:
                continue
        else:
            text = str(val)
        rows.append({"key": key, "label": label, "value": text})
    return rows


def _uploaded_inquiry_row(documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Id and file name for an already uploaded SR_GEN_INQ, if there is one."""
    for doc in documents:
        doc_type = str(doc.get("document_type_id") or doc.get("document_type") or "")
        if doc_type != GI_DOCUMENT_TYPE:
            continue
        document_id = str(doc.get("document_id") or "").strip()
        if not document_id:
            continue
        file_name = (
            doc.get("file_name")
            or doc.get("fileName")
            or doc.get("filename")
            or ""
        )
        row: dict[str, Any] = {"document_id": document_id}
        if file_name:
            row["file_name"] = file_name
        return row
    return {}


def build_gi_title_review(collected: dict[str, Any]) -> dict[str, Any]:
    """Yes keeps the generated title and description. No lets the user write both."""
    title = str(collected.get("title") or "").strip()
    description = str(collected.get("description") or "").strip()
    message = (
        "I added this title and description from the lease you selected.\n\n"
        f"Title: {title}\n"
        f"Description: {description}\n\n"
        "Is this okay?"
    )
    data = {
        **collected,
        GI_TITLE_REVIEW_KEY: GI_TITLE_REVIEW_PENDING,
        GI_TITLE_JUST_ASKED_KEY: False,
    }
    return {
        "collected_data": data,
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": {
            "type": "message",
            "verbatim": True,
            "message": message,
            "suggestions": {
                "kind": "labeled_options",
                "options": [
                    {"label": "Yes", "value": "yes"},
                    {"label": "No", "value": "no"},
                ],
            },
        },
        "missing_fields": [],
        "active_field": None,
    }


def _ask_custom_title_or_description(collected: dict[str, Any]) -> dict[str, Any]:
    """The next blank field after the user chose to write their own."""
    if not str(collected.get("title") or "").strip():
        field_name = "title"
        message = _TITLE_QUESTION
    else:
        field_name = "description"
        message = _DESCRIPTION_QUESTION
    data = {**collected, GI_TITLE_JUST_ASKED_KEY: True}
    return {
        "collected_data": data,
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": {
            "type": "text_question",
            "verbatim": True,
            "field": field_name,
            "message": message,
        },
        "missing_fields": [field_name],
        "active_field": field_name,
    }


def _quiet_hold(collected: dict[str, Any]) -> dict[str, Any]:
    """A form sync with nothing new. Do not ask again and do not show a new card."""
    data = {**collected, GI_TITLE_JUST_ASKED_KEY: False}
    return {
        "collected_data": data,
        "status": "WAITING_FOR_USER",
        "response_message": " ",
        "response_ui": {"type": "message", "verbatim": True, "message": " "},
        "missing_fields": [],
        "active_field": None,
    }


def handle_gi_title_review(
    state: dict[str, Any],
    *,
    user_message: str,
    action: str = "",
) -> dict[str, Any] | None:
    """Yes / No on the generated title. None when that step is not on screen."""
    if (state.get("workflow_stage") or "CREATE_SR") != "CREATE_SR":
        return None
    collected = dict(state.get("collected_data") or {})
    review = str(collected.get(GI_TITLE_REVIEW_KEY) or "")
    text = user_message.strip()
    if review == GI_TITLE_REVIEW_CUSTOM:
        # Skip and Upload are button actions with no typed message. A blank
        # message here is only a form sync; those actions must still reach
        # the document step.
        if action in _ACCEPT_UPLOAD_ACTIONS or action in _SKIP_UPLOAD_ACTIONS:
            return None
        step = str(collected.get(GI_CUSTOM_STEP_KEY) or "title")
        upload_status = str(collected.get(GI_OPTIONAL_UPLOAD_KEY) or "")
        # Title, then description, then the document. A blank form sync stays
        # on the question still being asked, and once both are written it
        # must not ask for the description again.
        if not text:
            if step == "done" or upload_status in (
                GI_UPLOAD_AWAITING_UPLOAD,
                GI_UPLOAD_DONE,
            ):
                return _quiet_hold(collected)
            return _ask_custom_title_or_description(collected)
        if step == "title":
            collected["title"] = text
            collected["description"] = ""
            collected[GI_CUSTOM_STEP_KEY] = "description"
            return _ask_custom_title_or_description(collected)
        if step == "description":
            collected["description"] = text
            collected[GI_CUSTOM_STEP_KEY] = "done"
            collected[GI_TITLE_JUST_ASKED_KEY] = False
            return build_gi_upload_card(state, collected)
        return None
    if review != GI_TITLE_REVIEW_PENDING:
        return None

    message = text.lower()
    if message in _TITLE_YES:
        collected[GI_TITLE_REVIEW_KEY] = GI_TITLE_REVIEW_ACCEPTED
        collected[GI_TITLE_JUST_ASKED_KEY] = False
        return build_gi_upload_card(state, collected)
    if message in _TITLE_NO:
        collected[GI_TITLE_REVIEW_KEY] = GI_TITLE_REVIEW_CUSTOM
        collected[GI_CUSTOM_STEP_KEY] = "title"
        collected["title"] = ""
        collected["description"] = ""
        return _ask_custom_title_or_description(collected)
    return build_gi_title_review(collected)


def build_gi_upload_offer(collected: dict[str, Any]) -> dict[str, Any]:
    """Upload / Skip chips. The document card comes only after Upload."""
    data = {**collected, GI_OPTIONAL_UPLOAD_KEY: GI_UPLOAD_AWAITING_CHOICE}
    return {
        "collected_data": data,
        "status": "WAITING_FOR_USER",
        "response_message": _UPLOAD_OFFER_MESSAGE,
        "response_ui": {
            "type": "message",
            "verbatim": True,
            "message": _UPLOAD_OFFER_MESSAGE,
            "suggestions": {
                "kind": "labeled_options",
                "options": [
                    {
                        "label": "Upload",
                        "value": "upload",
                        "action": "accept_optional_upload",
                    },
                    {
                        "label": "Skip",
                        "value": "skip",
                        "action": "skip_optional_upload",
                    },
                ],
            },
        },
        "missing_fields": [],
        "active_field": None,
    }


def build_gi_upload_card(
    state: dict[str, Any],
    collected: dict[str, Any],
) -> dict[str, Any]:
    """Optional SR_GEN_INQ card, with Skip so the inquiry can still be submitted."""
    documents = list(state.get("documents") or [])
    uploaded = _uploaded_inquiry_row(documents)
    data = {**collected, GI_OPTIONAL_UPLOAD_KEY: GI_UPLOAD_AWAITING_UPLOAD}
    backend_refs = state.get("backend_refs") or {}
    return {
        "collected_data": data,
        "status": "WAITING_FOR_USER",
        "response_message": _UPLOAD_CARD_MESSAGE,
        "response_ui": {
            "type": "document_requirement",
            "verbatim": True,
            "message": _UPLOAD_CARD_MESSAGE,
            "documents": [
                {
                    "key": GI_DOCUMENT_TYPE,
                    "label": "General Inquiry",
                    "required": False,
                    "status": "uploaded" if uploaded else "missing",
                    **uploaded,
                }
            ],
            "uploadContext": {
                "session_id": str(state.get("session_id") or ""),
                "sr_id": str(backend_refs.get("sr_id") or ""),
                "allowedDocumentTypes": [GI_DOCUMENT_TYPE],
                "defaultDocumentType": GI_DOCUMENT_TYPE,
            },
            "suggestions": {
                "kind": "labeled_options",
                "options": [
                    {
                        "label": "Skip",
                        "value": "skip",
                        "action": "skip_optional_upload",
                    },
                ],
            },
        },
        "missing_fields": [],
        "active_field": None,
    }


def gi_upload_waiting(
    state: dict[str, Any],
    collected: dict[str, Any],
) -> dict[str, Any]:
    """Re-show whichever upload step is on screen."""
    status = str(collected.get(GI_OPTIONAL_UPLOAD_KEY) or "")
    if status == GI_UPLOAD_AWAITING_UPLOAD:
        return build_gi_upload_card(state, collected)
    return build_gi_upload_offer(collected)


def handle_gi_optional_upload(
    state: dict[str, Any],
    *,
    action: str,
    user_message: str,
) -> dict[str, Any] | None:
    """CREATE_SR upload / skip. None when this turn is not that step."""
    if (state.get("workflow_stage") or "CREATE_SR") != "CREATE_SR":
        return None
    if state.get("confirmation_status") == "PENDING":
        return None
    collected = dict(state.get("collected_data") or {})
    status = str(collected.get(GI_OPTIONAL_UPLOAD_KEY) or "")
    if status not in (GI_UPLOAD_AWAITING_CHOICE, GI_UPLOAD_AWAITING_UPLOAD):
        return None

    message = user_message.strip().lower()
    accepts = action in _ACCEPT_UPLOAD_ACTIONS or (
        status == GI_UPLOAD_AWAITING_CHOICE and message in {"upload", "yes", "y"}
    )
    skips = action in _SKIP_UPLOAD_ACTIONS or message in {
        "skip",
        "no",
        "continue",
        "n",
    }
    # Saving the file does not send Skip. The card refresh is a blank turn
    # after the draft already has SR_GEN_INQ, so treat that as the upload
    # being finished and continue to the review card.
    uploaded = status == GI_UPLOAD_AWAITING_UPLOAD and bool(
        _uploaded_inquiry_row(list(state.get("documents") or []))
    )
    if accepts:
        collected[GI_OPTIONAL_UPLOAD_KEY] = GI_UPLOAD_AWAITING_UPLOAD
        return build_gi_upload_card(state, collected)
    if skips or uploaded:
        collected[GI_OPTIONAL_UPLOAD_KEY] = GI_UPLOAD_DONE
        return {
            "collected_data": collected,
            "status": "IN_PROGRESS",
            "response_message": None,
            "response_ui": {},
            "active_field": None,
        }
    return gi_upload_waiting(state, collected)


def _build_confirmation_card(
    collected_data: dict[str, Any],
) -> dict[str, Any]:
    """Build the confirmation card payload for the frontend."""
    fields: list[dict[str, Any]] = []
    for field_key in _DISPLAY_FIELDS:
        raw_value = collected_data.get(field_key)
        if raw_value is None and field_key in OPTIONAL_FIELDS:
            continue
        if field_key == "tenant_contact" and not str(raw_value or "").strip():
            continue
        is_editable = field_key in _EDITABLE_FIELDS
        display_value = (
            raw_value if is_editable else format_field_display_value(field_key, raw_value)
        )
        fields.append(
            {
                "key": field_key,
                "label": _FIELD_LABELS.get(field_key, field_key),
                "value": display_value,
                "editable": is_editable,
            }
        )

    return {
        "type": "confirmation_card",
        "requestType": "General Inquiry",
        "stage": "CREATE_SR",
        "fields": fields,
        "message": _CONFIRMATION_MESSAGE,
        # Tells the frontend which action_override to send when the user
        # clicks "Confirm & Submit".  Without this the frontend sends
        # action_override=undefined and general_inquiry_entry_node never
        # sets confirmation_status=CONFIRMED — the flow loops forever.
        "confirmAction": "confirm_submit",
    }


@trace_node("general_inquiry_confirmation", "AGENT")
async def general_inquiry_confirmation_node(state: ServiceRequestState) -> dict[str, Any]:
    """Generate a confirmation card when all required fields are present.

    No-op when:
    - Required fields are still missing.
    - ``confirmation_status`` is already ``CONFIRMED`` (user confirmed this turn).
    - ``confirmation_status`` is ``REJECTED`` with no new corrections.
    """
    existing_status: str | None = state.get("confirmation_status")
    if existing_status == "CONFIRMED":
        return {}
    if existing_status == "REJECTED":
        extracted = state.get("extracted_fields") or {}
        corrected = state.get("corrected_fields") or {}
        if not extracted and not corrected:
            return {}

    collected_data: dict[str, Any] = apply_lease_title_and_description(
        dict(state.get("collected_data") or {})
    )
    workflow_stage: str = state.get("workflow_stage") or "CREATE_SR"

    missing = get_missing_fields(workflow_stage, collected_data)
    non_optional_missing = [f for f in missing if f not in OPTIONAL_FIELDS]

    if non_optional_missing:
        logger.info(
            "general_inquiry_confirmation: skipped — %d required field(s) still missing: %s",
            len(non_optional_missing),
            non_optional_missing,
        )
        return {}

    # Lease fills the title and description. The user accepts them, or writes
    # both, and only then sees the document upload.
    if (
        workflow_stage == "CREATE_SR"
        and str(collected_data.get(GI_OPTIONAL_UPLOAD_KEY) or "") != GI_UPLOAD_DONE
    ):
        review = str(collected_data.get(GI_TITLE_REVIEW_KEY) or "")
        if review not in (GI_TITLE_REVIEW_ACCEPTED, GI_TITLE_REVIEW_CUSTOM):
            logger.info("general_inquiry_confirmation: title and description check")
            return build_gi_title_review(collected_data)
        if review == GI_TITLE_REVIEW_CUSTOM and (
            not str(collected_data.get("title") or "").strip()
            or not str(collected_data.get("description") or "").strip()
        ):
            return {}
        logger.info("general_inquiry_confirmation: document upload")
        return build_gi_upload_card(state, collected_data)

    logger.info(
        "general_inquiry_confirmation: all required fields present — generating card"
    )

    confirmation_card = _build_confirmation_card(collected_data)

    return {
        "confirmation_required": True,
        "confirmation_status": "PENDING",
        "status": "READY_TO_SUBMIT",
        "response_ui": confirmation_card,
        "response_message": None,
        "missing_fields": [],
        "active_field": None,
    }
