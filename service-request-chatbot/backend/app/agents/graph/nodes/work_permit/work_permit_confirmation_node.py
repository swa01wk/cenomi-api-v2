"""Human confirmation before work permit create or review PATCH.

Create: all CREATE_WORK_PERMIT required fields must be present.
Review: bolded message + compact ``summary`` + business ``actions`` + prompt suggestions.
Non-actors on a review hop get a status message only (no Approve/Reject).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.work_permit_form_fields import (
    FIELD_LABELS as WP_FORM_FIELD_LABELS,
    format_wp_field_display,
    form_spec_for,
    summarize_effective_starting,
    summarize_period,
    summarize_typeofwork,
)
from app.agents.schemas.work_permit_schema import (
    STAGE_LABELS,
    WP_REVIEW_STAGES,
    WORK_PERMIT_TYPE_LABELS,
    get_missing_fields,
    in_progress_roles,
    resolve_wp_sub_category,
)
from app.agents.services.action_resolver import (
    ServiceRequestContext,
    actions_to_ui,
    resolve_actions,
)
from app.agents.services.permission_service import PermissionService
from app.agents.services.work_permit_review_summary import (
    build_wp_review_details,
    build_wp_review_prompt_suggestions,
    build_wp_review_summary,
    compose_wp_review_message,
)
from app.observability.decorators import trace_node
from app.types.chat import AuthContext

logger = logging.getLogger(__name__)

_permission_service = PermissionService()

_FIELD_LABELS: dict[str, str] = {
    "lease_code": "Lease Code",
    "mall": "Mall",
    "brand": "Brand",
    "unit_codes": "Unit Codes",
    **WP_FORM_FIELD_LABELS,
}

_BASE_DISPLAY: tuple[str, ...] = (
    "lease_code",
    "mall",
    "brand",
    "unit_codes",
    "work_permit_type",
)

_EDITABLE_ALWAYS: frozenset[str] = frozenset({"work_permit_type"})

_AI_FIELD_META_KEY = "_ai_field_meta"

_PROVENANCE_HINTS: dict[str, str] = {
    "template": "Filled in for you — edit if you want different wording.",
    "ai": "Drafted by the assistant from your answers — please check it.",
}

_CREATE_MESSAGE = (
    "All required details have been collected. Please review the Work Permit Service "
    "Request below and confirm, or let me know if you want to change anything."
)


def _caller_can_act_on_review(state: ServiceRequestState, workflow_stage: str) -> bool:
    """True when the caller may approve/reject this hop.

    No ``auth_context`` (unit tests / shadow) → permissive, matching PATCH guard.
    """
    auth: AuthContext | None = state.get("auth_context")  # type: ignore[assignment]
    if auth is None:
        return True
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    return _permission_service.can_approve_work_permit(
        auth,
        workflow_stage=workflow_stage,
        in_progress_roles=in_progress_roles(backend_refs.get("sr_operations")),
    )


def _non_actor_review_message(stage: str, sr_id: str | None) -> str:
    label = STAGE_LABELS.get(stage, "the next reviewer")
    sr_bit = f" SR **{sr_id}**" if sr_id else ""
    return f"Work Permit{sr_bit} is with **{label}**. You don't have an action on this step."


def _summarize_workers(value: Any) -> str:
    if isinstance(value, list):
        names = [
            str(row.get("full_name") or "").strip()
            for row in value
            if isinstance(row, dict)
        ]
        names = [n for n in names if n]
        return ", ".join(names) if names else ""
    if isinstance(value, dict):
        workers = value.get("worker")
        names: list[str] = []
        if isinstance(workers, list):
            for row in workers:
                if isinstance(row, dict) and str(row.get("name") or "").strip():
                    names.append(str(row["name"]).strip())
        instructor = str(value.get("instructor_name") or "").strip()
        body = ", ".join(names) if names else ""
        if instructor:
            return f"{body} (instructor: {instructor})" if body else f"instructor: {instructor}"
        return body
    return ""


def _display_value(key: str, raw_val: Any) -> Any:
    if key == "work_permit_type":
        return WORK_PERMIT_TYPE_LABELS.get(raw_val, raw_val)
    if key == "planneddate" and isinstance(raw_val, dict):
        return format_wp_field_display(key, raw_val)
    if key == "workers":
        return format_wp_field_display(key, raw_val) or _summarize_workers(raw_val)
    if key == "measureofsafety" and isinstance(raw_val, dict):
        return format_wp_field_display(key, raw_val)
    if key == "typeofwork":
        return summarize_typeofwork(raw_val)
    if key == "effective_starting" and isinstance(raw_val, dict):
        return summarize_effective_starting(raw_val)
    if key in {"firstperiod", "secondperiod"} and isinstance(raw_val, dict):
        return summarize_period(raw_val)
    if isinstance(raw_val, (dict, list)):
        try:
            return json.dumps(raw_val, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(raw_val)
    return raw_val


def _display_keys(collected_data: dict[str, Any]) -> tuple[str, ...]:
    subtype = resolve_wp_sub_category(None, collected_data)
    spec = form_spec_for(subtype)
    if spec is None:
        return _BASE_DISPLAY
    tenant_keys = [
        k
        for k in spec.common_keys
        if k
        not in {
            "mall",
            "brand",
            "lease",
            "unit_codes",
            "company_name",
            "country_code",
            "tenant_contact",
            "lease_brand_mall",
        }
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for key in list(_BASE_DISPLAY) + tenant_keys:
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return tuple(ordered)


def _provenance_for(
    field_key: str,
    raw_value: Any,
    ai_meta: dict[str, Any],
) -> dict[str, Any]:
    """Label a row as generated — unless the user has since changed it."""
    entry = ai_meta.get(field_key)
    if not isinstance(entry, dict):
        return {}
    if str(entry.get("value") or "") != str(raw_value or ""):
        return {"source": "user"}
    source = str(entry.get("source") or "template")
    row: dict[str, Any] = {"generated": True, "source": source}
    hint = _PROVENANCE_HINTS.get(source)
    if hint:
        row["hint"] = hint
    confidence = entry.get("confidence")
    if source != "template" and isinstance(confidence, (int, float)):
        row["confidence"] = float(confidence)
    return row


def _editable_keys(collected_data: dict[str, Any]) -> frozenset[str]:
    subtype = resolve_wp_sub_category(None, collected_data)
    spec = form_spec_for(subtype)
    if spec is None:
        return _EDITABLE_ALWAYS | frozenset({"notes", "description", "title"})
    return _EDITABLE_ALWAYS | frozenset(spec.field_types.keys()) | frozenset(
        {"notes", "description", "title"}
    )


def _build_create_confirmation_card(collected_data: dict[str, Any]) -> dict[str, Any]:
    editable = _editable_keys(collected_data)
    ai_meta: dict[str, Any] = dict(collected_data.get(_AI_FIELD_META_KEY) or {})
    display_keys = list(_display_keys(collected_data))
    for text_key in ("description", "title"):
        if text_key not in display_keys and collected_data.get(text_key) not in (
            None,
            "",
        ):
            display_keys.append(text_key)
    fields: list[dict[str, Any]] = []
    for key in display_keys:
        raw_val = collected_data.get(key)
        if raw_val in (None, "", []):
            continue
        shown = _display_value(key, raw_val)
        if shown in (None, "", []) or isinstance(shown, (dict, list)):
            continue
        row: dict[str, Any] = {
            "key": key,
            "label": _FIELD_LABELS.get(key, key),
            "value": shown,
            "editable": key in editable,
        }
        row.update(_provenance_for(key, raw_val, ai_meta))
        fields.append(row)
    return {
        "type": "confirmation_card",
        "requestType": "Work Permit Service Request",
        "fields": fields,
        "message": _CREATE_MESSAGE,
        "confirmAction": "confirm_create_wp",
        "cancelAction": "cancel_create_wp",
    }


def _review_context(
    state: ServiceRequestState,
    *,
    workflow_stage: str,
    is_actor: bool,
) -> ServiceRequestContext:
    auth: AuthContext | None = state.get("auth_context")  # type: ignore[assignment]
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    collected: dict[str, Any] = dict(state.get("collected_data") or {})
    subtype = resolve_wp_sub_category(
        state.get("sub_category") or backend_refs.get("create_payload", {}).get("sub_category"),
        collected,
    )
    user_roles: frozenset[str] = frozenset()
    is_admin = False
    if auth is not None:
        user_roles = frozenset(str(r).upper() for r in auth.roles)
        is_admin = bool(auth.is_global_admin) or "ADMIN" in user_roles
    history_raw = backend_refs.get("sr_history") or []
    history: tuple[dict[str, Any], ...] = tuple(
        h for h in history_raw if isinstance(h, dict)
    )
    assign_raw = backend_refs.get("assign_to_options") or []
    assign_opts: tuple[dict[str, str], ...] = tuple(
        {"label": str(o.get("label") or ""), "value": str(o.get("value") or "")}
        for o in assign_raw
        if isinstance(o, dict) and str(o.get("value") or "").strip()
    )
    ext = backend_refs.get("work_permit_extension")
    return ServiceRequestContext(
        sr_type="WORK_PERMIT",
        subtype=subtype,
        workflow_stage=workflow_stage,
        assigned_roles=frozenset(in_progress_roles(backend_refs.get("sr_operations"))),
        user_roles=user_roles,
        platform_status=str(backend_refs.get("platform_sr_status") or "") or None,
        request_history=history,
        work_permit_extension=ext if isinstance(ext, dict) else None,
        assign_to_options=assign_opts,
        is_actor=is_actor,
        is_admin=is_admin,
    )


def _is_on_sr_page(state: ServiceRequestState, sr_id: Any) -> bool:
    """True when the widget is open beside this SR's details form."""
    if not sr_id:
        return False
    page: dict[str, Any] = state.get("msp_context") or {}  # type: ignore[assignment]
    current = str(page.get("current_sr_id") or "").strip()
    return bool(current) and current == str(sr_id).strip()


