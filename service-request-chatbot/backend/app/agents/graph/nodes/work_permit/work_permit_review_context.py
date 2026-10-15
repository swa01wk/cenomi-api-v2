"""The one ``ServiceRequestContext`` for a Work Permit review hop.

The review card's buttons, the actions the entry node accepts, the fallback
wording, and the details view's chips all resolve from this context, so they
cannot disagree (e.g. when ``sub_category`` is missing from session state).
"""

from __future__ import annotations

from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.work_permit_schema import in_progress_roles, resolve_wp_sub_category
from app.agents.services.action_resolver import ServiceRequestContext
from app.types.chat import AuthContext


def build_wp_review_context(
    state: ServiceRequestState,
    *,
    workflow_stage: str,
    is_actor: bool = True,
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
