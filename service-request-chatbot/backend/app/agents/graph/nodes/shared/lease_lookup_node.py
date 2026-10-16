"""Lease lookup node — resolves lease/tenant identity and enriches collected_data.

Responsibilities
----------------
- Detect lease-identifying signals in ``collected_data`` (lease_code, brand, mall).
- Delegate the actual API call to ``AbstractLeaseLookupService``.
- Trace every external call as a ``TOOL_CALL`` child run via ``TraceManager``.
- Handle three match outcomes:
    0 matches  → ask user for more details (WAITING_FOR_USER).
    1 match    → auto-enrich ``collected_data`` with backend-derived fields.
    N matches  → surface choices via ``response_ui.type = "lease_selection"``.
- Merge a user-selected lease (``selected_lease``) when one is already present.

Non-responsibilities (enforced)
--------------------------------
- MUST NOT call the LLM.
- MUST NOT modify fields that are not backend-derived (title, description, dates, …).
- MUST NOT overwrite backend-derived fields from LLM extraction; only this node
  (and backend API responses) may populate ``BACKEND_PROTECTED_FIELDS``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from app.agents.graph.nodes.shared.merge_state_node import BACKEND_PROTECTED_FIELDS
from app.agents.graph.state import ServiceRequestState
from app.agents.services.lease_lookup_service import (
    AbstractLeaseLookupService,
    LeaseRecord,
    LeaseLookupQuery,
    get_gi_lease_lookup_service,
    get_lease_lookup_service,
    normalise_lease_code,
)
from app.agents.services.resource_picker_decorations import (
    build_create_lease_picker,
    build_gi_lease_picker,
    lease_picker_purpose,
    should_browse_leases,
)
from app.agents.services.review_decorations_service import (
    merge_decorations,
)
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

# Fields from ``LeaseRecord`` that are written directly into ``collected_data``.
# This is a strict superset of BACKEND_PROTECTED_FIELDS (which only lists the
# IDs/codes that must never come from LLM extraction).
_LEASE_ENRICHMENT_FIELDS: frozenset[str] = frozenset(
    {
        "lease_code",
        "lease_id",
        "contract_id",
        "brand",
        "brand_id",
        "mall",
        "property_id",
        "tenant_profile_id",
        "unit_codes",
        "contracted_area",
        "city",
        "lease_brand_mall",
        # Base milestone dates — FOS/FOE/Trading agents use these to suggest
        # pre-filled actual dates and ask the user to confirm or correct them.
        "base_fitout_start_date",
        "base_fitout_end_date",
        "base_trading_date",
        "actual_handover_date",
        "actual_shop_handover_date",
        "actual_fitout_start_date",
        "actual_fitout_end_date",
        "actual_trading_date",
        # Recorded fit-out percentage — context and suggestion floor for the
        # Fitout Progress agent, display-only for everyone else.
        "fitout_progress",
    }
)

_NO_LEASE_FIELDS_MESSAGE = (
    "No lease identifiers were found. Ask the user for the lease code, "
    "or alternatively the brand name, company/tenant name, and/or mall name."
)
_NO_MATCH_MESSAGE = (
    "The lease lookup returned zero matches. Apologise and ask the user to "
    "double-check the lease code, brand name, company name, or mall name they provided."
)
_NO_MATCH_ACCESS_MESSAGE = (
    "No matching lease was found in the malls you manage. Apologise and ask the "
    "user to double-check the lease code, or try a lease at one of their malls. "
    "Trading Confirmation is available to Mall Managers and Operations for leases "
    "in their property scope."
)
_ACTIVATION_PENDING_MESSAGE = (
    "State plainly that the lease was found but is not activated yet, so a "
    "handover cannot be raised for it, and that it becomes available once the "
    "activation date has passed. Do not say the lease could not be found, and "
    "do not ask the user to re-check the code — the code was correct. Offer to "
    "continue with a different lease."
)
_NOT_ELIGIBLE_MESSAGE = (
    "State plainly that the lease was found but is not currently eligible for "
    "a handover request. Do not say it could not be found and do not ask the "
    "user to re-check the code. Offer to continue with a different lease."
)
_HANDOVER_EXISTS_MESSAGE = (
    "The lease already has an active Handover Service Request in progress. "
    "A new Handover SR cannot be created until the existing one is completed or cancelled. "
    "Please check the existing Handover SR, or contact your team to resolve it first. "
    "Ask the user to provide a different lease code if they want to create a new Handover SR."
)
_MULTI_MATCH_MESSAGE = (
    "Multiple leases matched the search. Ask the user to select the correct one "
    "from the list shown."
)

_GI_AGENT_NAMES: frozenset[str] = frozenset({"general_inquiry_agent"})


def _get_lease_service(active_agent: str | None) -> AbstractLeaseLookupService:
    """GI uses GET /general-inquiry/leases. Every other agent uses GET /leases."""
    if active_agent in _GI_AGENT_NAMES:
        return get_gi_lease_lookup_service()
    return get_lease_lookup_service()


def _lease_browse_picker(state: dict[str, Any]) -> dict[str, Any]:
    """Picker decoration for a CREATE turn that has no lease yet."""
    if str(state.get("active_agent") or "") in _GI_AGENT_NAMES:
        return build_gi_lease_picker()
    return build_create_lease_picker(lease_picker_purpose(state))


def _extract_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (ValueError, AttributeError):
        return None


_BASE_DATE_ENRICHMENT_FIELDS: frozenset[str] = frozenset(
    {
        "base_fitout_start_date",
        "base_fitout_end_date",
        "base_trading_date",
        "actual_handover_date",
        "actual_shop_handover_date",
        "actual_fitout_start_date",
        "actual_fitout_end_date",
        "actual_trading_date",
    }
)

_MILESTONE_ACK_KEY = "_milestone_confirmed_ack"

_MILESTONE_BY_AGENT: dict[str, tuple[tuple[str, ...], str]] = {
    "rdd_handover_agent": (
        ("actual_shop_handover_date", "actual_handover_date"),
        "Shop Handover",
    ),
    "rdd_fitout_start_agent": (("actual_fitout_start_date",), "Fitout Start"),
    "rdd_fitout_end_agent": (("actual_fitout_end_date",), "Fitout End"),
    "rdd_trading_confirmation_agent": (("actual_trading_date",), "Trading"),
}

_MILESTONE_CHIPS: dict[str, Any] = {
    "kind": "labeled_options",
    "options": [
        {"label": "Cancel", "value": "cancel", "action": "cancel_milestone"},
        {"label": "Continue", "value": "continue", "action": "continue_milestone"},
    ],
}


def _display_date(raw: str) -> str:
    """Format an ISO YYYY-MM-DD (or datetime) value as DD/MM/YYYY for the user."""
    text = str(raw or "").strip()
    if not text:
        return ""
    date_only = text.split("T", 1)[0]
    parts = date_only.split("-")
    if len(parts) == 3 and len(parts[0]) == 4:
        year, month, day = parts
        return f"{day}/{month}/{year}"
    return text


def _first_filled_date(record: LeaseRecord, fields: tuple[str, ...]) -> str:
    for field_name in fields:
        value = str(getattr(record, field_name, "") or "").strip()
        if value:
            return value
    return ""


def _handover_exists_block() -> dict[str, Any]:
    return {
        "status": "WAITING_FOR_USER",
        "response_message": _HANDOVER_EXISTS_MESSAGE,
        "response_ui": {
            "type": "text_question",
            "field": "lease_code",
            "message": _HANDOVER_EXISTS_MESSAGE,
        },
    }


def _milestone_pending_response(
    record: LeaseRecord,
    collected: dict[str, Any],
    label: str,
    actual_iso: str,
) -> dict[str, Any]:
    """Pause after lease resolve: milestone already confirmed, Cancel / Continue."""
    enriched = _enrich_collected_data(collected, record)
    display = _display_date(actual_iso) or actual_iso
    message = (
        f"The Actual {label} Date for this lease is already confirmed on {display}. "
        "Do you want to continue and revise it, or pick a different lease?"
    )
    return {
        "collected_data": enriched,
        "lease_matches": [],
        "selected_lease": None,
        "confirmation_status": "MILESTONE_PENDING",
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": {"type": "message", "verbatim": True, "message": message},
        "response_decorations": {"suggestions": _MILESTONE_CHIPS},
    }


def _maybe_pause_for_milestone(
    state: ServiceRequestState,
    record: LeaseRecord,
    collected: dict[str, Any],
    *,
    handover_exists: bool,
) -> dict[str, Any] | None:
    """Return a waiting patch when the user must ack an already-confirmed date.

    Returns the hard-block patch when a Handover SR exists and no actual date
    is present. Returns None to proceed with normal enrichment.
    """
    if collected.get(_MILESTONE_ACK_KEY):
        return None
    spec = _MILESTONE_BY_AGENT.get(state.get("active_agent") or "")
    actual = _first_filled_date(record, spec[0]) if spec else ""
    if actual and spec:
        return _milestone_pending_response(record, collected, spec[1], actual)
    if handover_exists:
        return _handover_exists_block()
    return None


def _enrich_collected_data(
    collected: dict[str, Any],
    record: LeaseRecord,
) -> dict[str, Any]:
    """Return a new dict with backend-derived fields merged from *record*."""
    updated = dict(collected)
    for field_name in _LEASE_ENRICHMENT_FIELDS:
        new_val = getattr(record, field_name)
        # Form/bridge may already have hydrated base milestone dates from the
        # contracts lease row.  Do not wipe those with empty strings when the
        # chatbot GET /leases payload omits a date (null / omitempty).
        if (
            field_name in _BASE_DATE_ENRICHMENT_FIELDS
            and (new_val is None or str(new_val).strip() == "")
            and str(updated.get(field_name) or "").strip()
        ):
            continue
        updated[field_name] = new_val
    # 'lease' is required by the validation schema as a display label for the
    # selected lease.  It is not a standalone LeaseRecord attribute — it maps
    # to lease_code so confirmation and submission flows can reference it by
    # that key without any further user input.
    updated["lease"] = record.lease_code
    # Search-trigger alias used by missing_field_node. The lease's company name
    # replaces whatever the user searched with, and ``company_lease_code`` marks
    # it as verified for this lease (the Work Permit summary shows only that).
    # Do not write this into ``company_name`` — the classic form binds that
    # key to tenant_profile_id.
    if record.company_name:
        updated["company"] = record.company_name
        updated["company_lease_code"] = normalise_lease_code(record.lease_code)
    # unit_type: only when the lease row provided it (empty string from API
    # must not wipe a user-supplied value, and we never invent phone/owner/trade).
    if str(getattr(record, "unit_type", "") or "").strip() and not str(
        updated.get("unit_type") or ""
    ).strip():
        updated["unit_type"] = str(record.unit_type).strip()
    # New lease selection → user must re-confirm yes/no in chat even if the
    # form already has a radio value synced from a previous pick.
    updated["_approve_radio_confirmed"] = False
    return updated


async def _with_gi_tenant_contact(
    state: ServiceRequestState,
    enriched: dict[str, Any],
) -> dict[str, Any]:
    """Fill ``tenant_contact`` for General Inquiry from the company's contacts.

    The classic form requires Tenant Contact. Primary contacts are often
    company-level, so this uses the same lookup as submit.
    """
    if (state.get("active_agent") or "") != "general_inquiry_agent":
        return enriched
    if str(enriched.get("tenant_contact") or "").strip():
        return enriched
    from app.agents.graph.nodes.general_inquiry.general_inquiry_payload_builder_node import (
        lookup_primary_tenant_contact,
    )

    label, user_id = await lookup_primary_tenant_contact(
        enriched.get("tenant_profile_id")
    )
    if not label:
        return enriched
    updated = dict(enriched)
    updated["tenant_contact"] = label
    if user_id:
        updated["created_for"] = user_id
    return updated


def _with_mm_optional_offer_after_lease(
    state: ServiceRequestState,
    enriched: dict[str, Any],
) -> dict[str, Any]:
    """After lease resolve: return enriched data and continue to field collection.

    The MM upload offer is seeded post-submission by api_submission_node (not
    here) so that the offer never fires during CREATE_SR field collection.
    """
    return {
        "collected_data": enriched,
        "lease_matches": [],
        "selected_lease": None,
        "status": "IN_PROGRESS",
    }


@trace_node("lease_lookup", "LANGGRAPH_NODE")
async def lease_lookup_node(
    state: ServiceRequestState,
    service: AbstractLeaseLookupService | None = None,
) -> dict[str, Any]:
    """LangGraph node: resolve lease identity and enrich draft state.

    Parameters
    ----------
    state:
        Current graph state.
    service:
        Optional ``AbstractLeaseLookupService`` override (injected in tests).
        Production code uses ``get_lease_lookup_service()``.
    """
    tm = state.get("trace_manager")
    trace_id: UUID | None = _extract_uuid(state.get("trace_id"))

    collected: dict[str, Any] = dict(state.get("collected_data") or {})
    lookup_service = service or _get_lease_service(state.get("active_agent"))

    # ── Path A: user has already selected a lease from a multi-match list ─────
    selected_lease: dict[str, Any] | None = state.get("selected_lease")
    if selected_lease:
        record: LeaseRecord | None = None
        try:
            record = LeaseRecord.model_validate(selected_lease)
        except Exception as exc:
            # The API boundary passes only {"id": "<lease_code>"} — a partial
            # hint rather than a full LeaseRecord.  Extract whatever identifier
            # is available and fall through to Path B for a fresh lookup.
            fallback_code = (
                selected_lease.get("lease_code") or selected_lease.get("id")
            )
            if fallback_code:
                log.info(
                    "lease_lookup.selected_lease.fallback_to_lookup",
                    fallback_code=fallback_code,
                )
                collected["lease_code"] = fallback_code
                # record remains None → fall through to Path B below
            else:
                log.warning("lease_lookup.selected_lease.invalid", error=str(exc))
                return {
                    "status": "WAITING_FOR_USER",
                    # response_message is user-facing: it must address the
                    # user, never instruct the model ("Apologise and ask the
                    # user to...") — that text was being shown verbatim.
                    "response_message": (
                        "Sorry — I couldn't validate that lease. Please select "
                        "it again, or type the lease code."
                    ),
                    "response_ui": {
                        "type": "text_question",
                        "field": "lease_code",
                        "message": _NO_LEASE_FIELDS_MESSAGE,
                    },
                }

        if record is not None:
            pause = _maybe_pause_for_milestone(
                state, record, collected, handover_exists=False
            )
            if pause is not None:
                return pause
            enriched = _enrich_collected_data(collected, record)
            enriched = await _with_gi_tenant_contact(state, enriched)
            log.info(
                "lease_lookup.selected_lease.merged",
                lease_code=record.lease_code,
            )
            return _with_mm_optional_offer_after_lease(state, enriched)
        # Fall through to Path B with collected["lease_code"] set from fallback_code

    # ── Path B: no lease yet selected — run lookup ────────────────────────────
    # "T0105712" or "t0 105712" must match the canonical lowercase codes in the DB.
    normalised_lease_code: str | None = (
        normalise_lease_code(collected.get("lease_code")) or None
    )

    # Forward the user's property scope so the HTTP adapter can restrict the
    # lease search to properties the authenticated user manages.
    auth_context = state.get("auth_context")
    property_ids: list[int] | None = None
    if auth_context is not None:
        raw_ids = getattr(auth_context, "unique_property_ids", None)
        if raw_ids:
            property_ids = [int(pid) for pid in raw_ids if pid is not None]

    # Fitout confirmations, fitout progress, work permit, and ceiling inspection
    # pass an agent-specific purpose so the platform applies that workflow's
    # lease rule instead of the Initiate Handover eligibility gate. Same
    # purpose as the lease picker, from one table.
    active_agent: str | None = state.get("active_agent")
    purpose: str | None = lease_picker_purpose(state)

    query = LeaseLookupQuery(
        lease_code=normalised_lease_code,
        brand=collected.get("brand"),
        mall=collected.get("mall"),
        company=collected.get("company") or collected.get("company_name"),
        property_ids=property_ids,
        purpose=purpose,
    )

    if not query.has_identifiers():
        log.info("lease_lookup.no_identifiers")
        if should_browse_leases(state):
            return {
                "status": "IN_PROGRESS",
                "response_decorations": merge_decorations(
                    state.get("response_decorations"),
                    _lease_browse_picker(state),
                ),
            }
        return {
            "status": "WAITING_FOR_USER",
            "response_message": _NO_LEASE_FIELDS_MESSAGE,
            "response_ui": {
                "type": "text_question",
                "field": "lease_code",
                "message": _NO_LEASE_FIELDS_MESSAGE,
            },
        }

    # ── Open a child TOOL run span ────────────────────────────────────────────
    tool_run_id: UUID | None = None
    if tm is not None and trace_id is not None:
        try:
            tool_run_id = await tm.start_run(
                trace_id=trace_id,
                run_name="lease_api_call",
                run_type="TOOL",
            )
        except Exception:
            log.warning("lease_lookup.start_tool_run.failed", exc_info=True)

    # ── Call the service ──────────────────────────────────────────────────────
    result = await lookup_service.lookup(query)

    # ── Record tool call ──────────────────────────────────────────────────────
    if tm is not None and trace_id is not None and tool_run_id is not None:
        try:
            await tm.capture_tool_call(
                trace_id=trace_id,
                run_id=tool_run_id,
                tool_name="lease_tenant_api",
                tool_type="HTTP",
                request_payload={
                    "endpoint": result.endpoint,
                    **query.model_dump(exclude_none=True),
                },
                response_payload=result.response_payload,
                status_code=result.status_code,
                success=result.error is None,
                latency_ms=result.latency_ms,
                error_message=result.error,
            )
        except Exception:
            log.warning("lease_lookup.capture_tool_call.failed", exc_info=True)

        try:
            await tm.finish_run(
                run_id=tool_run_id,
                output={
                    "match_count": len(result.matches),
                    "latency_ms": result.latency_ms,
                    "status_code": result.status_code,
                },
                status="SUCCESS" if result.error is None else "FAILED",
                error_message=result.error,
            )
        except Exception:
            log.warning("lease_lookup.finish_tool_run.failed", exc_info=True)

    # ── Handle error from service (connection failures, non-200, …) ───────────
    if result.error:
        log.warning(
            "lease_lookup.service_error",
            error=result.error,
            status_code=result.status_code,
        )
        return {
            "status": "WAITING_FOR_USER",
            "response_message": _NO_MATCH_MESSAGE,
            "response_ui": {
                "type": "text_question",
                "field": "lease_code",
                "message": _NO_MATCH_MESSAGE,
            },
        }

    match_count = len(result.matches)
    is_gi = (active_agent or "") in _GI_AGENT_NAMES
    log.info(
        "lease_lookup.result",
        match_count=match_count,
        reason=result.reason,
        purpose=purpose,
        active_agent=active_agent,
    )

    # ── 0 matches: ask for more details ──────────────────────────────────────
    if match_count == 0:
        # Trading / FOS / FOE with purpose set should never see handover_exists.
        # If we do, the platform likely ignored purpose — log loudly.
        if purpose and result.reason == "handover_exists":
            log.warning(
                "lease_lookup.purpose_ignored",
                purpose=purpose,
                reason=result.reason,
                active_agent=active_agent,
            )
        # GI never gates on an existing handover SR. The dedicated endpoint
        # should not return this reason; if it does, keep going as not-found.
        if is_gi and result.reason == "handover_exists":
            log.warning(
                "lease_lookup.gi_unexpected_handover_exists",
                reason=result.reason,
                active_agent=active_agent,
            )
        # Handover agent: lease exists but already has an active Handover SR
        # and the platform did not return the lease row (legacy empty matches).
        elif not purpose and result.reason == "handover_exists":
            return _handover_exists_block()
        # An empty result is not the same as a wrong code. The platform filters
        # the list and says why: a lease with no activation date on record is
        # withheld as ``activation_pending``, and that covers most of the
        # estate. Telling the user "I couldn't find a lease matching that code"
        # sent them to re-check a code that was correct — they had just picked
        # it from the form's own Lease ID list. Only a genuinely unmatched
        # lookup keeps the not-found wording.
        # ``not_found`` is itself one of the platform's reasons, so it must be
        # excluded here — otherwise a genuine miss is announced as "found but
        # not eligible", which is worse than the wording this branch replaced.
        if result.reason == "activation_pending":
            no_match_msg = _ACTIVATION_PENDING_MESSAGE
        elif (
            result.reason
            and result.reason != "not_found"
            and not (is_gi and result.reason == "handover_exists")
        ):
            log.info("lease_lookup.ineligible", reason=result.reason)
            no_match_msg = _NOT_ELIGIBLE_MESSAGE
        else:
            no_match_msg = (
                _NO_MATCH_ACCESS_MESSAGE
                if purpose == "trading_confirm"
                else _NO_MATCH_MESSAGE
            )
        return {
            "status": "WAITING_FOR_USER",
            "response_message": no_match_msg,
            "response_ui": {
                "type": "text_question",
                "field": "lease_code",
                "message": no_match_msg,
            },
        }

    # ── 1 match: auto-enrich collected_data + offer optional MM upload ────────
    if match_count == 1:
        record = result.matches[0]
        handover_exists = (
            not is_gi and not purpose and result.reason == "handover_exists"
        )
        pause = _maybe_pause_for_milestone(
            state, record, collected, handover_exists=handover_exists
        )
        if pause is not None:
            return pause
        enriched = _enrich_collected_data(collected, record)
        enriched = await _with_gi_tenant_contact(state, enriched)
        log.info(
            "lease_lookup.auto_enriched",
            lease_code=record.lease_code,
            brand=record.brand,
            mall=record.mall,
        )
        return _with_mm_optional_offer_after_lease(state, enriched)

    # ── N matches: surface selection UI ───────────────────────────────────────
    match_dicts = [m.model_dump() for m in result.matches]
    return {
        "lease_matches": match_dicts,
        "status": "WAITING_FOR_USER",
        "response_message": _MULTI_MATCH_MESSAGE,
        "response_ui": {
            "type": "lease_selection",
            "matches": match_dicts,
            "message": _MULTI_MATCH_MESSAGE,
        },
    }