def _build_review_confirmation_card(
    state: ServiceRequestState,
    *,
    workflow_stage: str,
) -> dict[str, Any]:
    """Generic summary layout: message + ``summary`` + separate ``actions``.

    ``details`` carries every filled field for the card's collapsed-by-default
    "View details"; no note and no in-card links.
    """
    collected: dict[str, Any] = dict(state.get("collected_data") or {})
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    sr_id = backend_refs.get("sr_id")
    subtype = resolve_wp_sub_category(state.get("sub_category"), collected)
    auth: AuthContext | None = state.get("auth_context")  # type: ignore[assignment]
    message = compose_wp_review_message(
        collected,
        workflow_stage=workflow_stage,
        subtype=subtype,
        first_name=auth.first_name if auth is not None else None,
        request_history=backend_refs.get("sr_history"),
        creator=backend_refs.get("sr_creator"),
    )
    ctx = _review_context(state, workflow_stage=workflow_stage, is_actor=True)
    actions = actions_to_ui(resolve_actions(ctx))
    suggestions = build_wp_review_prompt_suggestions(collected, subtype=subtype)
    card: dict[str, Any] = {
        "type": "confirmation_card",
        "requestType": f"Work Permit — {STAGE_LABELS.get(workflow_stage, 'Review')}",
        "message": message,
        "summary": build_wp_review_summary(collected, subtype=subtype),
        "details": build_wp_review_details(collected, subtype=subtype),
        "actions": actions,
        "suggestions": suggestions,
    }
    if sr_id and not _is_on_sr_page(state, sr_id):
        opts = list(suggestions.get("options") or [])
        opts.append(
            {
                "label": "Open full request",
                "value": str(sr_id),
                "action": "view_service_request",
            }
        )
        card["suggestions"] = {"kind": "labeled_options", "options": opts}
    return card


