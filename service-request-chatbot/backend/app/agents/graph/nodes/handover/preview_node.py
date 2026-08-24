"""Preview node — fetch and summarise the current service request state.

Responsibilities
----------------
- If the session has a submitted SR (``backend_refs["sr_id"]`` is set), call the
  platform API to retrieve the live SR data and format it as a structured summary.
- If no SR has been submitted yet, format the draft ``collected_data`` from state.
- Emit ``response_ui`` with ``type = "sr_preview_card"`` so the frontend renders
  a dedicated read-only card with field rows and (when submitted) a status badge.
- Also set ``response_message`` as a text hint for the LLM in response_generation.

Non-responsibilities
--------------------
- MUST NOT modify ``collected_data``, routing fields, or any domain fields.
- MUST NOT submit or approve service requests.
- MUST NOT call the LLM.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.graph.nodes.shared.sr_status_sync_node import (
    _FITOUT_CONFIRM_SUB_CATEGORIES,
)
from app.agents.graph.state import ServiceRequestState
from app.agents.prompts.response_generation_prompt import FIELD_LABELS
from app.agents.services.display_formatters import format_field_display_value
from app.agents.services.service_request_api_service import get_service_request_api_service
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

# Fields shown in the preview card — ordered for readability.
_PREVIEW_FIELD_ORDER: list[tuple[str, str]] = [
    ("lease_code",             "Lease Code"),
    ("brand",                  "Brand"),
    ("mall",                   "Mall"),
    ("city",                   "City"),
    ("unit_codes",             "Units"),
    ("contracted_area",        "Contracted Area"),
    # Discriminator — FE bridge reads this to switch HANDOVER ↔ HANDED_OVER
    # skeletons and pre-select the radio. Must be published on every turn
    # once the user picks a handover type.
    ("already_handovered",     "Handover Type"),
    ("title",                  "Title"),
    ("description",            "Description"),
    ("startDate",              "Inspection Start Date"),
    ("endDate",                "Inspection End Date"),
    ("inspection_done_by",     "Inspection Done By"),
    ("unit_readiness_date",    "Unit Readiness Date"),
    ("expected_handover_date", "Expected Handover Date"),
    ("actual_handover_date",   "Actual Handover Date"),
    ("fitout_start_date",      "Fitout Start Date"),
    ("fitout_end_date",        "Fitout End Date"),
    ("trading_date",           "Trading Date"),
    # Path B (Already Trading / HANDED_OVER) actual dates — must reach the FE
    # so HandoverAgenticBridge can hydrate the HANDED_OVER Formik skeleton.
    ("actual_shop_handover_date", "Actual Shop Handover Date"),
    ("actual_fitout_start_date",  "Actual Fitout Start Date"),
    ("actual_fitout_end_date",    "Actual Fitout End Date"),
    ("actual_trading_date",       "Actual Trading Date"),
    ("notes",                  "Notes"),
    ("comments",               "Comments"),
    ("guideLineLink",          "Guideline Link"),
    # ── Fitout Confirmation (FOS / FOE / Trading) fields ─────────────────
    # These must be published in draft_preview.fields so the FE bridge can
    # apply them to Formik.  format_field_display_value returns the raw
    # string values ("yes"/"no", dates) unchanged so the radio/date fields
    # bind correctly.
    ("fitout_start_done",            "Fitout Started"),
    ("fitout_complete_done",         "Fitout Complete"),
    ("trading_done",                 "Trading Started"),
    ("actual_fitout_complete_date",  "Actual Fitout Complete Date"),
    ("planned_fitout_start_date",    "Planned Fitout Start Date"),
    ("planned_fitout_end_date",      "Planned Fitout End Date"),
    ("planned_fitout_complete_date", "Planned Fitout Complete Date"),
    ("planned_trading_date",         "Planned Trading Date"),
    ("delay_reason",                 "Delay Reason"),
    # Base dates from lease contract — sent to FE so the bridge can populate
    # the read-only "Base Dates" section of the FOS/FOE/Trading form and the
    # ServiceReqForms auto-fill logic can seed actual dates from them.
    ("base_fitout_start_date",       "Base Fitout Start Date"),
    ("base_fitout_end_date",         "Base Fitout End Date"),
    ("base_trading_date",            "Base Trading Date"),
]

# Same keys from the platform API payload blob.
_PLATFORM_FIELD_ORDER: list[tuple[str, str]] = [
    ("title",                  "Title"),
    ("description",            "Description"),
    ("startDate",              "Inspection Start Date"),
    ("endDate",                "Inspection End Date"),
    ("inspection_done_by",     "Inspection Done By"),
    ("unit_readiness_date",    "Unit Readiness Date"),
    ("expected_handover_date", "Expected Handover Date"),
    ("actual_handover_date",   "Actual Handover Date"),
    ("fitout_start_date",      "Fitout Start Date"),
    ("fitout_end_date",        "Fitout End Date"),
    ("trading_date",           "Trading Date"),
    ("actual_shop_handover_date", "Actual Shop Handover Date"),
    ("actual_fitout_start_date",  "Actual Fitout Start Date"),
    ("actual_fitout_end_date",    "Actual Fitout End Date"),
    ("actual_trading_date",       "Actual Trading Date"),
    ("comments",               "Comments"),
    ("guideLineLink",          "Guideline Link"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _field(key: str, label: str, value: Any) -> dict[str, Any]:
    """Build a single preview field dict with user-friendly display values."""
    if value is None or value == "" or value == []:
        display = None
    else:
        formatted = format_field_display_value(key, value)
        display = str(formatted) if formatted is not None else None
    return {"key": key, "label": label, "value": display}


def _hidden_preview_keys(state: ServiceRequestState) -> frozenset[str]:
    """Keys to omit from the preview card for the current SR type.

    The fitout confirmation SRs (FOS / FOE / Trading) never collect ``comments``
    — ``_route_after_validation`` excludes fitout agents from proactive optional
    asking — so a Comments row on their card can only ever show a value the user
    was never given a chance to set.
    """
    sub_category = str(state.get("sub_category") or "")
    if sub_category in _FITOUT_CONFIRM_SUB_CATEGORIES:
        return frozenset({"comments"})
    return frozenset()


def _fields_from_collected(
    collected_data: dict[str, Any],
    hidden_keys: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Build ordered field rows from draft collected_data.

    Only fields explicitly listed in ``_PREVIEW_FIELD_ORDER`` are included, minus
    anything in *hidden_keys* (see ``_hidden_preview_keys``).
    Internal/system fields such as ``brand_id``, ``lease_id``, ``contract_id``,
    ``property_id``, ``tenant_profile_id``, etc. that live in ``collected_data``
    but are not meaningful to the user are intentionally excluded.

    ``startDate`` / ``endDate`` are emitted as UTC ISO
    (``YYYY-MM-DDTHH:MM:SS.mmmZ``) plus companion ``*LT`` display strings so
    the FE DateTimePicker hydrates the user's inspection time instead of
    defaulting date-only values to 9:00 AM.
    """
    rows: list[dict[str, Any]] = []
    from app.agents.services.payload_builder_service import resolve_start_end_hhmm

    start_hhmm, end_hhmm = resolve_start_end_hhmm(collected_data)
    times = {"startDate": start_hhmm, "endDate": end_hhmm}
    for key, label in _PREVIEW_FIELD_ORDER:
        if key in hidden_keys:
            continue
        val = collected_data.get(key)
        if val is None or val == "" or val == []:
            continue
        if key in ("startDate", "endDate"):
            utc_iso, lt_display = _inspection_preview_pair(
                val, collected_data.get(f"{key}LT"), times.get(key)
            )
            rows.append({"key": key, "label": label, "value": utc_iso})
            if lt_display:
                rows.append(
                    {"key": f"{key}LT", "label": f"{label} LT", "value": lt_display}
                )
        else:
            rows.append(_field(key, label, val))
    return rows


