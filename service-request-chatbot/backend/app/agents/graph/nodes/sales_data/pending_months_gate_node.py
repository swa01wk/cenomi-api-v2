"""Gate before the tenant CSV upload prompt.

Fetches PENDING sales-data rows and either:
  - lists pending months (no month named yet)
  - reminds about other PENDING months on the same lease, then waits for
    proceed / switch
  - continues to the normal upload prompt when there is nothing else pending
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.graph.nodes.sales_data.pending_months import (
    extract_lease_code,
    format_other_months_reminder,
    format_pending_list_message,
    leases_with_month,
    month_pending_on_lease,
    other_pending_months,
    pending_period_items,
)
from app.agents.graph.nodes.sales_data.period_utils import (
    asked_period_from_state,
    parse_asked_period,
)
from app.agents.graph.state import ServiceRequestState
from app.agents.services.sales_data_api_client import SalesDataApiClient
from app.core.request_context import get_current_bearer_token
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

_PROCEED_ACTIONS = frozenset({
    "proceed_other_months",
    "confirm_other_months",
    "proceed",
    "confirm",
})
_SWITCH_ACTIONS = frozenset({"switch_pending_month", "switch_month"})
_PROCEED_PHRASES = frozenset({
    "yes", "y", "ok", "okay", "proceed", "continue", "confirm", "sure",
    "just this", "this month only",
})


def _notification_lease(state: ServiceRequestState) -> str:
    corrected = state.get("corrected_fields") or {}
    refs = state.get("backend_refs") or {}
    raw = corrected.get("notification_context") or refs.get("notification_context") or {}
    if not isinstance(raw, dict):
        return ""
    return str(raw.get("lease_code") or "").strip()


def _stored_lease(state: ServiceRequestState) -> str:
    collected = state.get("collected_data") or {}
    return str(collected.get("_upload_lease") or "").strip()


def _resolve_lease(
    state: ServiceRequestState,
    items: list[dict[str, Any]],
    asked_year_month: str | None,
) -> str:
    message = state.get("user_message") or ""
    return (
        extract_lease_code(message)
        or _notification_lease(state)
        or _stored_lease(state)
        or (
            leases_with_month(items, asked_year_month)[0]
            if asked_year_month and len(leases_with_month(items, asked_year_month)) == 1
            else ""
        )
    )


def _upload_prompt(state: ServiceRequestState, *, lease: str, asked: dict[str, str] | None) -> dict[str, Any]:
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        _upload_prompt_patch,
    )

    refs = dict(state.get("backend_refs") or {})
    collected = dict(state.get("collected_data") or {})
    if asked:
        refs["asked_period"] = asked
        collected["_asked_period"] = asked
    if lease:
        collected["_upload_lease"] = lease
        notif = dict(refs.get("notification_context") or {})
        if notif:
            notif["lease_code"] = lease
            if asked:
                notif["period_hint"] = asked.get("label") or notif.get("period_hint")
            refs["notification_context"] = notif
        elif asked:
            refs["notification_context"] = {
                "lease_code": lease,
                "period_hint": asked.get("label") or "",
                "source": "pending_months_gate",
            }
    patched_state = {
        **state,
        "backend_refs": refs,
        "collected_data": collected,
        "user_message": (
            f"{lease} {asked.get('label', '')}".strip()
            if asked or lease
            else (state.get("user_message") or "")
        ),
    }
    return _upload_prompt_patch(patched_state)


def _pending_list_payload(items: list[dict[str, Any]], collected: dict, refs: dict) -> dict[str, Any]:
    message = format_pending_list_message(items)
    collected["_pending_items"] = items
    refs["pending_items"] = items
    grouped = {}
    for item in items:
        grouped.setdefault(item["lease_code"], []).append({
            "label": item["label"],
            "year_month": item["year_month"],
        })
    return {
        "workflow_stage": "AWAITING_PENDING_MONTHS_CHOICE",
        "status": "WAITING_FOR_USER",
        "intent": "UPLOAD_SALES_DATA",
        "backend_refs": refs,
        "collected_data": collected,
        "response_message": message,
        "response_ui": {
            "type": "pending_months_list",
            "leases": [
                {"lease_code": lease, "months": months}
                for lease, months in grouped.items()
            ],
            "show_file_upload": True,
            "show_template_download": True,
            "verbatim": True,
            "message": message,
        },
    }


def _reminder_payload(
    lease: str,
    asked: dict[str, str],
    others: list[dict[str, Any]],
    collected: dict,
    refs: dict,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    message = format_other_months_reminder(lease, asked["label"], others)
    collected["_pending_items"] = items
    collected["_asked_period"] = asked
    collected["_upload_lease"] = lease
    refs["asked_period"] = asked
    refs["pending_items"] = items
    return {
        "workflow_stage": "AWAITING_OTHER_MONTHS_REMINDER",
        "status": "WAITING_FOR_USER",
        "intent": "UPLOAD_SALES_DATA",
        "backend_refs": refs,
        "collected_data": collected,
        "response_message": message,
        "response_ui": {
            "type": "other_months_reminder",
            "lease_code": lease,
            "asked_label": asked["label"],
            "asked_year_month": asked["year_month"],
            "other_months": [
                {"label": month["label"], "year_month": month["year_month"]}
                for month in others
            ],
            "show_file_upload": False,
            "verbatim": True,
            "message": message,
        },
    }


def _decide(
    state: ServiceRequestState,
    items: list[dict[str, Any]],
    *,
    asked: dict[str, str] | None,
    lease: str,
    skip_reminder: bool,
) -> dict[str, Any]:
    refs = dict(state.get("backend_refs") or {})
    collected = dict(state.get("collected_data") or {})
    collected["_pending_items"] = items
    refs["pending_items"] = items

    if not items:
        return _upload_prompt(state, lease=lease, asked=asked)

    if not asked:
        return _pending_list_payload(items, collected, refs)

    if not lease:
        matches = leases_with_month(items, asked["year_month"])
        if len(matches) == 1:
            lease = matches[0]
        else:
            return _pending_list_payload(items, collected, refs)

    if not month_pending_on_lease(items, lease, asked["year_month"]):
        message = (
            f"**{asked['label']}** is not pending for **{lease}**. "
            "Here are the months that are still pending:"
        )
        payload = _pending_list_payload(items, collected, refs)
        payload["response_message"] = message + "\n\n" + payload["response_message"]
        payload["response_ui"]["message"] = payload["response_message"]
        return payload

    others = other_pending_months(items, lease, asked["year_month"])
    if others and not skip_reminder:
        return _reminder_payload(lease, asked, others, collected, refs, items)

    collected["_asked_period"] = asked
    collected["_upload_lease"] = lease
    refs["asked_period"] = asked
    return _upload_prompt(
        {**state, "backend_refs": refs, "collected_data": collected},
        lease=lease,
        asked=asked,
    )


@trace_node("pending_months_gate", "AGENT")
async def pending_months_gate_node(state: ServiceRequestState) -> dict[str, Any]:
    """List pending months or remind about other months on the same lease."""
    refs = dict(state.get("backend_refs") or {})
    collected = dict(state.get("collected_data") or {})
    corrected = dict(state.get("corrected_fields") or {})
    action = state.get("action_override") or ""
    stage = state.get("workflow_stage") or ""
    user_message = (state.get("user_message") or "").strip()
    lowered = user_message.lower()

    on_reminder = stage == "AWAITING_OTHER_MONTHS_REMINDER"
    on_list = stage == "AWAITING_PENDING_MONTHS_CHOICE"

    items: list[dict[str, Any]] = []
    cached = collected.get("_pending_items") or refs.get("pending_items")
    use_cache = (on_list or on_reminder) and isinstance(cached, list) and cached
    if use_cache:
        items = cached
    else:
        try:
            token = get_current_bearer_token()
            client = SalesDataApiClient(bearer_token=token)
            rows = await client.get_sales_data(disable_pagination=True)
            items = pending_period_items(rows)
        except Exception:
            log.warning("pending_months_gate.fetch_failed", exc_info=True)
            if isinstance(cached, list) and cached:
                items = cached
            else:
                asked = asked_period_from_state({**state, "backend_refs": refs})
                lease = _resolve_lease(state, [], asked["year_month"] if asked else None)
                return _upload_prompt(state, lease=lease, asked=asked)

    asked = asked_period_from_state({**state, "backend_refs": refs, "collected_data": collected})
    msg_asked = parse_asked_period(user_message)
    switch_ym = str(corrected.get("year_month") or "").strip()
    if switch_ym and len(switch_ym) >= 7:
        from app.agents.graph.nodes.sales_data.period_utils import format_year_month

        msg_asked = {
            "year_month": switch_ym[:7],
            "label": format_year_month(switch_ym[:7]),
            "start": f"{switch_ym[:7]}-01",
            "end": f"{switch_ym[:7]}-01",
        }

    lease = _resolve_lease(
        state,
        items,
        (msg_asked or asked or {}).get("year_month") if (msg_asked or asked) else None,
    )

    proceed = (
        action in _PROCEED_ACTIONS
        or lowered in _PROCEED_PHRASES
        or lowered.startswith("proceed")
        or "this month only" in lowered
        or lowered in {"upload", "upload csv", "i'll upload", "i will upload"}
    )
    switching = (
        action in _SWITCH_ACTIONS
        or bool(switch_ym)
        or "switch" in lowered
        or (msg_asked and asked and msg_asked["year_month"] != asked["year_month"])
        or (on_list and msg_asked)
        or (on_reminder and msg_asked and msg_asked["year_month"] != (asked or {}).get("year_month"))
    )

    if on_list and proceed and not msg_asked:
        return _upload_prompt(state, lease=lease, asked=None)

    if on_reminder and proceed and not switching:
        return _decide(state, items, asked=asked, lease=lease, skip_reminder=True)

    if switching and msg_asked:
        return _decide(
            state,
            items,
            asked=msg_asked,
            lease=lease,
            skip_reminder=on_reminder,
        )

    if on_reminder and not proceed and not switching:
        others = other_pending_months(items, lease, (asked or {}).get("year_month") or "")
        if asked and others:
            return _reminder_payload(lease, asked, others, collected, refs, items)
        return _pending_list_payload(items, collected, refs)

    skip_reminder = False
    return _decide(
        state,
        items,
        asked=msg_asked or asked,
        lease=lease,
        skip_reminder=skip_reminder,
    )