@trace_node("work_permit_confirmation", "AGENT")
async def work_permit_confirmation_node(state: ServiceRequestState) -> dict[str, Any]:
    """Generate confirmation card for create or a review hop."""
    existing_status: str | None = state.get("confirmation_status")
    if existing_status == "CONFIRMED":
        return {}
    if existing_status == "REJECTED":
        extracted = state.get("extracted_fields") or {}
        corrected = state.get("corrected_fields") or {}
        if not extracted and not corrected:
            return {}

    workflow_stage: str = state.get("workflow_stage") or "CREATE_WORK_PERMIT"
    is_review = workflow_stage in WP_REVIEW_STAGES
    collected_data: dict[str, Any] = state.get("collected_data") or {}
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    sr_id = backend_refs.get("sr_id")

    if not is_review:
        missing = get_missing_fields("CREATE_WORK_PERMIT", collected_data)
        if missing:
            logger.info(
                "work_permit_confirmation_node: skipped — %d field(s) still missing: %s",
                len(missing),
                missing,
            )
            return {}

    if is_review and not _caller_can_act_on_review(state, workflow_stage):
        msg = _non_actor_review_message(workflow_stage, str(sr_id) if sr_id else None)
        logger.info(
            "work_permit_confirmation_node: non_actor_status stage=%s",
            workflow_stage,
        )
        ui: dict[str, Any] = {
            "type": "message",
            "message": msg,
            "workflow_stage": workflow_stage,
            "verbatim": True,
        }
        if sr_id and not _is_on_sr_page(state, sr_id):
            ui["suggestions"] = {
                "kind": "labeled_options",
                "options": [
                    {
                        "label": "Open full request",
                        "value": str(sr_id),
                        "action": "view_service_request",
                    }
                ],
            }
        return {
            "confirmation_required": False,
            "confirmation_status": None,
            "status": "WAITING_FOR_USER",
            "response_message": msg,
            "response_ui": ui,
            # Sync's View SR chip would be merged back in by finalize; this
            # reply owns its chips (none while the SR form is already open).
            "response_decorations": {},
        }

    logger.info(
        "work_permit_confirmation_node: generating confirmation card stage=%s",
        workflow_stage,
    )

    if is_review:
        card = _build_review_confirmation_card(state, workflow_stage=workflow_stage)
        return {
            "confirmation_required": True,
            "confirmation_status": "PENDING",
            "status": "READY_TO_SUBMIT",
            "response_ui": card,
            # None keeps the card's own bolded wording (no LLM rewrite).
            "response_message": None,
            "response_decorations": {
                "suggestions": card.get("suggestions"),
            },
        }

    return {
        "confirmation_required": True,
        "confirmation_status": "PENDING",
        "status": "READY_TO_SUBMIT",
        "response_ui": _build_create_confirmation_card(collected_data),
        "response_message": None,
    }
