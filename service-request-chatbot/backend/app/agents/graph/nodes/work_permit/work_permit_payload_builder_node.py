"""Build API payloads for Work Permit create (POST) and review (PATCH)."""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.work_permit_form_fields import (
    coerce_tenant_field_value,
    empty_value_for,
    expand_period_for_api,
    expand_planneddate_for_api,
    form_spec_for,
    is_nested_widget,
)
from app.agents.schemas.work_permit_schema import (
    WP_REVIEW_STAGES,
    generate_wp_description,
    generate_wp_title,
    resolve_wp_sub_category,
    truncate_wp_title,
)
from app.observability.decorators import trace_node

logger = logging.getLogger(__name__)

_REQUIRED_CREATE_KEYS: frozenset[str] = frozenset(
    {"lease_code", "lease_id", "work_permit_type"}
)

# Envelope / GET snapshot keys that must not be nested inside PATCH payload —
# portal form values omit these; Go ignores them, but stripping keeps parity.
_REVIEW_INNER_ENVELOPE_KEYS: frozenset[str] = frozenset(
    {
        "category",
        "service_category",
        "lease_id",
        "lease_code",
        "tenant_profile_id",
        "property_id",
        "sub_category",
        "workflow_version",
        "contract_id",
    }
)


def _apply_ops_period_iso(inner: dict[str, Any]) -> None:
    """Mutate Ops ``firstperiod`` / ``secondperiod`` HH:MM → ISO for Go validators."""
    effective = inner.get("effective_starting")
    if not isinstance(effective, dict):
        return
    date_from = str(effective.get("datefrom") or "").strip()
    if not date_from:
        return
    for key in ("firstperiod", "secondperiod"):
        if key not in inner:
            continue
        period = inner.get(key)
        if not isinstance(period, dict):
            continue
        if not str(period.get("from") or "").strip() and not str(
            period.get("to") or ""
        ).strip():
            continue
        inner[key] = expand_period_for_api(period, date_ddmmyyyy=date_from)


def _inner_payload_from_collected(data: dict[str, Any]) -> dict[str, Any]:
    """Copy this subtype's ``intialState.common`` keys into the nested payload."""
    subtype = resolve_wp_sub_category(None, data)
    spec = form_spec_for(subtype)

    if spec is None:
        # Subtype unknown — minimal lease-shaped shell.
        return {
            "mall": data.get("mall", ""),
            "brand": data.get("brand", ""),
            "lease": data.get("lease_code") or data.get("lease") or "",
            "unit_codes": data.get("unit_codes") or [],
            "lease_brand_mall": data.get("lease_brand_mall") or "",
        }

    inner: dict[str, Any] = {}
    for key in spec.common_keys:
        field_type = spec.field_types.get(key)
        raw = data.get(key)
        if raw is None or raw == "":
            # Lease identifiers always prefer collected lease_code.
            if key == "lease":
                inner[key] = data.get("lease_code") or data.get("lease") or ""
            elif key in {"mall", "brand", "unit_codes", "unit_type", "lease_brand_mall"}:
                inner[key] = data.get(key) if data.get(key) is not None else (
                    [] if key == "unit_codes" else ""
                )
            elif is_nested_widget(key, field_type):
                inner[key] = empty_value_for(key, field_type)
            else:
                inner[key] = ""
            continue
        if is_nested_widget(key, field_type) or key in spec.field_types:
            inner[key] = coerce_tenant_field_value(key, raw, field_type=field_type)
        else:
            inner[key] = raw

    # Prefer authoritative lease_code for the lease slot.
    if data.get("lease_code"):
        inner["lease"] = data["lease_code"]
    if data.get("unit_codes") is not None:
        inner["unit_codes"] = data["unit_codes"]
    for lease_key in ("mall", "brand", "unit_type", "lease_brand_mall"):
        if data.get(lease_key) not in (None, ""):
            inner[lease_key] = data[lease_key]

    # Safety net: portal may require title/description even if chat left them blank.
    if not str(inner.get("description") or "").strip():
        fallback_desc = generate_wp_description(data)
        if fallback_desc:
            inner["description"] = fallback_desc
    if not str(inner.get("title") or "").strip():
        fallback_title = generate_wp_title(data)
        if fallback_title:
            inner["title"] = fallback_title

    # Platform Title max=50 (WorkPermitOperations / Hot / Cold share the limit).
    if "title" in inner:
        inner["title"] = truncate_wp_title(str(inner.get("title") or ""))

    # Portal binds ``company_name`` to tenant_profile_id (numeric). Go structs
    # (e.g. WorkPermitOperations) expect int — never send "" or a display name.
    _apply_company_name_from_tenant_profile(inner, data)

    # Ops schedule: chat HH:MM → ISO8601 for Go is_not_before_time.
    _apply_ops_period_iso(inner)

    # Cold/hot planneddate: ensure portal TimePicker LT aliases are present.
    if isinstance(inner.get("planneddate"), dict):
        inner["planneddate"] = expand_planneddate_for_api(inner["planneddate"])

    return inner


