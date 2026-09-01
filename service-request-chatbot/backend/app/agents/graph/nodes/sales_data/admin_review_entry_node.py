"""Admin Review Entry Node.

Handles SALES_DATA_APPROVER and ADMIN intents for reviewing and updating
sales data submission statuses.

Admin actions always collect ``admin_targets`` (a list). One lease is a
list of length 1 — batch approve uses the same confirm + PATCH loop.
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
from app.agents.schemas.sales_data_schema import (
    ADMIN_ACTION_LABELS,
    ADMIN_TABLE_STATUS_LABELS,
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
_REVIEWABLE_STATUSES = frozenset({
    "SUBMITTED",
    "RE_UPLOADED",
    "UPLOADED_SUPPORTING_DOCUMENT",
    "UPLOADED_AUDITED_SALES",
})
_ACTION_HINTS = ("approve", "reupload", "reject", "need_", "request", "accept")
_SELECT_ALL_PHRASES = (
    "approve all",
    "accept all",
    "approve both",
    "accept both",
    "approve them",
    "approve everything",
    "all of them",
    "all submissions",
)
_APPROVE_HINTS = ("approve", "accept", "looks good")
_REUPLOAD_HINTS = ("reupload", "re-upload", "reject", "needs correction")
_DOC_HINTS = ("supporting", "audited", "request doc", "extra doc", "need document")
_LC_RE = re.compile(r"\bLC[-\s]?(\d{3,})\b", re.I)
_BARE_NUM_RE = re.compile(r"\b(\d{3,5})\b")
_ISO_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


def _lease_context_bits(row: dict[str, Any]) -> str:
    """Portal list columns: property, company, brand (from GET /sales-data)."""
    property_name = (row.get("property_name") or row.get("mall_name") or "").strip()
    company = (row.get("company_name") or "").strip()
    brand = (row.get("brand_name") or "").strip()
    parts = [p for p in (property_name, company, brand) if p]
    return " · ".join(parts) if parts else "—"


def _action_examples(rows: list[dict[str, Any]] | None = None) -> str:
    """Build approve/reupload examples from the current review list."""
    codes = [
        str(r.get("lease_code") or "").strip()
        for r in (rows or [])
        if r.get("lease_code")
    ]
    if len(codes) >= 2:
        a, b = codes[0], codes[1]
        return (
            f'"Approve {a}", "Approve {a} and {b}", "Approve all", '
            f'"Request reupload for {a}", '
            f'"Request supporting document for {a}", '
            f'"Request audited sales for {b}"'
        )
    if len(codes) == 1:
        a = codes[0]
        return (
            f'"Approve {a}", "Approve all", "Request reupload for {a}", '
            f'"Request supporting document for {a}", '
            f'"Request audited sales for {a}"'
        )
    return (
        '"Approve LC-1234", "Approve LC-1234 and LC-5678", '
        '"Approve all", "Request reupload for LC-1234", '
        '"Request supporting document for LC-1234", '
        '"Request audited sales for LC-1234"'
    )


def _reviewable(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [r for r in (rows or []) if r.get("status") in _REVIEWABLE_STATUSES]


def _target_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "lease_id": row.get("lease_id"),
        "lease_code": row.get("lease_code"),
        "sales_period_start": row.get("sales_period_start"),
        "sales_period_end": row.get("sales_period_end"),
        "sales_amt": row.get("sales_amt"),
    }


def _digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _lease_tokens_from_message(message: str) -> list[str]:
    """Pull LC-* codes and bare numeric shorthand (not calendar years)."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _LC_RE.finditer(message or ""):
        token = f"LC-{match.group(1)}"
        if token not in seen:
            seen.add(token)
            found.append(token)
    already = {_digits(t) for t in found}
    for match in _BARE_NUM_RE.finditer(message or ""):
        num = match.group(1)
        if num in already:
            continue
        if int(num) == 0:
            continue
        already.add(num)
        found.append(num)
    return found


