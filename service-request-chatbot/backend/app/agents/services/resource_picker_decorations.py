"""Generic ``resource_picker`` suggestion decorations for the chat UI.

Backend emits only ``kind``, ``resource``, ``filters``, and optional ``selection``.
The frontend registry owns fetch URLs, chip labels, and selection handlers.
"""

from __future__ import annotations

from typing import Any

_HANDOVER_INBOX_SR_FILTERS: dict[str, Any] = {
    "limit": 5,
    "offset": 0,
    "self": True,
    "service_category": "FIT_OUT_AND_HANDOVER",
    "sub_category": "HANDOVER",
    "status": "IN_PROCESS",
    "order": "desc",
    "sort_by": "updated_at",
}


def build_resource_picker(
    resource: str,
    *,
    filters: dict[str, Any] | None = None,
    selection: dict[str, Any] | None = None,
    searchable: bool = False,
) -> dict[str, Any]:
    """Return a ``response_decorations`` fragment with a resource picker suggestion.

    ``searchable`` tells the frontend registry to render a search box above the
    chips; the typed term is forwarded to the resource's fetch as ``search``.
    """
    spec: dict[str, Any] = {
        "kind": "resource_picker",
        "resource": resource,
        "filters": dict(filters or {"limit": 5, "offset": 0}),
    }
    if selection:
        spec["selection"] = selection
    if searchable:
        spec["searchable"] = True
    return {"suggestions": spec}


def build_handover_inbox_picker() -> dict[str, Any]:
    """Pending Handover SRs assigned to the current reviewer."""
    return build_resource_picker("service_requests", filters=_HANDOVER_INBOX_SR_FILTERS)


# Each fitout confirmation confirms the *next* milestone, so the platform lists
# only leases that already carry the one before it: the shop handed over before
# fit-out can start, fit-out started before it can end, fit-out ended before
# trading can begin. ``purpose`` is what tells GET /leases which rule to apply.
LEASE_PICKER_PURPOSE_BY_AGENT: dict[str, str] = {
    "rdd_fitout_start_agent": "fitout_confirm",
    "rdd_fitout_end_agent": "fitout_end",
    "rdd_trading_confirmation_agent": "trading_confirm",
}


_GI_LEASE_AGENTS: frozenset[str] = frozenset({"general_inquiry_agent"})


def build_gi_lease_picker() -> dict[str, Any]:
    """Activated leases a General Inquiry can be raised against.

    Resource ``gi_leases`` is separate from handover ``leases`` so the portal
    calls GET /general-inquiry/leases and shows GI empty-state copy.
    """
    return build_resource_picker(
        "gi_leases",
        filters={
            "limit": 5,
            "offset": 0,
            "sort_by": "activation_date",
            "order": "desc",
        },
        selection={"mode": "select_lease"},
        searchable=True,
    )


def build_create_lease_picker(purpose: str | None = None) -> dict[str, Any]:
    """Leases this workflow can be raised against, when none is resolved yet.

    Shows the 5 most recent, pages with read-more and narrows with the search
    box. Without *purpose* these are the handover-eligible leases (newest
    activated first); with one they are the leases holding that workflow's
    prerequisite milestone, newest milestone first.
    """
    filters: dict[str, Any] = {
        "limit": 5,
        "offset": 0,
        "sort_by": "activation_date",
        "order": "desc",
    }
    if purpose:
        filters["purpose"] = purpose
    return build_resource_picker(
        "leases",
        filters=filters,
        selection={"mode": "select_lease"},
        searchable=True,
    )


_LEASE_BROWSE_STAGES: frozenset[str] = frozenset({"CREATE_SR", "CREATE_WORK_PERMIT"})


def lease_picker_purpose(state: dict[str, Any]) -> str | None:
    """The ``purpose`` this state's lease picker must carry, if any."""
    return LEASE_PICKER_PURPOSE_BY_AGENT.get(str(state.get("active_agent") or ""))


def should_browse_leases(state: dict[str, Any]) -> bool:
    """True when a CREATE turn should offer lease browse chips.

    Handover, Work Permit create, General Inquiry, and the three fitout
    confirmations each get a list. ``lease_picker_purpose`` carries the fitout
    rule to the platform; handover and Work Permit use the eligible-lease list.
    General Inquiry uses ``gi_leases`` (GET /general-inquiry/leases).
    """
    from app.core.config import settings

    # See HANDOVER_LEASE_PICKER_ENABLED. The earlier version of this picker was
    # disabled because it listed arbitrary leases the user never asked for; it
    # now leads with the 5 most recently activated ones and offers search, so
    # it narrows the question instead of padding it. Setting the flag false
    # falls back to the plain lease-code prompt.
    if not settings.handover_lease_picker_enabled:
        return False

    collected = state.get("collected_data") or {}
    if collected.get("lease_id"):
        return False

    agent = str(state.get("active_agent") or "")
    stage = state.get("workflow_stage") or "CREATE_SR"

    # Work Permit create shares Handover's eligible-lease picker.
    if agent == "work_permit_agent" or stage == "CREATE_WORK_PERMIT":
        return stage in _LEASE_BROWSE_STAGES

    # General Inquiry lists any activated lease, via its own endpoint.
    if agent in _GI_LEASE_AGENTS:
        return stage == "CREATE_SR"

    # The workflow must be known. An unpinned session used to qualify, and was
    # shown handover's leases by default — the wrong list for whatever it
    # turned out to be. When we do not know the workflow, we do not know which
    # leases are the right ones.
    if agent != "rdd_handover_agent" and agent not in LEASE_PICKER_PURPOSE_BY_AGENT:
        return False
    return stage == "CREATE_SR"
