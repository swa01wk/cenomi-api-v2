"""Human confirmation before work permit create or review PATCH.

Create: all CREATE_WORK_PERMIT required fields must be present.
Review: no extra fields — card is built from GET-hydrated collected_data.
Non-actors on a review hop get a status message only (no Approve/Reject).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.work_permit_form_fields import (
    FIELD_LABELS as WP_FORM_FIELD_LABELS,
    form_spec_for,
)
from app.agents.schemas.work_permit_schema import (
    STAGE_LABELS,
    WP_REVIEW_STAGES,
    WORK_PERMIT_TYPE_LABELS,
    get_missing_fields,
    in_progress_roles,
    resolve_wp_sub_category,
)
from app.agents.services.permission_service import PermissionService
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

_REVIEW_MESSAGE = (
    "Please review this Work Permit hop and approve, or reply 'reject' to decline."
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
    return (
        f"Work Permit{sr_bit} is with **{label}**. "
        "You do not have an action on this hop — open the Service Request to view status, "
        "or wait for the assigned reviewer."
    )


def _fmt_time(raw: Any) -> str:
    text = str(raw or "").strip()
    return text


def _summarize_planneddate(value: dict[str, Any]) -> str:
    start = str(value.get("worktimedate") or "").strip()
    end = str(value.get("completionworkdate") or "").strip()
    t_from = _fmt_time(value.get("worktimefrom") or value.get("worktimefromLT"))
    t_to = _fmt_time(value.get("worktimeto") or value.get("worktimetoLT"))
    left = start
    if t_from:
        left = f"{start} {t_from}".strip()
    right = end
    if t_to:
        right = f"{end} {t_to}".strip()
    if left and right:
        return f"{left} → {right}"
    return left or right or ""


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


def _summarize_measureofsafety(value: dict[str, Any]) -> str:
    from app.agents.schemas.work_permit_form_fields import WP_MEASURE_OF_SAFETY_LABELS

    on = [
        WP_MEASURE_OF_SAFETY_LABELS.get(k, k)
        for k, v in value.items()
        if str(v or "").strip()
    ]
    return ", ".join(on) if on else ""


def _summarize_typeofwork(value: Any) -> str:
    if isinstance(value, dict):
        raw = value.get("value")
        if isinstance(raw, list):
            primary = ", ".join(str(v).strip() for v in raw if str(v or "").strip())
        else:
            primary = str(raw or "").strip()
        other = str(value.get("otheroption") or "").strip()
        if primary and other:
            return f"{primary} ({other})"
        return primary or other
    if isinstance(value, list):
        return ", ".join(str(v).strip() for v in value if str(v or "").strip())
    return str(value or "").strip()


def _summarize_effective_starting(value: dict[str, Any]) -> str:
    left = str(value.get("datefrom") or "").strip()
    right = str(value.get("dateto") or "").strip()
    if left and right:
        return f"{left} → {right}"
    return left or right


def _summarize_period(value: dict[str, Any]) -> str:
    left = str(value.get("from") or value.get("fromLT") or "").strip()
    right = str(value.get("to") or value.get("toLT") or "").strip()
    if left and right:
        return f"{left} – {right}"
    return left or right


def _display_value(key: str, raw_val: Any) -> Any:
    if key == "work_permit_type":
        return WORK_PERMIT_TYPE_LABELS.get(raw_val, raw_val)
    if key == "planneddate" and isinstance(raw_val, dict):
        return _summarize_planneddate(raw_val) or raw_val
    if key == "workers":
        summary = _summarize_workers(raw_val)
        return summary or raw_val
    if key == "measureofsafety" and isinstance(raw_val, dict):
        return _summarize_measureofsafety(raw_val) or raw_val
    if key == "typeofwork":
        return _summarize_typeofwork(raw_val) or raw_val
    if key == "effective_starting" and isinstance(raw_val, dict):
        return _summarize_effective_starting(raw_val) or raw_val
    if key in {"firstperiod", "secondperiod"} and isinstance(raw_val, dict):
        return _summarize_period(raw_val) or raw_val
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
    # Prefer tenant-facing form keys over backend-only common keys.
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


def _build_confirmation_card(
    collected_data: dict[str, Any],
    *,
    review: bool,
    stage: str,
) -> dict[str, Any]:
    editable = _editable_keys(collected_data)
    ai_meta: dict[str, Any] = dict(collected_data.get(_AI_FIELD_META_KEY) or {})
    # Always surface title/description when present (editable + autofill badges).
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
        row: dict[str, Any] = {
            "key": key,
            "label": _FIELD_LABELS.get(key, key),
            "value": _display_value(key, raw_val),
            "editable": (not review) and key in editable,
        }
        row.update(_provenance_for(key, raw_val, ai_meta))
        fields.append(row)
    request_type = "Work Permit Service Request"
    if review:
        request_type = f"Work Permit — {STAGE_LABELS.get(stage, 'Review')}"
    card: dict[str, Any] = {
        "type": "confirmation_card",
        "requestType": request_type,
        "fields": fields,
        "message": _REVIEW_MESSAGE if review else _CREATE_MESSAGE,
    }
    if review:
        card["confirmAction"] = "confirm_approve_wp"
        card["rejectAction"] = "reject_wp"
    else:
        card["confirmAction"] = "confirm_create_wp"
        card["cancelAction"] = "cancel_create_wp"
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
        return {
            "confirmation_required": False,
            "confirmation_status": None,
            "status": "WAITING_FOR_USER",
            "response_message": msg,
            "response_ui": {
                "type": "message",
                "message": msg,
                "workflow_stage": workflow_stage,
            },
        }

    logger.info(
        "work_permit_confirmation_node: generating confirmation card stage=%s",
        workflow_stage,
    )

    return {
        "confirmation_required": True,
        "confirmation_status": "PENDING",
        "status": "READY_TO_SUBMIT",
        "response_ui": _build_confirmation_card(
            collected_data, review=is_review, stage=workflow_stage
        ),
        "response_message": None,
    }
