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


def build_create_lease_picker() -> dict[str, Any]:
    """Eligible leases for a new Handover SR when lease is not yet resolved.

    Shows the 5 most recently activated eligible leases.  ``sort_by``/``order``
    are forwarded to the platform ``GET /leases`` list mode; the user pages
    through the rest with read-more, or narrows them with the search box.
    """
    return build_resource_picker(
        "leases",
        filters={
            "limit": 5,
            "offset": 0,
            "sort_by": "activation_date",
            "order": "desc",
        },
        selection={"mode": "select_lease"},
        searchable=True,
    )


def should_browse_leases(state: dict[str, Any]) -> bool:
    """True when Handover CREATE should offer eligible-lease browse chips.

    Only offered for the Handover agent (rdd_handover_agent).  Fitout/Trading
    confirmation agents use purpose-scoped lease lookups and must not show the
    generic Handover SR picker.
    """
    from app.core.config import settings

    # See HANDOVER_LEASE_PICKER_ENABLED. The earlier version of this picker was
    # disabled because it listed arbitrary leases the user never asked for; it
    # now leads with the 5 most recently activated ones and offers search, so
    # it narrows the question instead of padding it. Setting the flag false
    # falls back to the plain lease-code prompt.
    if not settings.handover_lease_picker_enabled:
        return False

    active_agent = state.get("active_agent") or ""
    if active_agent not in ("rdd_handover_agent", ""):
        return False
    if (state.get("workflow_stage") or "CREATE_SR") != "CREATE_SR":
        return False
    collected = state.get("collected_data") or {}
    return not collected.get("lease_id")