def _select_all(message: str) -> bool:
    lowered = (message or "").lower()
    return any(phrase in lowered for phrase in _SELECT_ALL_PHRASES)


def _mixed_actions(message: str) -> bool:
    lowered = (message or "").lower()
    kinds = 0
    if any(h in lowered for h in _APPROVE_HINTS):
        kinds += 1
    if any(h in lowered for h in _REUPLOAD_HINTS):
        kinds += 1
    if any(h in lowered for h in _DOC_HINTS):
        kinds += 1
    return kinds > 1


def _deterministic_action(message: str) -> str | None:
    lowered = (message or "").lower()
    if any(h in lowered for h in _DOC_HINTS):
        if "audit" in lowered:
            return "NEED_UPLOAD_AUDITED_SALES"
        return "NEED_UPLOAD_SUPPORTING_DOCUMENT"
    if any(h in lowered for h in _REUPLOAD_HINTS):
        return "NEED_REUPLOAD"
    if any(h in lowered for h in _APPROVE_HINTS):
        return "APPROVED"
    return None


def _field_value(fields: dict[str, Any], key: str) -> Any:
    raw = fields.get(key)
    if isinstance(raw, dict):
        return raw.get("value")
    return raw


def _codes_from_fields(fields: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for key in ("lease_codes", "lease_code"):
        val = _field_value(fields, key)
        if isinstance(val, list):
            codes.extend(str(item) for item in val if item)
        elif val:
            codes.append(str(val))
    return codes


def _period_from_message(message: str) -> tuple[str | None, str | None]:
    dates = _ISO_DATE_RE.findall(message or "")
    if len(dates) >= 2:
        return dates[0], dates[1]
    if len(dates) == 1:
        return dates[0], None
    return None, None


def _row_matches_period(row: dict[str, Any], start: str | None, end: str | None) -> bool:
    if not start:
        return True
    row_start = str(row.get("sales_period_start") or "")
    row_end = str(row.get("sales_period_end") or "")
    if end:
        return row_start == start and row_end == end
    return row_start == start or row_start.startswith(start[:7])


def _match_targets(
    requested: list[str],
    reviewable: list[dict[str, Any]],
    *,
    period_start: str | None = None,
    period_end: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    by_code: dict[str, list[dict[str, Any]]] = {}
    by_suffix: dict[str, list[dict[str, Any]]] = {}
    for row in reviewable:
        code = str(row.get("lease_code") or "").strip().upper()
        if not code:
            continue
        by_code.setdefault(code, []).append(row)
        suffix = _digits(code)
        if suffix:
            by_suffix.setdefault(suffix, []).append(row)
            stripped = suffix.lstrip("0") or "0"
            by_suffix.setdefault(stripped, []).append(row)

    matched: list[dict[str, Any]] = []
    unmatched: list[str] = []
    seen: set[tuple[Any, ...]] = set()
    for raw in requested:
        token = str(raw or "").strip().upper().replace(" ", "")
        if token.startswith("LC") and not token.startswith("LC-"):
            token = "LC-" + token[2:].lstrip("-")
        rows = list(by_code.get(token) or [])
        if not rows:
            suffix = _digits(token)
            rows = list(by_suffix.get(suffix) or by_suffix.get(suffix.lstrip("0") or "") or [])
        if not rows:
            suffix = _digits(token)
            if 2020 <= int(suffix or 0) <= 2099:
                continue
            unmatched.append(str(raw))
            continue
        period_rows = [r for r in rows if _row_matches_period(r, period_start, period_end)]
        if period_start and not period_rows:
            unmatched.append(str(raw))
            continue
        for row in period_rows or rows:
            key = (
                row.get("lease_id"),
                row.get("lease_code"),
                row.get("sales_period_start"),
            )
            if key in seen:
                continue
            seen.add(key)
            matched.append(_target_from_row(row))
    return matched, unmatched


def _confirm_patch(
    collected: dict[str, Any],
    backend_refs: dict[str, Any],
    *,
    unmatched: list[str] | None = None,
) -> dict[str, Any]:
    targets: list[dict[str, Any]] = collected.get("admin_targets") or []
    action = collected.get("admin_action", "")
    comment = collected.get("comment_text", "")
    first = targets[0] if targets else {}
    collected["lease_code"] = first.get("lease_code", "")
    collected["lease_id"] = first.get("lease_id")
    collected["sales_period_start"] = first.get("sales_period_start", "")
    collected["sales_period_end"] = first.get("sales_period_end", "")

    lease_lines = []
    for t in targets:
        amt = t.get("sales_amt")
        amt_str = f" — SAR {float(amt):,.2f}" if amt is not None and amt != "" else ""
        period = t.get("sales_period_start") or ""
        period_bit = f" ({period[:7]})" if period else ""
        lease_lines.append(f"• **{t.get('lease_code')}**{period_bit}{amt_str}")
    comment_str = f'\nComment: "{comment}"' if comment else ""
    unmatched_str = ""
    if unmatched:
        unmatched_str = (
            "\n\nI could not match: "
            + ", ".join(f"**{c}**" for c in unmatched)
            + ". Those were skipped."
        )
    action_label = ADMIN_ACTION_LABELS.get(action, action)
    noun = "submission" if len(targets) == 1 else "submissions"
    message = (
        f"Please confirm:\n\n"
        f"**Action:** {action_label}\n"
        f"**{len(targets)} {noun}:**\n"
        + "\n".join(lease_lines)
        + f"{comment_str}{unmatched_str}\n\n"
        "Reply **yes** to confirm or **no** to cancel."
    )
    return {
        "collected_data": collected,
        "backend_refs": backend_refs,
        "confirmation_status": "PENDING",
        "workflow_stage": "ADMIN_CONFIRM",
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": {
            "type": "admin_confirm",
            "action": action,
            "action_label": action_label,
            "lease_code": first.get("lease_code", ""),
            "targets": targets,
            "comment_text": comment,
            "unmatched": unmatched or [],
            "message": message,
        },
    }


async def _load_reviewable(
    backend_refs: dict[str, Any],
) -> list[dict[str, Any]]:
    cached = backend_refs.get("admin_review_rows")
    if isinstance(cached, list) and cached:
        return _reviewable(cached)
    token = get_current_bearer_token()
    client = SalesDataApiClient(bearer_token=token)
    rows = await client.get_sales_data(disable_pagination=True)
    submitted = _reviewable(rows)
    backend_refs["admin_review_rows"] = submitted
    return submitted


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
        has_targets = bool(collected.get("admin_targets")) or bool(
            collected.get("lease_id")
        )
        if collected.get("admin_action") and has_targets:
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
    looks_like_action = any(h in user_message.lower() for h in _ACTION_HINTS)
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

        submitted = _reviewable(rows)
        if not submitted:
            return {
                "status": "WAITING_FOR_USER",
                "workflow_stage": "ADMIN_REVIEW",
                "response_message": "No submitted sales data awaiting review at this time.",
            }

        backend_refs["admin_review_rows"] = submitted
        lines = [f"📋 **{len(submitted)} submission(s) awaiting review:**\n"]
        for r in submitted[:10]:
            badge = ADMIN_TABLE_STATUS_LABELS.get(str(r.get("status") or ""), r.get("status") or "")
            lines.append(
                f"• **{r['lease_code']}** | "
                f"{_lease_context_bits(r)} | "
                f"{r.get('sales_period_start', '')} – {r.get('sales_period_end', '')} | "
                f"SAR {float(r.get('sales_amt', 0)):,.2f} | {badge}"
            )
        if len(submitted) > 10:
            lines.append(f"_...and {len(submitted) - 10} more._")
        lines.append(
            "\nSame as the portal **Ask upload** menu — tell me:\n"
            "• **Approve** a lease\n"
            "• **Request reupload** (sales data)\n"
            "• **Request supporting document**\n"
            "• **Request audited sales**\n\n"
            "For example: "
            + _action_examples(submitted)
            + "."
        )

        return {
            "status": "WAITING_FOR_USER",
            "workflow_stage": "ADMIN_REVIEW",
            "backend_refs": backend_refs,
            "response_message": "\n".join(lines),
            "response_ui": {
                "type": "admin_sales_data_list",
                "rows": submitted,
                "message": "\n".join(lines),
            },
        }

    # ── APPROVE_SALES_DATA — extract action (deterministic, then LLM) ──────
    if user_message or collected.get("admin_action"):
        if _mixed_actions(user_message):
            return {
                "workflow_stage": "ADMIN_REVIEW",
                "status": "WAITING_FOR_USER",
                "collected_data": collected,
                "backend_refs": backend_refs,
                "response_message": (
                    "I can only apply **one action** per confirmation. "
                    "Please approve, request a re-upload, or request documents "
                    "in separate messages — for example "
                    + _action_examples(backend_refs.get("admin_review_rows") or [])
                    + "."
                ),
            }

        fields: dict[str, Any] = {}
        det_action = _deterministic_action(user_message)
        det_codes = _lease_tokens_from_message(user_message)
        det_all = _select_all(user_message)
        need_llm = det_action is None or (not det_codes and not det_all)

        if need_llm:
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
                fields = parsed.get("fields", {}) or {}
            except Exception:
                fields = {}

        if _field_value(fields, "mixed_actions") is True:
            return {
                "workflow_stage": "ADMIN_REVIEW",
                "status": "WAITING_FOR_USER",
                "collected_data": collected,
                "backend_refs": backend_refs,
                "response_message": (
                    "I can only apply **one action** per confirmation. "
                    "Please split that into two messages."
                ),
            }

        action = det_action or _field_value(fields, "action") or collected.get("admin_action")
        comment = _field_value(fields, "comment_text") or collected.get("comment_text") or ""
        select_all = det_all or bool(_field_value(fields, "select_all"))
        requested = list(det_codes)
        for code in _codes_from_fields(fields):
            if code not in requested:
                requested.append(code)

        if action:
            collected["admin_action"] = action
        if comment:
            collected["comment_text"] = comment

        try:
            reviewable = await _load_reviewable(backend_refs)
        except Exception:
            return {
                "status": "WAITING_FOR_USER",
                "response_message": "⚠️ Could not fetch sales data. Please try again.",
            }

        unmatched: list[str] = []
        if select_all:
            targets = [_target_from_row(r) for r in reviewable]
        else:
            targets, unmatched = _match_targets(
                requested,
                reviewable,
                period_start=_period_from_message(user_message)[0],
                period_end=_period_from_message(user_message)[1],
            )

        collected["admin_targets"] = targets

        action = collected.get("admin_action", "")
        if action in _VALID_ADMIN_STATUSES and targets:
            return _confirm_patch(collected, backend_refs, unmatched=unmatched)

        if action in _VALID_ADMIN_STATUSES and requested and not targets:
            return {
                "workflow_stage": "ADMIN_REVIEW",
                "status": "WAITING_FOR_USER",
                "collected_data": collected,
                "backend_refs": backend_refs,
                "response_message": (
                    "None of those leases are in the current review queue: "
                    + ", ".join(f"**{c}**" for c in (unmatched or requested))
                    + '. Say "review sales data" to see what is awaiting review.'
                ),
            }

    return {
        "workflow_stage": "ADMIN_REVIEW",
        "status": "WAITING_FOR_USER",
        "collected_data": collected,
        "backend_refs": backend_refs,
        "response_message": (
            "Please tell me which lease to take action on. "
            "For example: "
            + _action_examples(backend_refs.get("admin_review_rows") or [])
            + "."
        ),
    }
