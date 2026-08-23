"""Admin Review Entry Node.

Handles SALES_DATA_APPROVER and ADMIN intents for reviewing and updating
sales data submission statuses.

Phase 1 implementation: fetches the list and accepts approve/reject actions
via LLM extraction.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.llm.gateway import get_default_gateway as get_llm_gateway
from app.agents.prompts.sales_data_manual_entry_prompt import (
    SALES_DATA_ADMIN_EXTRACTION_SYSTEM_PROMPT,
)
from app.agents.services.sales_data_api_client import SalesDataApiClient
from app.core.request_context import get_current_bearer_token
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)

_VALID_ADMIN_STATUSES = frozenset({
    "APPROVED",
    "NEED_REUPLOAD",
    "NEED_UPLOAD_SUPPORTING_DOCUMENT",
    "NEED_UPLOAD_AUDITED_SALES",
})


@trace_node("admin_review_entry", "AGENT")
async def admin_review_entry_node(state: ServiceRequestState) -> dict[str, Any]:
    """Route admin intent: list submissions or extract approve/reject action."""
    intent: str = state.get("intent") or ""
    user_message: str = state.get("user_message") or ""
    action_override: str | None = state.get("action_override")
    collected: dict = dict(state.get("collected_data") or {})
    backend_refs: dict = dict(state.get("backend_refs") or {})

    # ── Confirm admin action ───────────────────────────────────────────────
    if action_override in ("confirm_admin_action", "confirm"):
        if collected.get("admin_action") and collected.get("lease_id"):
            return {
                "confirmation_status": "CONFIRMED",
                "workflow_stage": "ADMIN_SUBMITTING",
                "status": "IN_PROGRESS",
            }

    # ── Cancel ─────────────────────────────────────────────────────────────
    if action_override in ("cancel", "cancel_admin"):
        return {
            "workflow_stage": "ADMIN_REVIEW",
            "collected_data": {},
            "status": "WAITING_FOR_USER",
            "response_message": "Action cancelled. What would you like to do?",
        }

    # ── REVIEW_SALES_DATA — list submissions ───────────────────────────────
    # Do not re-list when the user is already acting on a row ("Approve LC-2001").
    _action_hints = ("approve", "reupload", "reject", "need_", "request")
    looks_like_action = any(h in user_message.lower() for h in _action_hints)
    if (
        intent == "REVIEW_SALES_DATA"
        and not collected.get("admin_action")
        and not looks_like_action
    ):
        token = get_current_bearer_token()
        client = SalesDataApiClient(bearer_token=token)
        try:
            rows = await client.get_sales_data(disable_pagination=True)
        except Exception:
            return {
                "status": "WAITING_FOR_USER",
                "response_message": "⚠️ Could not fetch sales data. Please try again.",
            }

        submitted = [r for r in rows if r.get("status") == "SUBMITTED"]
        if not submitted:
            return {
                "status": "WAITING_FOR_USER",
                "workflow_stage": "ADMIN_REVIEW",
                "response_message": "No submitted sales data awaiting review at this time.",
            }

        lines = [f"📋 **{len(submitted)} submission(s) awaiting review:**\n"]
        for r in submitted[:10]:
            lines.append(
                f"• **{r['lease_code']}** | "
                f"{r.get('sales_period_start', '')} – {r.get('sales_period_end', '')} | "
                f"SAR {float(r.get('sales_amt', 0)):,.2f}"
            )
        if len(submitted) > 10:
            lines.append(f"_...and {len(submitted) - 10} more._")
        lines.append(
            "\nTo approve or take action, tell me: "
            '"Approve LC-2001" or "Request reupload for LC-2001".'
        )

        return {
            "status": "WAITING_FOR_USER",
            "workflow_stage": "ADMIN_REVIEW",
            "response_message": "\n".join(lines),
            "response_ui": {
                "type": "admin_sales_data_list",
                "rows": submitted,
                "message": "\n".join(lines),
            },
        }

    # ── APPROVE_SALES_DATA — extract action via LLM ────────────────────────
    if user_message or collected.get("admin_action"):
        try:
            gateway = get_llm_gateway()
            history = state.get("conversation_history") or []
            messages = [
                {"role": "system", "content": SALES_DATA_ADMIN_EXTRACTION_SYSTEM_PROMPT},
                *history,
                {"role": "user", "content": user_message},
            ]
            response = await gateway.generate(messages=messages, temperature=0.0)
            raw = response.content.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            parsed = json.loads(raw)
            fields = parsed.get("fields", {})
        except Exception:
            fields = {}

        if "lease_code" in fields:
            collected["lease_code"] = fields["lease_code"].get("value", "")
        if "action" in fields:
            collected["admin_action"] = fields["action"].get("value", "")
        if "comment_text" in fields:
            collected["comment_text"] = fields["comment_text"].get("value", "")

        # Look up lease_id from backend
        if collected.get("lease_code") and not collected.get("lease_id"):
            token = get_current_bearer_token()
            client = SalesDataApiClient(bearer_token=token)
            try:
                rows = await client.get_sales_data(
                    disable_pagination=True,
                    lease_code=collected["lease_code"],
                )
                if rows:
                    collected["lease_id"] = rows[0].get("lease_id")
                    collected["sales_period_start"] = rows[0].get("sales_period_start")
                    collected["sales_period_end"] = rows[0].get("sales_period_end")
            except Exception:
                pass

        action = collected.get("admin_action", "")
        lease_code = collected.get("lease_code", "")
        comment = collected.get("comment_text", "")

        if action in _VALID_ADMIN_STATUSES and lease_code:
            # Build confirmation card
            comment_str = f'\nComment: "{comment}"' if comment else ""
            message = (
                f"Please confirm:\n\n"
                f"**Action:** {action}\n"
                f"**Lease:** {lease_code}{comment_str}\n\n"
                "Reply **yes** to confirm or **no** to cancel."
            )
            return {
                "collected_data": collected,
                "confirmation_status": "PENDING",
                "workflow_stage": "ADMIN_CONFIRM",
                "status": "WAITING_FOR_USER",
                "response_message": message,
                "response_ui": {
                    "type": "admin_confirm",
                    "action": action,
                    "lease_code": lease_code,
                    "comment_text": comment,
                    "message": message,
                },
            }

    return {
        "workflow_stage": "ADMIN_REVIEW",
        "status": "WAITING_FOR_USER",
        "collected_data": collected,
        "response_message": (
            "Please tell me which lease to take action on. "
            'For example: "Approve LC-2001" or "Request reupload for LC-2001 — please double check figures."'
        ),
    }
