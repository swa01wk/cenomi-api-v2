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
) -> dict[str, Any]:
    """Return a ``response_decorations`` fragment with a resource picker suggestion."""
    spec: dict[str, Any] = {
        "kind": "resource_picker",
        "resource": resource,
        "filters": dict[str, int](filters or {"limit": 5, "offset": 0}),
    }
    if selection:
        spec["selection"] = selection
    return {"suggestions": spec}


def build_handover_inbox_picker() -> dict[str, Any]:
    """Pending Handover SRs assigned to the current reviewer."""
    return build_resource_picker("service_requests", filters=_HANDOVER_INBOX_SR_FILTERS)


def build_create_lease_picker() -> dict[str, Any]:
    """Eligible leases for a new Handover SR when lease is not yet resolved."""
    return build_resource_picker(
        "leases",
        filters={"limit": 5, "offset": 0},
        selection={"mode": "select_lease"},
    )


_LEASE_BROWSE_STAGES: frozenset[str] = frozenset({"CREATE_SR", "CREATE_WORK_PERMIT"})


def should_browse_leases(state: dict[str, Any]) -> bool:
    """True when CREATE should offer eligible-lease browse chips.

    Handover ``CREATE_SR`` and Work Permit ``CREATE_WORK_PERMIT`` share the
    same lease picker. Fitout confirmation stages stay excluded.
    """
    stage = state.get("workflow_stage") or "CREATE_SR"
    if stage not in _LEASE_BROWSE_STAGES:
        return False
    collected = state.get("collected_data") or {}
    return not collected.get("lease_id")
