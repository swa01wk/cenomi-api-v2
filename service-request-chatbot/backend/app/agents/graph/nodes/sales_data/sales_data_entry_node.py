"""Sales Data entry node — detects intent and routes to the correct sub-flow.

Responsibilities
----------------
- First node reached when active_agent == "sales_data_agent".
- Sets workflow_stage if not already set.
- Routes based on action_override or intent:
    * CSV file uploaded → CSV upload flow
    * ENTER_SALES_DATA_MANUAL → manual entry flow
    * CHECK_SALES_DATA_STATUS → status lookup flow
    * REVIEW_SALES_DATA / APPROVE_SALES_DATA → admin flow
    * CONFIRM_CSV_SUBMISSION → confirmation from table UI

Non-responsibilities
--------------------
- Must NOT run LangGraph or validate CSV.
- Must NOT call the backend API.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.graph.nodes.sales_data.period_utils import (
    asked_period_from_state,
    parse_asked_period,
)
from app.agents.graph.nodes.sales_data.manual_entry_parser import parse_manual_entry
from app.agents.graph.nodes.sales_data.pending_months import extract_lease_code
from app.agents.services.permission_service import graph_permission_guard
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)

# Actions sent by the frontend UI
_CSV_UPLOAD_ACTIONS = frozenset({"csv_uploaded", "upload_csv"})
_CONFIRM_ACTIONS = frozenset({"CONFIRM_CSV_SUBMISSION", "confirm_csv_submission"})
# cancel_supporting_doc must NOT be here — it would wipe lease/status and
# drop the PDF attach bar. It is handled as a doc-flow resume instead.
_CANCEL_ACTIONS = frozenset({"cancel", "cancel_sales_data"})
_DOC_CANCEL_ACTIONS = frozenset({"cancel_supporting_doc"})
_DOC_UPLOAD_ACTIONS = frozenset({"doc_uploaded", "upload_supporting_doc"})
# A pending-month chip click: the user picked one lease+month, so go straight
# to asking for that month's amount rather than making them type the lease
# and month they just clicked.
_PICK_MONTH_ACTIONS = frozenset({"pick_pending_month"})
_CONFIRM_DOC_ACTIONS = frozenset({"confirm_supporting_doc", "confirm_doc"})
_DOC_STAGES = frozenset({
    "DOC_SELECT",
    "AWAITING_DOC_UPLOAD",
    "DOC_UPLOADING",
    "AWAITING_DOC_CONFIRM",
    "DOC_SUBMITTING",
})
_DOC_KEYWORDS = (
    "pdf",
    "supporting document",
    "supporting doc",
    "extra document",
    "extra doc",
    "audited sales",
    "need_reupload",
    "need re-upload",
    "need reupload",
    "re-upload a pdf",
    "reupload a pdf",
    "re-upload pdf",
    "upload a pdf",
)
_UPLOAD_SWITCH_PHRASES = (
    "upload",
    "csv",
    "attach",
    "i want to submit",
    "submit it",
    "submit sales",
    "enter sales",
    "type the amount",
    "type the details",
)
_GATE_ENTRY_STAGES = frozenset({
    None,
    "",
    "STATUS_LOOKUP",
    "SALES_DATA_COMPLETE",
})
_PENDING_GATE_STAGES = frozenset({
    "PENDING_MONTHS_CHECK",
    "AWAITING_PENDING_MONTHS_CHOICE",
    "AWAITING_OTHER_MONTHS_REMINDER",
})
_AMOUNT_RE = re.compile(
    # Bare numbers need 5+ digits (not 4) so a plain year mention ("August
    # 2026") doesn't get misread as a typed sales amount.
    # "[\d,]+\s*sar\b" (no \b before "sar") separately covers digits glued
    # directly to the currency code ("12280SAR") — the trailing \b in the
    # bare-number branch can't match there since a digit-to-letter run has
    # no word boundary in between.
    r"(?:sar\s*[\d,]+|[\d,]+\s*sar\b|amount\s*(?:is|:)?\s*[\d,]+"
    r"|(?:is|:)\s*(?:sar\s*)?[\d,]{3,}"
    r"|(?<![a-zA-Z0-9-])\d{5,}(?:\.\d+)?\b)",
    re.I,
)
_LEASE_CODE_RE = re.compile(
    r"\b(?:lc[-\s]?\d{3,}|t\d{6,}|[a-z]{2,10}(?:[_-][a-z0-9]{2,10}){1,3}"
    r"|(?:lease)\s+[a-z0-9_-]{3,})\b",
    re.I,
)
_EXPLICIT_CSV_PHRASES = ("upload", "csv", "attach")
# "directly", "manually", "type it" answer the CSV-or-type choice with intent
# to type — must win over _EXPLICIT_CSV_PHRASES even though "upload" often
# appears in the same reply ("directly I want to upload", i.e. type it
# directly rather than via file upload).
_MANUAL_CHOICE_PHRASES = (
    "directly",
    "manually",
    "type it",
    "type them",
    "enter it manually",
    "not upload",
    "no file",
    "without a file",
    "without csv",
    "skip the file",
    "skip the csv",
)
_YES_PHRASES = frozenset({
    "yes", "y", "ok", "okay", "proceed", "continue", "confirm", "sure",
})
_NO_PHRASES = frozenset({"no", "n", "cancel", "nope", "stop"})


_CANCELLED_MESSAGE = "Sales data submission cancelled. What would you like to do next?"



# ── Stage-independent commands ────────────────────────────────────────────
# Typed commands that must mean the same thing at every step of the flow.
# Resolved here, before any stage-specific routing, so "reupload" / "cancel" /
# "help" never fall into whichever node happens to own the current stage.
_CANCEL_ONLY_RE = re.compile(
    r"^\s*(no[, ]*)?(please\s+)?(cancel( it| that| this)?|stop|never\s*mind|nevermind|"
    r"forget it|exit|quit|abort)\s*[.!]*\s*$",
    re.I,
)
_RESTART_RE = re.compile(r"\b(start over|start again|restart|from scratch|begin again)\b", re.I)
_HELP_RE = re.compile(
    r"^\s*(help|help me|what can you do|what are my options|options|menu)\s*[?.!]*\s*$",
    re.I,
)
_REUPLOAD_RE = re.compile(
    r"\b(re-?\s?upload\w*|upload\w*\s+(?:it\s+|the\s+file\s+|a\s+file\s+|the\s+csv\s+)?again|"
    r"(?:new|another|different|corrected|fixed|updated)\s+(?:file|csv|sheet)|"
    r"i(?:'ll| will)\s+upload|let me upload|upload\s+(?:a\s+)?new)\b",
    re.I,
)
_EXPLICIT_DOC_RE = re.compile(r"\b(pdf|supporting|audited|document)\b", re.I)
_EXPLICIT_CSV_RE = re.compile(
    r"\b(csv|sales\s+data|sales\s+figures?|template|spreadsheet|excel|sheet)\b", re.I
)
_UPLOAD_WORD_RE = re.compile(r"\bupload\w*\b", re.I)
# Stages whose own node owns a typed "cancel" (keeps the entry / file).
_OWN_CANCEL_STAGES = frozenset({"AWAITING_MANUAL_CONFIRM", "AWAITING_DOC_CONFIRM",
                                "AWAITING_PERIOD_MISMATCH_CONFIRM"})
_START_STAGES = frozenset({None, "", "SALES_DATA_COMPLETE", "STATUS_LOOKUP", "STALE_NOTIFICATION"})

_HELP_MESSAGE_TENANT = (
    "I can help you with your sales data:\n"
    "- Upload a CSV file (you can download the template)\n"
    '- Type your sales figures, for example "t0057323, August 2026, 12,280"\n'
    "- Check the status of your submissions\n"
    "- Upload a file the Sales Data Team has requested\n\n"
    "What would you like to do?"
)
_HELP_MESSAGE_ADMIN = (
    "I can help you review sales data:\n"
    "- Show the submissions awaiting review\n"
    '- Approve submissions, for example "Approve t0106013" or "Approve all"\n'
    "- Request a re-upload, supporting document or audited sales\n\n"
    "What would you like to do?"
)
_REUPLOAD_CSV_MESSAGE = (
    "Please attach the corrected CSV file. You can download the template "
    "if you need it."
)


def _resolve_command(message: str, stage: str | None) -> str | None:
    """"cancel" | "restart" | "help" | "csv_upload" | "doc_upload" | None."""
    text = (message or "").strip()
    if not text:
        return None
    if _HELP_RE.search(text):
        return "help"
    if _RESTART_RE.search(text):
        return "restart"
    if _CANCEL_ONLY_RE.search(text) and stage not in _OWN_CANCEL_STAGES:
        return "cancel"
    if stage == "AWAITING_PERIOD_MISMATCH_CONFIRM":
        return None  # its own yes / no / different-file handling
    wants_upload = bool(_REUPLOAD_RE.search(text) or _UPLOAD_WORD_RE.search(text))
    if not wants_upload:
        return None
    if _EXPLICIT_DOC_RE.search(text):
        return "doc_upload"
    in_doc_flow = stage in _DOC_STAGES
    if in_doc_flow and not _EXPLICIT_CSV_RE.search(text) and not text.lower().startswith("no"):
        return "doc_upload"
    if in_doc_flow or _REUPLOAD_RE.search(text) or stage in _START_STAGES:
        return "csv_upload"
    return None


# The intent each command switches the conversation to.
_COMMAND_INTENTS = {
    "restart": "UPLOAD_SALES_DATA",
    "csv_upload": "UPLOAD_SALES_DATA",
    "doc_upload": "UPLOAD_SUPPORTING_DOCUMENT",
}


def _command_permitted(state: ServiceRequestState, command: str) -> bool:
    """Commands must not switch a user into a flow their role cannot use.

    Mid-conversation turns reach this node through session continuity, which
    skips the supervisor's RBAC gate — so check the same permission table
    here. Admin review steps never switch into tenant upload flows.
    """
    target = _COMMAND_INTENTS.get(command)
    if target is None:
        return True  # help / cancel are safe for every role
    if (state.get("workflow_stage") or "").startswith("ADMIN"):
        return False
    auth = state.get("auth_context")
    if auth is None:
        return True
    from app.agents.schemas.help_agent_schema import intents_for_roles

    permitted = intents_for_roles(
        frozenset(getattr(auth, "roles", None) or ()),
        bool(getattr(auth, "is_global_admin", False)),
    )
    return target in permitted


def _clear_file_state(state: ServiceRequestState) -> tuple[dict[str, Any], dict[str, Any]]:
    refs = dict(state.get("backend_refs") or {})
    for key in ("csv_session_id", "csv_rows", "csv_rows_all", "csv_filename",
                "period_mismatch", "doc_session_id", "action_required_rows",
                "notification_context"):
        refs.pop(key, None)
    collected = {
        k: v for k, v in dict(state.get("collected_data") or {}).items()
        if k in ("_pending_items", "_asked_period")
    }
    return refs, collected


def _command_reply(message: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": {"type": "message", "verbatim": True, "message": message},
        **extra,
    }


def _looks_like_doc_request(message: str) -> bool:
    lowered = (message or "").lower()
    return any(token in lowered for token in _DOC_KEYWORDS)


def _looks_like_upload_request(message: str) -> bool:
    lowered = (message or "").lower()
    return any(token in lowered for token in _UPLOAD_SWITCH_PHRASES)


def _looks_like_typed_amount(message: str) -> bool:
    lowered = (message or "").lower()
    if any(token in lowered for token in ("sar ", " amount", "amount ", "enter sales", "sales figure")):
        return True
    return bool(_AMOUNT_RE.search(message or ""))


def _mentions_lease_code(message: str) -> bool:
    return bool(_LEASE_CODE_RE.search(message or ""))


def _explicit_csv_request(message: str) -> bool:
    lowered = (message or "").lower()
    if any(token in lowered for token in _MANUAL_CHOICE_PHRASES):
        return False
    return any(token in lowered for token in _EXPLICIT_CSV_PHRASES)


def _explicit_manual_choice(message: str) -> bool:
    lowered = (message or "").lower()
    return any(token in lowered for token in _MANUAL_CHOICE_PHRASES)


def _notification_context(state: ServiceRequestState) -> dict[str, Any]:
    corrected = state.get("corrected_fields") or {}
    refs = state.get("backend_refs") or {}
    raw = corrected.get("notification_context") or refs.get("notification_context") or {}
    if not isinstance(raw, dict):
        return {}
    lease = (raw.get("lease_code") or "").strip()
    if not lease:
        return {}
    return {
        "lease_code": lease,
        "period_hint": (raw.get("period_hint") or raw.get("period_label") or "").strip(),
        "source": "notification",
    }


def _upload_prompt_patch(
    state: ServiceRequestState,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    notif = _notification_context(state)
    refs = dict(state.get("backend_refs") or {})
    collected = dict(state.get("collected_data") or {})
    asked = asked_period_from_state({
        **state,
        "backend_refs": refs,
        "collected_data": collected,
    })
    msg_asked = parse_asked_period(state.get("user_message") or "")
    if msg_asked:
        asked = msg_asked
    lease = (
        extract_lease_code(state.get("user_message") or "")
        or str(collected.get("_upload_lease") or "").strip()
        or notif.get("lease_code")
        or ""
    )
    period = (asked or {}).get("label") or notif.get("period_hint") or ""
    if lease:
        where = f" for **{lease}**"
        if period:
            where += f" ({period})"
        message = (
            f"Please upload the sales data file{where}, or type the details — "
            f'for example: "{lease}, August 2026, 85,000".'
        )
    else:
        message = (
            "I can help you submit your sales data. You can upload a CSV file "
            "(download the template if you need one), or type the details — "
            'for example: "LC-1234, August 2026, 85,000".\n\n'
            "Which would you prefer?"
        )
    if asked:
        refs["asked_period"] = asked
        collected["_asked_period"] = asked
    if lease:
        collected["_upload_lease"] = lease
    if notif:
        refs["notification_context"] = notif
    patch: dict[str, Any] = {
        "intent": "UPLOAD_SALES_DATA",
        "workflow_stage": "UPLOAD_INTENT",
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": {
            "type": "sales_data_upload_prompt",
            "show_file_upload": True,
            "show_template_download": True,
            "message": message,
            **({"lease_code": lease} if lease else {}),
            **({"period_label": period} if period else {}),
        },
        "backend_refs": refs,
        "collected_data": collected,
    }
    if extra:
        patch.update(extra)
    return patch


@trace_node("sales_data_entry", "AGENT")
async def sales_data_entry_node(state: ServiceRequestState) -> dict[str, Any]:
    """Route the turn to the correct sales-data sub-flow."""
    # Front door: SALES_AGENT gates the whole agent, the same permission the
    # frontend's "Upload with AI" / "Review with AI" buttons check — a user
    # who free-types "I want to submit sales data" straight into open chat
    # (bypassing those buttons) must be stopped here, not just at the final
    # submit/approve call downstream.
    denied = graph_permission_guard(state, "ENTER_SALES_DATA_AGENT")
    if denied:
        log.info("sales_data_entry.permission_denied")
        return denied

    intent: str = state.get("intent") or ""
    action_override: str | None = state.get("action_override")
    workflow_stage: str | None = state.get("workflow_stage")
    backend_refs: dict = dict(state.get("backend_refs") or {})
    corrected_fields: dict = state.get("corrected_fields") or {}
    hydrated_asked = asked_period_from_state({**state, "backend_refs": backend_refs})
    if hydrated_asked:
        backend_refs["asked_period"] = hydrated_asked
    collected = dict(state.get("collected_data") or {})
    if collected.get("_csv_rows") and not backend_refs.get("csv_rows"):
        backend_refs["csv_rows"] = collected["_csv_rows"]
        backend_refs["csv_filename"] = collected.get("_csv_filename") or backend_refs.get(
            "csv_filename", "upload.csv"
        )

    log.info(
        "sales_data_entry",
        intent=intent,
        action=action_override,
        stage=workflow_stage,
    )

    # ── 00. Stage-independent typed commands ───────────────────────────────
    if not action_override:
        command = _resolve_command(state.get("user_message") or "", workflow_stage)
        if command and not _command_permitted(state, command):
            log.info("sales_data_entry.command_not_permitted", command=command, stage=workflow_stage)
            command = None
        if command:
            log.info("sales_data_entry.command", command=command, stage=workflow_stage)
        if command == "help":
            admin = intent in ("REVIEW_SALES_DATA", "APPROVE_SALES_DATA") or (
                workflow_stage or ""
            ).startswith("ADMIN")
            return _command_reply(_HELP_MESSAGE_ADMIN if admin else _HELP_MESSAGE_TENANT)
        if command == "cancel":
            return _command_reply(
                _CANCELLED_MESSAGE,
                active_agent=None, intent=None, workflow_stage=None,
                collected_data={}, backend_refs={},
            )
        if command in ("restart", "csv_upload"):
            refs, collected = _clear_file_state(state)
            if command == "restart" or workflow_stage in _START_STAGES:
                # Fresh start: show the pending months with the attach control.
                return {
                    "intent": "UPLOAD_SALES_DATA",
                    "workflow_stage": "PENDING_MONTHS_CHECK",
                    "status": "IN_PROGRESS",
                    "backend_refs": refs,
                    "collected_data": collected,
                }
            patch = _upload_prompt_patch(
                {**state, "backend_refs": refs, "collected_data": collected}
            )
            patch["intent"] = "UPLOAD_SALES_DATA"
            patch["response_message"] = _REUPLOAD_CSV_MESSAGE
            if isinstance(patch.get("response_ui"), dict):
                patch["response_ui"] = {
                    **patch["response_ui"], "message": _REUPLOAD_CSV_MESSAGE, "verbatim": True,
                }
            return patch
        if command == "doc_upload" and workflow_stage not in _DOC_STAGES:
            intent = "UPLOAD_SUPPORTING_DOCUMENT"

    # ── 0. Cancel ──────────────────────────────────────────────────────────
    # Supporting-doc cancel resumes the PDF attach step — do not wipe session.
    if action_override in _DOC_CANCEL_ACTIONS or (
        action_override == "cancel" and workflow_stage in _DOC_STAGES
    ):
        log.info("sales_data_entry.cancel_supporting_doc")
        return {
            "workflow_stage": "AWAITING_DOC_CONFIRM",
            "intent": "UPLOAD_SUPPORTING_DOCUMENT",
            "status": "IN_PROGRESS",
        }

    if (
        action_override in _CANCEL_ACTIONS
        and workflow_stage != "AWAITING_PERIOD_MISMATCH_CONFIRM"
    ):
        log.info("sales_data_entry.cancel")
        return {
            "active_agent": None,
            "intent": None,
            "workflow_stage": None,
            "collected_data": {},
            "backend_refs": {},
            "status": "WAITING_FOR_USER",
            "response_message": _CANCELLED_MESSAGE,
            "response_ui": {"type": "message", "verbatim": True, "message": _CANCELLED_MESSAGE},
        }

    # ── 0a2. Approval acknowledgement (informational, terminal) ────────────
    # Opening a SALES_DATA_APPROVED notification must not seed the upload
    # flow — it's a done-deal notice, not an action request.
    if intent == "ACKNOWLEDGE_APPROVAL":
        log.info("sales_data_entry.acknowledge_approval")
        notif = _notification_context(state)
        lease = notif.get("lease_code") or ""
        period = notif.get("period_hint") or ""
        where = f" for **{lease}**" if lease else ""
        if period:
            where += f" ({period})"
        message = (
            f"Your sales data{where} has been approved by the Sales Data Team. "
            "No further action is required."
        )
        return {
            "active_agent": None,
            "intent": None,
            "workflow_stage": None,
            "collected_data": {},
            "backend_refs": {},
            "status": "WAITING_FOR_USER",
            "response_message": message,
            "response_ui": {"type": "message", "verbatim": True, "message": message},
        }

    # ── 0b. CSV month vs the month the tenant asked for ────────────────────
    if (
        workflow_stage == "AWAITING_PERIOD_MISMATCH_CONFIRM"
        and action_override not in _CSV_UPLOAD_ACTIONS
        and not corrected_fields.get("csv_session_id")
    ):
        asked = (backend_refs.get("asked_period") or {}) if isinstance(
            backend_refs.get("asked_period"), dict
        ) else {}
        asked_label = asked.get("label") or "the month you asked for"
        user_message_raw = (state.get("user_message") or "").strip().lower()
        if (
            action_override in ("confirm_period_mismatch", "confirm")
            or user_message_raw in _YES_PHRASES
            or "proceed" in user_message_raw
            or "still want" in user_message_raw
        ):
            log.info("sales_data_entry.period_mismatch_proceed")
            return {
                "workflow_stage": "AWAITING_TABLE_CONFIRM",
                "status": "IN_PROGRESS",
                "backend_refs": backend_refs,
            }
        if (
            action_override in (
                "cancel_period_mismatch",
                "cancel_period",
                "cancel",
                "cancel_sales_data",
            )
            or user_message_raw in _NO_PHRASES
            or "different file" in user_message_raw
        ):
            log.info("sales_data_entry.period_mismatch_decline")
            message = (
                f"Understood. Please upload the **{asked_label}** file when you are ready."
            )
            refs = dict(backend_refs)
            refs.pop("csv_session_id", None)
            refs.pop("csv_rows", None)
            refs.pop("period_mismatch", None)
            collected = dict(state.get("collected_data") or {})
            collected.pop("_csv_rows", None)
            collected.pop("_csv_filename", None)
            return {
                "workflow_stage": "UPLOAD_INTENT",
                "status": "WAITING_FOR_USER",
                "backend_refs": refs,
                "collected_data": collected,
                "response_message": message,
                "response_ui": {
                    "type": "sales_data_upload_prompt",
                    "show_file_upload": True,
                    "show_template_download": True,
                    "message": message,
                },
            }
        mismatch = backend_refs.get("period_mismatch") or {}
        retry_msg = mismatch.get("message") or (
            f"The uploaded file is not **{asked_label}** data. "
            "Do you still want to proceed with this file?"
        )
        return {
            "workflow_stage": "AWAITING_PERIOD_MISMATCH_CONFIRM",
            "status": "WAITING_FOR_USER",
            "response_message": retry_msg,
            "response_ui": {
                "type": "period_mismatch_warning",
                "asked_label": asked_label,
                "file_label": mismatch.get("file_label", ""),
                "includes_asked_month": bool(mismatch.get("includes_asked_month")),
                "show_file_upload": True,
                "verbatim": True,
                "message": retry_msg,
            },
        }

    # ── 1. CONFIRM_CSV_SUBMISSION from review table ────────────────────────
    if action_override in _CONFIRM_ACTIONS or workflow_stage == "AWAITING_TABLE_CONFIRM":
        if action_override in _CONFIRM_ACTIONS:
            log.info("sales_data_entry.confirm_csv_submission")
            return {
                "workflow_stage": "SUBMITTING",
                "confirmation_status": "CONFIRMED",
                "status": "IN_PROGRESS",
            }

    # ── 1b. Confirm typed entry — stay on AWAITING_MANUAL_CONFIRM so the
    #     confirmation node can set SUBMITTING and route to manual submission.
    if action_override in ("confirm_manual_entry", "confirm") and (
        workflow_stage == "AWAITING_MANUAL_CONFIRM" or intent == "ENTER_SALES_DATA_MANUAL"
    ):
        log.info("sales_data_entry.confirm_manual_entry")
        return {
            "workflow_stage": "AWAITING_MANUAL_CONFIRM",
            "status": "IN_PROGRESS",
        }

    # ── 1c. Confirm supporting-doc / NEED_REUPLOAD PDF ─────────────────────
    if action_override in _CONFIRM_DOC_ACTIONS or (
        action_override == "confirm" and workflow_stage == "AWAITING_DOC_CONFIRM"
    ):
        log.info("sales_data_entry.confirm_supporting_doc")
        return {
            "workflow_stage": "AWAITING_DOC_CONFIRM",
            "status": "IN_PROGRESS",
        }

    # ── 2. Status lookup (skip if the user is actually trying to upload) ──
    user_message = (state.get("user_message") or "").lower()
    if (
        (intent == "CHECK_SALES_DATA_STATUS" or action_override == "check_status")
        and not _looks_like_upload_request(user_message)
        and not _looks_like_doc_request(user_message)
    ):
        log.info("sales_data_entry.status_lookup")
        return {
            "workflow_stage": "STATUS_LOOKUP",
            "status": "IN_PROGRESS",
        }

    # ── 3. Admin review ────────────────────────────────────────────────────
    if intent in ("REVIEW_SALES_DATA", "APPROVE_SALES_DATA"):
        log.info("sales_data_entry.admin_review", intent=intent)
        return {
            "workflow_stage": "ADMIN_REVIEW",
            "status": "IN_PROGRESS",
        }

    # ── 3b. Supporting document / NEED_REUPLOAD PDF ────────────────────────
    looks_like_doc = _looks_like_doc_request(user_message)
    has_doc_session = bool(
        backend_refs.get("doc_session_id") or corrected_fields.get("doc_session_id")
    )
    if (
        intent == "UPLOAD_SUPPORTING_DOCUMENT"
        or action_override in _DOC_UPLOAD_ACTIONS
        or workflow_stage in _DOC_STAGES
        or has_doc_session
        or looks_like_doc
    ):
        log.info(
            "sales_data_entry.supporting_doc",
            intent=intent,
            action=action_override,
            stage=workflow_stage,
        )
        if workflow_stage == "AWAITING_DOC_CONFIRM":
            return {"workflow_stage": "AWAITING_DOC_CONFIRM", "status": "IN_PROGRESS"}
        if workflow_stage == "DOC_SUBMITTING":
            return {"workflow_stage": "DOC_SUBMITTING", "status": "IN_PROGRESS"}
        if has_doc_session or action_override in _DOC_UPLOAD_ACTIONS:
            return {"workflow_stage": "DOC_UPLOADING", "status": "IN_PROGRESS"}
        if workflow_stage == "AWAITING_DOC_UPLOAD":
            # NEED_REUPLOAD asks for corrected *sales data*, so a typed
            # figure is a valid answer — the tenant shouldn't have to
            # produce a file just to fix a number. The other two ask-types
            # want evidence, so only a file will do there.
            if (
                collected.get("current_status") == "NEED_REUPLOAD"
                and _looks_like_typed_amount(user_message)
            ):
                parsed = parse_manual_entry(user_message)
                amount = parsed.get("sales_amt")
                if amount is not None:
                    collected["sales_amt"] = amount
                    log.info(
                        "sales_data_entry.reupload_typed_amount",
                        lease_code=collected.get("lease_code"),
                        amount=amount,
                    )
                    return {
                        "workflow_stage": "AWAITING_DOC_CONFIRM",
                        "status": "IN_PROGRESS",
                        "collected_data": collected,
                    }
            return {"workflow_stage": "AWAITING_DOC_UPLOAD", "status": "IN_PROGRESS"}
        return {"workflow_stage": "DOC_SELECT", "status": "IN_PROGRESS"}

    # ── 4. Manual entry ────────────────────────────────────────────────────
    # Typed amount always goes here. A named lease without an amount also
    # starts manual collection of the missing period/amount — unless this
    # turn came from a notification deep-link (CSV dropzone) or the user
    # explicitly asked to upload a file.
    looks_typed = _looks_like_typed_amount(user_message)
    wants_manual = _explicit_manual_choice(user_message)
    partial_lease_entry = (
        _mentions_lease_code(user_message)
        and not _notification_context(state)
        and not _explicit_csv_request(user_message)
        and workflow_stage not in _PENDING_GATE_STAGES
    )
    if (
        intent == "ENTER_SALES_DATA_MANUAL"
        or action_override == "manual_entry"
        or (
            intent == "UPLOAD_SALES_DATA"
            and action_override not in _CSV_UPLOAD_ACTIONS
            and (looks_typed or partial_lease_entry or wants_manual)
        )
    ):
        log.info("sales_data_entry.manual_entry")
        return {
            "workflow_stage": "MANUAL_ENTRY",
            "status": "IN_PROGRESS",
        }

    # ── 5. CSV upload (file sent in previous turn) ─────────────────────────
    if (
        action_override in _CSV_UPLOAD_ACTIONS
        or backend_refs.get("csv_session_id")
        or corrected_fields.get("csv_session_id")
    ):
        log.info("sales_data_entry.csv_upload")
        return {
            "workflow_stage": "CSV_LAYER1_VALIDATION",
            "status": "IN_PROGRESS",
        }

    # ── 5a. Pending-month chip click ───────────────────────────────────────
    # The chip carries the lease and month, so skip straight to collecting
    # the amount for it — manual_entry_missing_field asks for whatever is
    # still missing, which at this point is only sales_amt.
    if action_override in _PICK_MONTH_ACTIONS:
        picked_lease = str(corrected_fields.get("lease_code") or "").strip()
        picked_ym = str(corrected_fields.get("year_month") or "").strip()[:7]
        if picked_lease and len(picked_ym) == 7:
            # parse_asked_period wants prose ("September 2025"), not a bare
            # year-month, so go through the label the chip itself shows.
            from app.agents.graph.nodes.sales_data.period_utils import (
                format_year_month,
            )

            asked = parse_asked_period(format_year_month(picked_ym))
            collected["lease_code"] = picked_lease
            collected["_upload_lease"] = picked_lease
            if asked:
                collected["_asked_period"] = asked
                backend_refs["asked_period"] = asked
                collected["sales_period_start"] = asked.get("start") or ""
                collected["sales_period_end"] = asked.get("end") or ""
            # A fresh pick replaces any half-entered amount from a previous
            # month, so the confirm card can't show a stale figure.
            collected.pop("sales_amt", None)
            collected.pop("_amount_rejected", None)
            log.info(
                "sales_data_entry.pick_pending_month",
                lease_code=picked_lease,
                year_month=picked_ym,
            )
            return {
                "workflow_stage": "MANUAL_ENTRY",
                "status": "IN_PROGRESS",
                "intent": "ENTER_SALES_DATA_MANUAL",
                "collected_data": collected,
                "backend_refs": backend_refs,
            }
        log.warning(
            "sales_data_entry.pick_pending_month_incomplete",
            lease_code=picked_lease,
            year_month=picked_ym,
        )

    # ── 5b. Pending-month list / other-month reminder ──────────────────────
    # Keep AWAITING_* so the gate can tell list vs reminder vs first check.
    if workflow_stage in _PENDING_GATE_STAGES:
        log.info("sales_data_entry.pending_months_gate", stage=workflow_stage)
        return {
            "workflow_stage": workflow_stage,
            "status": "IN_PROGRESS",
        }

    # ── 6. UPLOAD_SALES_DATA / follow-up after status lookup ───────────────
    naming_new_month = False
    if workflow_stage == "UPLOAD_INTENT":
        stored_asked = asked_period_from_state({
            **state,
            "backend_refs": backend_refs,
            "collected_data": collected,
        })
        new_asked = parse_asked_period(state.get("user_message") or "")
        naming_new_month = bool(
            new_asked
            and (
                not stored_asked
                or new_asked.get("year_month") != stored_asked.get("year_month")
            )
        )
    if (
        (
            intent == "UPLOAD_SALES_DATA"
            or _looks_like_upload_request(user_message)
        )
        and (
            workflow_stage in _GATE_ENTRY_STAGES
            or naming_new_month
        )
        and action_override not in _CSV_UPLOAD_ACTIONS
    ):
        log.info("sales_data_entry.pending_months_check", intent=intent, stage=workflow_stage)
        return {
            "workflow_stage": "PENDING_MONTHS_CHECK",
            "status": "IN_PROGRESS",
        }

    if (
        intent == "UPLOAD_SALES_DATA"
        or _looks_like_upload_request(user_message)
    ) and workflow_stage == "UPLOAD_INTENT" and action_override not in _CSV_UPLOAD_ACTIONS:
        log.info("sales_data_entry.upload_intent_repeat", intent=intent)
        return _upload_prompt_patch(state)

    # ── 7. Continue existing stage ─────────────────────────────────────────
    if workflow_stage:
        log.info("sales_data_entry.continue_stage", stage=workflow_stage)
        return {"status": "IN_PROGRESS"}

    # ── 8. Fallback: prompt user ───────────────────────────────────────────
    return _upload_prompt_patch(state)