def _apply_company_name_from_tenant_profile(
    inner: dict[str, Any],
    data: dict[str, Any],
) -> None:
    """Set ``company_name`` to int(tenant_profile_id), or omit if unavailable."""
    if "company_name" not in inner and data.get("tenant_profile_id") is None:
        return
    tp = data.get("tenant_profile_id")
    if tp is not None and str(tp).strip() != "":
        try:
            inner["company_name"] = int(tp)
            return
        except (TypeError, ValueError):
            pass
    existing = inner.get("company_name")
    if existing in (None, ""):
        inner.pop("company_name", None)
        return
    try:
        inner["company_name"] = int(existing)
    except (TypeError, ValueError):
        # Non-numeric leftover (e.g. display name) — omit rather than break API.
        inner.pop("company_name", None)


def build_create_work_permit_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Build the nested CREATE work permit API payload.

    Raises ``ValueError`` when required keys are absent.
    """
    missing = [k for k in _REQUIRED_CREATE_KEYS if not data.get(k)]
    if missing:
        raise ValueError(f"Work permit payload missing required keys: {missing}")

    sub_category = resolve_wp_sub_category(None, data)
    title = truncate_wp_title(
        str(data.get("title") or "").strip()
        or generate_wp_title(data)
        or f"Work Permit - {sub_category}"
    )
    return {
        "payload": _inner_payload_from_collected(data),
        "title": title,
        "tenant_profile_id": data.get("tenant_profile_id"),
        "property_id": data.get("property_id"),
        "service_category": "WORK_PERMIT",
        "sub_category": sub_category,
        "lease_code": data.get("lease_code"),
        "lease_id": data.get("lease_id"),
        "service_request_id": "",
    }


def _strip_review_inner_envelope(inner: dict[str, Any]) -> dict[str, Any]:
    """Drop non-form envelope keys from a WP review PATCH nested payload."""
    return {k: v for k, v in inner.items() if k not in _REVIEW_INNER_ENVELOPE_KEYS}


def build_review_work_permit_payload(
    *,
    sub_category: str,
    inner_payload: dict[str, Any],
    action: str = "approve",
    comment: str = "",
    assign_to: str = "",
    approve: bool | None = None,
) -> dict[str, Any]:
    """Build PATCH body for a WP review hop (approve / reject / more info / assign).

    ``approve`` is kept for older callers; prefer ``action``.
    """
    if approve is not None and action == "approve":
        action = "approve" if approve else "reject"
    cleaned = _strip_review_inner_envelope(dict(inner_payload or {}))
    if action == "assign_to":
        body: dict[str, Any] = {
            "status": "REASSIGN_TO",
            "sub_category": sub_category,
            "assign_to": assign_to,
            "payload": cleaned,
        }
        if comment:
            body["comment"] = comment
        return body
    if action == "reject":
        body = {
            "status": "REJECTED",
            "sub_category": sub_category,
        }
        if comment:
            body["comment"] = comment
        return body
    if action == "action_required":
        body = {
            "status": "ACTION_REQUIRED",
            "sub_category": sub_category,
            "payload": cleaned,
        }
        if comment:
            body["comment"] = comment
        return body
    return {
        "status": "APPROVED",
        "sub_category": sub_category,
        "comment": comment,
        "payload": cleaned,
    }


def build_wp_status_payload(
    *,
    sr_id: str,
    action: str,
    comment: str = "",
    duration: dict[str, int] | None = None,
) -> dict[str, Any]:
    """POST /service-requests/work-permit/status body (Extend / Cancel decision)."""
    status = "EXTEND" if action == "extend" else "CANCEL"
    body: dict[str, Any] = {
        "service_request_id": sr_id,
        "status": status,
        "comment": comment or "",
        "document_ids": [],
    }
    if action == "extend" and isinstance(duration, dict):
        if "extension_duration_in_days" in duration:
            body["extension_duration_in_days"] = int(
                duration["extension_duration_in_days"]
            )
        if "extension_duration_in_hours" in duration:
            body["extension_duration_in_hours"] = int(
                duration["extension_duration_in_hours"]
            )
    return body


@trace_node("work_permit_payload_builder", "TOOL")
async def work_permit_payload_builder_node(state: ServiceRequestState) -> dict[str, Any]:
    """Build the work permit POST or PATCH payload and store it in backend_refs."""
    collected_data: dict[str, Any] = state.get("collected_data") or {}
    workflow_stage: str = state.get("workflow_stage") or ""
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})

    if workflow_stage in WP_REVIEW_STAGES:
        existing = backend_refs.get("create_payload") or {}
        inner = (
            existing.get("payload")
            if isinstance(existing.get("payload"), dict)
            else {}
        )
        if not inner:
            inner = _inner_payload_from_collected(collected_data)
        sub_category = resolve_wp_sub_category(
            state.get("sub_category") or existing.get("sub_category"),
            collected_data,
        )
        wp_action = str(backend_refs.get("wp_action") or "approve")
        comment = str(
            collected_data.get("comments") or collected_data.get("notes") or ""
        )
        if wp_action in {"extend", "cancel_permit"}:
            sr_id = str(backend_refs.get("sr_id") or "")
            duration = backend_refs.get("wp_extension_duration")
            payload = build_wp_status_payload(
                sr_id=sr_id,
                action="extend" if wp_action == "extend" else "cancel",
                comment=comment,
                duration=duration if isinstance(duration, dict) else None,
            )
            backend_refs["create_payload"] = payload
            backend_refs["wp_submit_mode"] = "work_permit_status"
        else:
            assign_to = str(backend_refs.get("wp_assign_to") or "")
            payload = build_review_work_permit_payload(
                sub_category=sub_category,
                inner_payload=inner,
                action=wp_action,
                comment=comment,
                assign_to=assign_to,
            )
            backend_refs["create_payload"] = payload
            backend_refs["wp_submit_mode"] = "patch"
        logger.info(
            "work_permit_payload_builder_node: review payload stage=%s action=%s sub=%s",
            workflow_stage,
            wp_action,
            sub_category,
        )
        return {"backend_refs": backend_refs}

    if workflow_stage != "CREATE_WORK_PERMIT":
        logger.info(
            "work_permit_payload_builder_node: skipped for stage=%s", workflow_stage
        )
        return {}

    try:
        payload = build_create_work_permit_payload(collected_data)
    except ValueError as exc:
        logger.warning("work_permit_payload_builder_node: %s", exc)
        return {"status": "FAILED"}

    backend_refs["create_payload"] = payload
    backend_refs.pop("wp_submit_mode", None)
    logger.info(
        "work_permit_payload_builder_node: create payload lease_code=%s permit_type=%s",
        collected_data.get("lease_code"),
        payload.get("sub_category"),
    )
    return {"backend_refs": backend_refs, "sub_category": payload.get("sub_category")}
