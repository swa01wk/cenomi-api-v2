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
from app.agents.graph.nodes.sales_data.pending_months import extract_lease_code
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
    "upload again",
    "attach again",
    "attach a different",
    "different file",
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
    r"(?:sar\s*[\d,]+|amount\s*(?:is|:)?\s*[\d,]+|(?:is|:)\s*(?:sar\s*)?[\d,]{3,})",
    re.I,
)
_LEASE_CODE_RE = re.compile(
    r"\b(?:lc[-\s]?\d{3,}|t\d{6,}|(?:lease)\s+[a-z0-9-]{3,})\b",
    re.I,
)
_EXPLICIT_CSV_PHRASES = ("upload", "csv", "attach")
_YES_PHRASES = frozenset({
    "yes", "y", "ok", "okay", "proceed", "continue", "confirm", "sure",
})
_NO_PHRASES = frozenset({"no", "n", "cancel", "nope", "stop"})


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
    return any(token in lowered for token in _EXPLICIT_CSV_PHRASES)


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
            f"Great — please upload the sales data file{where}, "
            "and I'll take it from there.\n\n"
            "📁 **Upload a CSV** using the drop zone or the Upload CSV button below.\n\n"
            "✏️ Or **type the amount** — for example: "
            f'"Sales for {lease} is SAR 85,000."'
        )
    else:
        message = (
            "I can help you submit your sales data. You can either:\n\n"
            "📁 **Upload a CSV file** — download the Cenomi template "
            "(Brand and Property pre-filled), or upload your existing file "
            "(`lease_code`, dates, `sales_amt`). Brand and Property are optional.\n\n"
            "✏️ **Type the details** — for example: "
            '"My sales for August, lease LC-1234, amount SAR 85,000."\n\n'
            "Which would you prefer?\n\n"
            "_Download the CSV template using the button above._"
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
            "response_message": (
                "Sales data submission cancelled. What would you like to do next?"
            ),
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
                f"No problem — please upload the **{asked_label}** file when you are ready."
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
            return {"workflow_stage": "AWAITING_DOC_UPLOAD", "status": "IN_PROGRESS"}
        return {"workflow_stage": "DOC_SELECT", "status": "IN_PROGRESS"}

    # ── 4. Manual entry ────────────────────────────────────────────────────
    # Typed amount always goes here. A named lease without an amount also
    # starts manual collection of the missing period/amount — unless this
    # turn came from a notification deep-link (CSV dropzone) or the user
    # explicitly asked to upload a file.
    looks_typed = _looks_like_typed_amount(user_message)
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
            and (looks_typed or partial_lease_entry)
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