def _inspection_preview_pair(
    iso_or_date: Any,
    existing_lt: Any,
    inspection_time: str | None,
) -> tuple[str, str]:
    """Return ``(utc_iso, lt_display)`` for draft_preview start/end dates."""
    from app.agents.services.payload_builder_service import format_inspection_datetime_pair

    utc_iso, lt_display = format_inspection_datetime_pair(
        iso_or_date, existing_lt, inspection_time=inspection_time
    )
    if utc_iso:
        return utc_iso, lt_display
    return str(iso_or_date), lt_display or ("" if existing_lt is None else str(existing_lt))


def _fields_from_platform(
    payload: dict[str, Any],
    hidden_keys: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Build ordered field rows from a platform SR payload blob."""
    rows: list[dict[str, Any]] = []
    for key, label in _PLATFORM_FIELD_ORDER:
        if key in hidden_keys:
            continue
        val = payload.get(key)
        if val is not None and val != "":
            rows.append(_field(key, label, val))
    return rows


def _ops_field(ops: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Summarise workflow operations as a single display field.

    Renders role + status as human-readable labels via ``format_enum_label``
    (falls through to the raw code when unmapped) — e.g. `MALL_MANAGER:
    FINISHED` becomes `Mall Manager: Completed`.
    """
    if not ops:
        return None
    from app.agents.services.display_formatters import format_enum_label
    lines = []
    for op in ops:
        role_raw = op.get("assigned_role") or op.get("role") or "Unknown"
        status_raw = op.get("status") or "—"
        role = format_enum_label(role_raw)
        status = format_enum_label(status_raw)
        lines.append(f"{role}: {status}")
    return {"key": "workflow_operations", "label": "Workflow Stages", "value": " | ".join(lines)}


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


@trace_node("preview", "LANGGRAPH_NODE")
async def preview_node(state: ServiceRequestState) -> dict[str, Any]:
    """Build a rich preview of the current service request for the user.

    Reads
    -----
    state["backend_refs"]   — checked for ``sr_id`` (platform-assigned ID).
    state["collected_data"] — used when no SR has been submitted.

    Writes
    ------
    state["response_ui"]      — ``sr_preview_card`` payload for the frontend.
    state["response_message"] — text hint for response_generation LLM polish.
    state["status"]           — set to ``"WAITING_FOR_USER"``.
    """
    backend_refs: dict[str, Any] = state.get("backend_refs") or {}
    sr_id: str | None = backend_refs.get("sr_id")
    collected_data: dict[str, Any] = state.get("collected_data") or {}
    hidden: frozenset[str] = _hidden_preview_keys(state)

    if sr_id:
        # ── Submitted SR — use synced state when available, else platform GET ──
        platform_status = backend_refs.get("platform_sr_status")
        if platform_status is not None:
            log.info("preview_node.using_synced_state", sr_id=sr_id)
            fields = _fields_from_collected(collected_data, hidden)
            ops_row = _ops_field(backend_refs.get("sr_operations") or [])
            if ops_row:
                fields.append(ops_row)
            response_ui = {
                "type": "sr_preview_card",
                "message": f"Here is your submitted service request (ID: {sr_id}).",
                "requestType": "Handover Service Request",
                "srId": sr_id,
                "status": platform_status or "SUBMITTED",
                "isSubmitted": True,
                "fields": fields,
            }
            response_message = (
                f"Show the user the service request (ID: {sr_id}) "
                f"with status '{platform_status}'. "
                "The structured card is being shown; give a very short 1-sentence intro."
            )
            return {
                "response_ui": response_ui,
                "response_message": response_message,
                "status": "WAITING_FOR_USER",
            }

        log.info("preview_node.fetching_sr", sr_id=sr_id)
        try:
            api_service = get_service_request_api_service()
            result = await api_service.get_service_request(sr_id)
            if result.success:
                payload: dict[str, Any] = result.payload or {}
                fields = _fields_from_platform(payload, hidden)
                ops_row = _ops_field(result.service_request_operations or [])
                if ops_row:
                    fields.append(ops_row)

                response_ui: dict[str, Any] = {
                    "type": "sr_preview_card",
                    "message": f"Here is your submitted service request (ID: {sr_id}).",
                    "requestType": "Handover Service Request",
                    "srId": sr_id,
                    "status": result.status or "SUBMITTED",
                    "isSubmitted": True,
                    "fields": fields,
                }
                response_message = (
                    f"Show the user the service request (ID: {sr_id}) "
                    f"with status '{result.status}'. "
                    "The structured card is being shown; give a very short 1-sentence intro."
                )
                log.info("preview_node.sr_fetched", sr_id=sr_id, status=result.status)
            else:
                log.warning("preview_node.sr_fetch_failed", sr_id=sr_id, error=result.error)
                fields = _fields_from_collected(collected_data, hidden)
                response_ui = {
                    "type": "sr_preview_card",
                    "message": f"Could not fetch live SR {sr_id} — showing draft data.",
                    "requestType": "Handover Service Request",
                    "srId": sr_id,
                    "status": None,
                    "isSubmitted": False,
                    "fields": fields,
                }
                response_message = (
                    f"There was an issue fetching SR {sr_id} ({result.error}). "
                    "Showing collected draft data instead. Keep the intro very short."
                )
        except Exception as exc:
            log.exception("preview_node.sr_fetch_error", sr_id=sr_id, error=str(exc))
            fields = _fields_from_collected(collected_data, hidden)
            response_ui = {
                "type": "sr_preview_card",
                "message": f"Could not reach the platform for SR {sr_id} — showing draft data.",
                "requestType": "Handover Service Request",
                "srId": sr_id,
                "status": None,
                "isSubmitted": False,
                "fields": fields,
            }
            response_message = (
                "Platform unavailable. Showing draft data. Keep the intro very short."
            )
    else:
        # ── Not yet submitted — show collected draft data ─────────────────
        log.info("preview_node.showing_draft")
        fields = _fields_from_collected(collected_data, hidden)
        if not fields:
            response_ui = {
                "type": "sr_preview_card",
                "message": "Nothing has been collected yet in this session.",
                "requestType": "Handover Service Request",
                "srId": None,
                "status": None,
                "isSubmitted": False,
                "fields": [],
            }
            response_message = (
                "Nothing collected yet. Offer to help create, update, or check a service request."
            )
        else:
            response_ui = {
                "type": "sr_preview_card",
                "message": "Here is what has been collected so far (not yet submitted).",
                "requestType": "Handover Service Request",
                "srId": None,
                "status": "DRAFT",
                "isSubmitted": False,
                "fields": fields,
            }
            response_message = (
                "Here is the current draft. "
                "Give a very short 1-sentence intro; the structured card is being shown."
            )

    return {
        "response_ui": response_ui,
        "response_message": response_message,
        "status": "WAITING_FOR_USER",
    }
