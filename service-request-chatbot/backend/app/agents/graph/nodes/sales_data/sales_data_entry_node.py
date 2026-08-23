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
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)

# Actions sent by the frontend UI
_CSV_UPLOAD_ACTIONS = frozenset({"csv_uploaded", "upload_csv"})
_CONFIRM_ACTIONS = frozenset({"CONFIRM_CSV_SUBMISSION", "confirm_csv_submission"})
_CANCEL_ACTIONS = frozenset({"cancel", "cancel_sales_data"})


@trace_node("sales_data_entry", "AGENT")
async def sales_data_entry_node(state: ServiceRequestState) -> dict[str, Any]:
    """Route the turn to the correct sales-data sub-flow."""
    intent: str = state.get("intent") or ""
    action_override: str | None = state.get("action_override")
    workflow_stage: str | None = state.get("workflow_stage")
    backend_refs: dict = state.get("backend_refs") or {}
    corrected_fields: dict = state.get("corrected_fields") or {}

    log.info(
        "sales_data_entry",
        intent=intent,
        action=action_override,
        stage=workflow_stage,
    )

    # ── 0. Cancel ──────────────────────────────────────────────────────────
    if action_override in _CANCEL_ACTIONS:
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

    # ── 2. Status lookup ───────────────────────────────────────────────────
    if intent == "CHECK_SALES_DATA_STATUS" or action_override == "check_status":
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

    # ── 4. Manual entry (notification context or free text) ───────────────
    user_message = (state.get("user_message") or "").lower()
    looks_typed = any(
        token in user_message
        for token in ("lease", "lc-", "sar ", "amount", "enter sales")
    )
    if (
        intent == "ENTER_SALES_DATA_MANUAL"
        or action_override == "manual_entry"
        or (intent == "UPLOAD_SALES_DATA" and looks_typed and action_override not in _CSV_UPLOAD_ACTIONS)
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

    # ── 6. Default: UPLOAD_SALES_DATA — prompt user to upload or type ─────
    if intent == "UPLOAD_SALES_DATA" and not workflow_stage:
        log.info("sales_data_entry.upload_intent_initial")
        return {
            "workflow_stage": "UPLOAD_INTENT",
            "status": "WAITING_FOR_USER",
            "response_message": (
                "I can help you submit your sales data. You can either:\n\n"
                "📁 **Upload a CSV file** — drag and drop or click the attachment button below.\n\n"
                "✏️ **Type the details** — for example: "
                '"My sales for August 2026, lease LC-1001, amount SAR 85,000."\n\n'
                "Which would you prefer?\n\n"
                "_You can also download the CSV template using the button above._"
            ),
            "response_ui": {
                "type": "sales_data_upload_prompt",
                "show_file_upload": True,
                "show_template_download": True,
                "message": (
                    "I can help you submit your sales data. You can either:\n\n"
                    "📁 **Upload a CSV file** — drag and drop or click the attachment button below.\n\n"
                    "✏️ **Type the details** — for example: "
                    '"My sales for August 2026, lease LC-1001, amount SAR 85,000."\n\n'
                    "Which would you prefer?"
                ),
            },
        }

    # ── 7. Continue existing stage ─────────────────────────────────────────
    if workflow_stage:
        log.info("sales_data_entry.continue_stage", stage=workflow_stage)
        return {"status": "IN_PROGRESS"}

    # ── 8. Fallback: prompt user ───────────────────────────────────────────
    return {
        "workflow_stage": "UPLOAD_INTENT",
        "status": "WAITING_FOR_USER",
        "response_message": (
            "I can help you with sales data. Would you like to:\n"
            "- Upload a CSV file\n"
            "- Enter sales figures directly in chat\n"
            "- Check your current submission status"
        ),
    }
