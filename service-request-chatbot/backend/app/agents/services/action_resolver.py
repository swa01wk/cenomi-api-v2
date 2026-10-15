"""Generic business-action resolution for SR chatbot review turns.

Deterministic only — the LLM never decides which mutations are legal.
Domain rules (Work Permit today, other SR types later) implement
``ActionResolver.available_actions``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ActionRequires:
    """Inputs that must be collected before the action may execute."""

    comment: bool = False
    role: bool = False
    duration: bool = False
    confirm: bool = False


@dataclass(frozen=True)
class BusinessAction:
    """Executable workflow mutation offered to the user."""

    id: str
    label: str
    requires: ActionRequires = field(default_factory=ActionRequires)

    def to_dict(self) -> dict[str, Any]:
        req: dict[str, bool] = {}
        if self.requires.comment:
            req["comment"] = True
        if self.requires.role:
            req["role"] = True
        if self.requires.duration:
            req["duration"] = True
        if self.requires.confirm:
            req["confirm"] = True
        out: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "type": "workflow_action",
        }
        if req:
            out["requires"] = req
        return out


@dataclass(frozen=True)
class ServiceRequestContext:
    """Snapshot used to decide which business actions are available."""

    sr_type: str
    subtype: str | None
    workflow_stage: str | None
    assigned_roles: frozenset[str]
    user_roles: frozenset[str]
    platform_status: str | None = None
    request_history: tuple[dict[str, Any], ...] = ()
    work_permit_extension: dict[str, Any] | None = None
    assign_to_options: tuple[dict[str, str], ...] = ()
    is_actor: bool = True
    is_admin: bool = False


class ActionResolver(Protocol):
    """Domain plugin that lists legal business actions for a context."""

    def available_actions(self, context: ServiceRequestContext) -> list[BusinessAction]:
        ...


# ── Work Permit ───────────────────────────────────────────────────────────────

# Action ids sent as ``action_override`` on the next turn.
WP_ACTION_APPROVE: str = "confirm_approve_wp"
WP_ACTION_REJECT: str = "reject_wp"
WP_ACTION_MORE_INFO: str = "ask_more_info_wp"
WP_ACTION_ASSIGN: str = "assign_to_wp"
WP_ACTION_EXTEND: str = "extend_wp"
WP_ACTION_CANCEL_PERMIT: str = "cancel_permit_wp"

# Fallback Assign To chips when platform master-data is not loaded into chat.
_WP_DEFAULT_ASSIGN_ROLES: tuple[tuple[str, str], ...] = (
    ("DD Engineer", "DD_ENGINEER"),
    ("FM Manager", "FM_MANAGER"),
    ("Mall Manager", "MALL_MANAGER"),
    ("Operations", "OPERATIONS"),
    ("Operation Executives", "OPERATION_EXECUTIVES"),
)


def wp_assign_to_options(
    context: ServiceRequestContext,
) -> list[dict[str, str]]:
    """Labeled role chips for Assign To (master-data when present, else defaults)."""
    if context.assign_to_options:
        return [
            {"label": str(o.get("label") or o.get("value") or ""), "value": str(o.get("value") or "")}
            for o in context.assign_to_options
            if str(o.get("value") or "").strip()
        ]
    user = {r.upper() for r in context.user_roles}
    out: list[dict[str, str]] = []
    for label, value in _WP_DEFAULT_ASSIGN_ROLES:
        if value.upper() in user and not context.is_admin:
            continue
        out.append({"label": label, "value": value})
    return out


def _last_history_status(history: tuple[dict[str, Any], ...]) -> str:
    if not history:
        return ""
    last = history[-1] if history else {}
    return str(last.get("status") or "").upper()


def _last_history_user_type(history: tuple[dict[str, Any], ...]) -> str:
    if not history:
        return ""
    last = history[-1] if history else {}
    return str(last.get("user_type") or last.get("operator_type") or "").upper()


def should_show_wp_extension_actions(context: ServiceRequestContext) -> bool:
    """True when the form would show Extend/Cancel instead of the review bar."""
    if not context.is_actor and not context.is_admin:
        return False
    history = context.request_history
    last_status = _last_history_status(history)
    last_user = _last_history_user_type(history)
    ext = context.work_permit_extension or {}
    pending = bool(ext.get("pending"))
    already = bool(ext.get("already_extended"))
    tenant_pending = last_user in {"TENANT", ""} and last_status in {"EXTEND", "CANCEL"}
    if not (pending or tenant_pending):
        return False
    if last_status == "EXTEND" and already and not pending:
        # Still allow Cancel after extend was consumed; form filters EXTEND out.
        pass
    # Without operations on context we approximate: actor on a review stage is enough.
    return True


class WorkPermitActionResolver:
    """Work Permit in-progress actions — parity with ``actionButtonSR`` / Extend bar."""

    def available_actions(self, context: ServiceRequestContext) -> list[BusinessAction]:
        if str(context.sr_type or "").upper() != "WORK_PERMIT":
            return []
        if not context.is_actor and not context.is_admin:
            return []

        if should_show_wp_extension_actions(context):
            actions: list[BusinessAction] = []
            last = _last_history_status(context.request_history)
            ext = context.work_permit_extension or {}
            already = bool(ext.get("already_extended"))
            if last == "EXTEND" or bool(ext.get("pending")):
                if not already:
                    actions.append(
                        BusinessAction(
                            id=WP_ACTION_EXTEND,
                            label="Extend",
                            requires=ActionRequires(duration=True, comment=True),
                        )
                    )
            # Cancel decision is always available when the extend bar is shown.
            actions.append(
                BusinessAction(
                    id=WP_ACTION_CANCEL_PERMIT,
                    label="Cancel",
                    requires=ActionRequires(comment=True, confirm=True),
                )
            )
            return actions

        # Standard in-progress review menu (all WP subtypes and levels share
        # this bar). Work Permit approvers cannot reassign, so no Assign To.
        return [
            BusinessAction(
                id=WP_ACTION_APPROVE,
                label="Approve",
                requires=ActionRequires(comment=True),
            ),
            BusinessAction(
                id=WP_ACTION_MORE_INFO,
                label="Ask for more info",
                requires=ActionRequires(comment=True),
            ),
            BusinessAction(
                id=WP_ACTION_REJECT,
                label="Reject",
                requires=ActionRequires(comment=True),
            ),
        ]


def resolve_actions(
    context: ServiceRequestContext,
    *,
    resolver: ActionResolver | None = None,
) -> list[BusinessAction]:
    """Dispatch to the domain resolver (Work Permit today)."""
    if resolver is not None:
        return list(resolver.available_actions(context))
    sr = str(context.sr_type or "").upper()
    if sr == "WORK_PERMIT":
        return WorkPermitActionResolver().available_actions(context)
    return []


def actions_to_ui(actions: list[BusinessAction]) -> list[dict[str, Any]]:
    return [a.to_dict() for a in actions]
