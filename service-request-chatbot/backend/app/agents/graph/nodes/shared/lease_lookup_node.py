"""Lease lookup node — resolves lease/tenant identity and enriches collected_data.

Responsibilities
----------------
- Detect lease-identifying signals in ``collected_data`` (lease_code, brand, mall).
- Delegate the actual API call to ``AbstractLeaseLookupService``.
- Trace every external call as a ``TOOL_CALL`` child run via ``TraceManager``.
- Handle match outcomes:
    0 matches  → inspect ``reason`` (not_found / activation_pending /
                 handover_exists); never collapse all empty results to
                 "lease not found".
    1 match    → auto-enrich ``collected_data`` with backend-derived fields.
    N matches  → surface choices via ``response_ui.type = "lease_selection"``.
- Merge a user-selected lease (``selected_lease``) when one is already present.

Non-responsibilities (enforced)
--------------------------------
- MUST NOT call the LLM.
- MUST NOT modify fields that are not backend-derived (title, description, dates, …).
- MUST NOT overwrite backend-derived fields from LLM extraction; only this node
  (and backend API responses) may populate ``BACKEND_PROTECTED_FIELDS``.
- MUST NOT create / reopen / convert an existing Handover SR when
  ``reason=handover_exists``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from app.agents.graph.nodes.shared.merge_state_node import BACKEND_PROTECTED_FIELDS
from app.agents.graph.state import ServiceRequestState
from app.agents.services.lease_lookup_service import (
    AbstractLeaseLookupService,
    LeaseLookupQuery,
    LeaseLookupResult,
    LeaseRecord,
    get_lease_lookup_service,
)
from app.agents.services.resource_picker_decorations import (
    build_create_lease_picker,
    should_browse_leases,
)
from app.agents.services.review_decorations_service import (
    merge_decorations,
)
from app.core.config import settings
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
_MULTI_MATCH_MESSAGE = (
    "Multiple leases matched the search. Ask the user to select the correct one "
    "from the list shown."
)

# Platform eligibility reasons returned on empty ``leases`` arrays.
_REASON_HANDOVER_EXISTS = "handover_exists"
_REASON_ACTIVATION_PENDING = "activation_pending"
_REASON_NOT_FOUND = "not_found"


def build_sr_details_url(sr_id: str) -> str:
    """Build a Tenant Central deep link to an existing service-request page."""
    path = f"/dashboard/servicerequest/servicedetails?sr_number={sr_id}"
    base = (settings.tenant_portal_base_url or "").rstrip("/")
    return f"{base}{path}" if base else path


def _handover_exists_message(
    *,
    lease_code: str | None,
    sr_id: str,
    details_url: str,
) -> str:
    lease_bit = f" for lease {lease_code}" if lease_code else ""
    return (
        f"A handover already exists{lease_bit} — Service Request #{sr_id}. "
        f"You can view it here: {details_url}. "
        "A new handover cannot be created for this lease."
    )


def _activation_pending_message(*, lease_code: str | None) -> str:
    lease_bit = f" {lease_code}" if lease_code else ""
    return (
        f"Lease{lease_bit} was found, but handover cannot start until the "
        "lease activation/start date is reached. Please try again later, or "
        "provide a different lease code."
    )


def _zero_match_response(
    result: LeaseLookupResult,
    *,
    lease_code: str | None,
) -> dict[str, Any]:
    """Map empty-match eligibility ``reason`` to the correct user-facing response.

    Never collapses ``handover_exists`` / ``activation_pending`` into "not found".
    """
    reason = (result.reason or "").strip().lower() or _REASON_NOT_FOUND
    sr_id = result.existing_handover_sr_id

    if reason == _REASON_HANDOVER_EXISTS and sr_id:
        details_url = build_sr_details_url(sr_id)
        message = _handover_exists_message(
            lease_code=lease_code,
            sr_id=sr_id,
            details_url=details_url,
        )
        # Hint for response_generation: preserve facts; do not invent "not found".
        llm_hint = (
            "IMPORTANT: Do NOT say the lease was not found — it WAS found. "
            f"Relay this to the user verbatim in substance: {message} "
            "Do NOT create a new handover, call it a draft, or offer to reopen it."
        )
        log.info(
            "lease_lookup.handover_exists",
            lease_code=lease_code,
            existing_handover_sr_id=sr_id,
        )
        return {
            "status": "WAITING_FOR_USER",
            "response_message": llm_hint,
            "response_ui": {
                "type": "existing_handover",
                "message": message,
                "srId": str(sr_id),
                "leaseCode": lease_code,
                "detailsUrl": details_url,
            },
        }

    if reason == _REASON_HANDOVER_EXISTS:
        message = (
            f"Lease {lease_code} was found, but a Handover Service Request "
            "already exists for it. A new handover cannot be created."
            if lease_code
            else (
                "A matching lease was found, but a Handover Service Request "
                "already exists for it. A new handover cannot be created."
            )
        )
        llm_hint = (
            "IMPORTANT: Do NOT say the lease was not found — it WAS found. "
            f"Relay this to the user: {message}"
        )
        log.info("lease_lookup.handover_exists_missing_sr_id", lease_code=lease_code)
        return {
            "status": "WAITING_FOR_USER",
            "response_message": llm_hint,
            "response_ui": {
                "type": "message",
                "message": message,
            },
        }

    if reason == _REASON_ACTIVATION_PENDING:
        message = _activation_pending_message(lease_code=lease_code)
        llm_hint = (
            "IMPORTANT: Do NOT say the lease was not found — it WAS found. "
            f"Relay this to the user: {message}"
        )
        log.info("lease_lookup.activation_pending", lease_code=lease_code)
        return {
            "status": "WAITING_FOR_USER",
            "response_message": llm_hint,
            "response_ui": {
                "type": "text_question",
                "field": "lease_code",
                "message": message,
            },
        }

    # not_found or unknown reason with empty matches → keep current copy.
    log.info("lease_lookup.not_found", lease_code=lease_code, reason=reason)
    return {
        "status": "WAITING_FOR_USER",
        "response_message": _NO_MATCH_MESSAGE,
        "response_ui": {
            "type": "text_question",
            "field": "lease_code",
            "message": _NO_MATCH_MESSAGE,
        },
    }


def _extract_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (ValueError, AttributeError):
        return None


def _enrich_collected_data(
    collected: dict[str, Any],
    record: LeaseRecord,
) -> dict[str, Any]:
    """Return a new dict with backend-derived fields merged from *record*."""
    updated = dict(collected)
    for field_name in _LEASE_ENRICHMENT_FIELDS:
        updated[field_name] = getattr(record, field_name)
    # 'lease' is required by the validation schema as a display label for the
    # selected lease.  It is not a standalone LeaseRecord attribute — it maps
    # to lease_code so confirmation and submission flows can reference it by
    # that key without any further user input.
    updated["lease"] = record.lease_code
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
    lookup_service = service or get_lease_lookup_service()

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
                    "response_message": (
                        "The selected lease could not be validated due to a data error. "
                        "Apologise and ask the user to try selecting again or provide the lease code."
                    ),
                    "response_ui": {
                        "type": "text_question",
                        "field": "lease_code",
                        "message": _NO_LEASE_FIELDS_MESSAGE,
                    },
                }

        if record is not None:
            enriched = _enrich_collected_data(collected, record)
            log.info(
                "lease_lookup.selected_lease.merged",
                lease_code=record.lease_code,
            )
            return _with_mm_optional_offer_after_lease(state, enriched)
        # Fall through to Path B with collected["lease_code"] set from fallback_code

    # ── Path B: no lease yet selected — run lookup ────────────────────────────
    # Normalise lease_code: strip whitespace and lowercase so that inputs like
    # "T0105712" or "t0 105712" match the canonical lowercase codes in the DB.
    raw_lease_code: str | None = collected.get("lease_code")
    normalised_lease_code: str | None = (
        raw_lease_code.strip().lower().replace(" ", "") if raw_lease_code else None
    )

    # Forward the user's property scope so the HTTP adapter can restrict the
    # lease search to properties the authenticated user manages.
    auth_context = state.get("auth_context")
    property_ids: list[int] | None = None
    if auth_context is not None:
        raw_ids = getattr(auth_context, "unique_property_ids", None)
        if raw_ids:
            property_ids = [int(pid) for pid in raw_ids if pid is not None]

    query = LeaseLookupQuery(
        lease_code=normalised_lease_code,
        brand=collected.get("brand"),
        mall=collected.get("mall"),
        company=collected.get("company") or collected.get("company_name"),
        property_ids=property_ids,
    )

    if not query.has_identifiers():
        log.info("lease_lookup.no_identifiers")
        if should_browse_leases(state):
            return {
                "status": "IN_PROGRESS",
                "response_decorations": merge_decorations(
                    state.get("response_decorations"),
                    build_create_lease_picker(),
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
    log.info(
        "lease_lookup.result",
        match_count=match_count,
        reason=result.reason,
        existing_handover_sr_id=result.existing_handover_sr_id,
    )

    # ── 0 matches: inspect eligibility reason (never collapse to not_found) ──
    if match_count == 0:
        return _zero_match_response(result, lease_code=normalised_lease_code)

    # ── 1 match: auto-enrich collected_data + offer optional MM upload ────────
    if match_count == 1:
        record = result.matches[0]
        enriched = _enrich_collected_data(collected, record)
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
