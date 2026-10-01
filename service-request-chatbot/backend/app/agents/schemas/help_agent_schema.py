"""Help Agent schema — role-to-intent permissions and supervisor decision model.

This module defines:
  ROLE_PERMITTED_INTENTS    — which supervisor intents each role is allowed to trigger.
  HelpAgentSupervisorDecision — extended supervisor output with ASK_HELP intent.
  intents_for_roles()       — helper to resolve permitted intents for an AuthContext.

Agent taxonomy
--------------
- help_agent  : supervisor classification + FAQ Q&A
- notification_agent : list / explain / mark-read using page viewing context
- rdd_agent   : full RDD lifecycle (CREATE_SR → FM_REVIEW → RDD_REVIEW)

Design principle
----------------
Read intents (ASK_HELP, CHECK_STATUS, PREVIEW) are available to every role —
all stakeholders can ask questions and check SR status at any point in the lifecycle.
Action intents (CREATE_RDD_SR, APPROVE_RDD_SR, etc.) are role-restricted.

The DD_ENGINEER's RDD review action is triggered by ``sr_status_sync`` detecting
``DD_ENGINEER IN_PROGRESS``, NOT by a supervisor intent, so they only need
the read intent set here.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.agents.schemas.ceiling_schema import (
    ACK_ROLES as _CEILING_ACK_ROLES,
    CREATE_ROLES as _CEILING_CREATE_ROLES,
)

# ---------------------------------------------------------------------------
# Shared read intents — available to every authenticated role
# ---------------------------------------------------------------------------

_SR_READ_INTENTS: frozenset[str] = frozenset(
    {
        "ASK_HELP",
        "CHECK_SERVICE_REQUEST_STATUS",
        "PREVIEW_SERVICE_REQUEST",
        "LIST_NOTIFICATIONS",
        "GET_NOTIFICATION_DETAIL",
        "MARK_NOTIFICATION_READ",
        "OPEN_NOTIFICATION",
        "UNKNOWN",
    }
)

# ALL_INTENTS is derived from the WorkflowConfig registry so new workflows
# automatically extend what the ADMIN role can use.
def _build_all_intents() -> frozenset[str]:
    from app.agents.registries.workflow_config import WORKFLOW_CONFIG_REGISTRY
    return frozenset(
        intent
        for cfg in WORKFLOW_CONFIG_REGISTRY.values()
        for intent in cfg.action_intents
    ) | _SR_READ_INTENTS


ALL_INTENTS: frozenset[str] = _build_all_intents()

# ---------------------------------------------------------------------------
# Role → permitted intents
# ---------------------------------------------------------------------------

_SALES_DATA_TENANT_INTENTS: frozenset[str] = frozenset(
    {
        "UPLOAD_SALES_DATA",
        "ENTER_SALES_DATA_MANUAL",
        "CHECK_SALES_DATA_STATUS",
        "UPLOAD_SUPPORTING_DOCUMENT",
    }
)

_SALES_DATA_ADMIN_INTENTS: frozenset[str] = frozenset(
    {
        "REVIEW_SALES_DATA",
        "APPROVE_SALES_DATA",
        "CHECK_SALES_DATA_STATUS",
    }
)

ROLE_PERMITTED_INTENTS: dict[str, frozenset[str]] = {
    # Phase 1 actor — can create/update RDD SRs, create work permit SRs, and
    # create Already Trading (HANDED_OVER) SRs.
    # Also creates/approves Trading Confirmation SRs (FIT_OUT_CONFIRM_TRADING).
    "MALL_MANAGER": _SR_READ_INTENTS
    | frozenset(
        {
            "CREATE_RDD_SERVICE_REQUEST",
            "UPDATE_RDD_SERVICE_REQUEST",
            "CREATE_WORK_PERMIT_SR",
            "APPROVE_WORK_PERMIT_SR",
            "CREATE_HANDED_OVER_SR",
            "CREATE_FITOUT_CONFIRM_TRADING",
            "APPROVE_FITOUT_CONFIRM_TRADING",
        }
    ),
    # Phase 2 actor — FM review: update stage fields, upload docs, approve/save
    "FM_MANAGER": _SR_READ_INTENTS
    | frozenset(
        {
            "UPDATE_RDD_SERVICE_REQUEST",
            "APPROVE_RDD_SERVICE_REQUEST",
            "APPROVE_WORK_PERMIT_SR",
        }
    ),
    # Phase 2 support — same FM review actions as FM_MANAGER.
    # Also creates/approves Trading Confirmation SRs.
    "OPERATIONS": _SR_READ_INTENTS
    | frozenset(
        {
            "UPDATE_RDD_SERVICE_REQUEST",
            "APPROVE_RDD_SERVICE_REQUEST",
            "CREATE_FITOUT_CONFIRM_TRADING",
            "APPROVE_FITOUT_CONFIRM_TRADING",
            "APPROVE_WORK_PERMIT_SR",
        }
    ),
    # Phase 3 actor — RDD review is triggered by sr_status_sync, not supervisor intent.
    # Also creates/approves Fitout Start and End Confirmation SRs.
    "DD_ENGINEER": _SR_READ_INTENTS
    | frozenset(
        {
            "CREATE_FITOUT_CONFIRM_FOS",
            "CREATE_FITOUT_CONFIRM_END",
            "APPROVE_FITOUT_CONFIRM_FOS",
            "APPROVE_FITOUT_CONFIRM_END",
            "APPROVE_WORK_PERMIT_SR",
        }
    ),
    # ── Sales Data roles ──────────────────────────────────────────────────
    # Tenant uploading CSV sales data
    "TENANT_CSV_UPLOADER": _SR_READ_INTENTS | _SALES_DATA_TENANT_INTENTS,
    # Tenant uploading supporting documents
    "TENANT_DOC_UPLOADER": _SR_READ_INTENTS | _SALES_DATA_TENANT_INTENTS,
    # Sales data team member who reviews and approves
    "SALES_DATA_APPROVER": _SR_READ_INTENTS | _SALES_DATA_ADMIN_INTENTS,
    # Unrestricted
    "ADMIN": ALL_INTENTS,
    # Cold L2 Work Permit hop (workflow 6).
    "OPERATION_EXECUTIVES": _SR_READ_INTENTS | frozenset({"APPROVE_WORK_PERMIT_SR"}),
}

# Live cenomi-api AI session JWTs stamp platform role names, not the demo
# aliases (TENANT_CSV_UPLOADER / SALES_DATA_APPROVER). Keep both so inbox
# skip-logic and supervisor shortcuts work against the dump / staging.
SALES_DATA_TENANT_PLATFORM_ROLES: frozenset[str] = frozenset(
    {
        "AUTHORIZED_SIGNATORY",
        "BILLING_CONTACT",
        "PRIMARY_TENANT_CONTACT",
        "COMPANY_ADMIN_CENTRAL",
        # Live AI-session JWTs stamp the role name "COMPANY_ADMIN" (see the
        # "Shop Manager / Company Admin" tenant user) — not "COMPANY_ADMIN_CENTRAL".
        # Keep both so real tokens route the same as the assumed platform name.
        "COMPANY_ADMIN",
        "SHOP_MANAGER",
        "FINANCIAL_CONTROLLER",
        "LEASING_MANAGER",
    }
)
SALES_DATA_APPROVER_PLATFORM_ROLES: frozenset[str] = frozenset({"SALES_DATA_TEAM"})

for _role in SALES_DATA_TENANT_PLATFORM_ROLES:
    ROLE_PERMITTED_INTENTS[_role] = _SR_READ_INTENTS | _SALES_DATA_TENANT_INTENTS
for _role in SALES_DATA_APPROVER_PLATFORM_ROLES:
    ROLE_PERMITTED_INTENTS[_role] = _SR_READ_INTENTS | _SALES_DATA_ADMIN_INTENTS

# Tenant company admins / authorized signatories may also create Work Permit
# SRs via chatbot (same CREATE_WORK_PERMIT_SR path as Mall Manager). Keep their
# sales-data intents from the loop above; union only the WP create intent.
_TENANT_WORK_PERMIT_CREATE_ROLES: frozenset[str] = frozenset(
    {
        "COMPANY_ADMIN_CENTRAL",
        "COMPANY_ADMIN",
        "AUTHORIZED_SIGNATORY",
    }
)
for _role in _TENANT_WORK_PERMIT_CREATE_ROLES:
    ROLE_PERMITTED_INTENTS[_role] = ROLE_PERMITTED_INTENTS.get(
        _role, _SR_READ_INTENTS
    ) | frozenset({"CREATE_WORK_PERMIT_SR"})

# ── Ceiling Inspection (Open / Close) ─────────────────────────────────────────
# Create: Mall Manager, FM Manager, RDD PM; acknowledge: tenant roles
# (ceiling_schema.CREATE_ROLES / ACK_ROLES — read from there, so the two schemas
# cannot drift). Review / approve are further gated by the live operation's role
# in ceiling_review_entry_node.
_CEILING_EXTRA_INTENTS: dict[str, set[str]] = {}
for _role in _CEILING_CREATE_ROLES:
    _CEILING_EXTRA_INTENTS.setdefault(_role, set()).add("CREATE_CEILING_INSPECTION_SR")
for _role in _CEILING_ACK_ROLES:
    _CEILING_EXTRA_INTENTS.setdefault(_role, set()).add("ACKNOWLEDGE_CEILING_INSPECTION_SR")
_CEILING_EXTRA_INTENTS.setdefault("DD_ENGINEER", set()).add("REVIEW_CEILING_INSPECTION_SR")
_CEILING_EXTRA_INTENTS.setdefault("FM_MANAGER", set()).add("APPROVE_CEILING_INSPECTION_SR")
for _role, _intents in _CEILING_EXTRA_INTENTS.items():
    ROLE_PERMITTED_INTENTS[_role] = (
        ROLE_PERMITTED_INTENTS.get(_role, _SR_READ_INTENTS) | frozenset(_intents)
    )


def _rebuild_intents_for_roles() -> None:
    """Rebuild ALL_INTENTS and ADMIN's permitted intents after registry changes.

    Call this after registering additional workflows at runtime so that the
    ADMIN role's intent set stays in sync with the registry.
    """
    global ALL_INTENTS
    ALL_INTENTS = _build_all_intents()
    ROLE_PERMITTED_INTENTS["ADMIN"] = ALL_INTENTS


def intents_for_roles(roles: frozenset[str], is_global_admin: bool = False) -> frozenset[str]:
    """Return the union of permitted intents for a set of roles.

    Falls back to read-only intents when the role is unknown,
    rather than blocking the user entirely.
    """
    if is_global_admin:
        return ALL_INTENTS
    permitted: set[str] = set()
    for role in roles:
        permitted |= ROLE_PERMITTED_INTENTS.get(role, _SR_READ_INTENTS)
    return frozenset(permitted) if permitted else _SR_READ_INTENTS


# ---------------------------------------------------------------------------
# Extended supervisor decision schema
# ---------------------------------------------------------------------------

# HelpAgentIntent is a plain string so adding new workflow intents (e.g.
# "CREATE_RCD_SERVICE_REQUEST") never requires a type-annotation change here.
# Intent validation is enforced by RBAC (ROLE_PERMITTED_INTENTS) and the
# agent registry, not by the Python type system.
HelpAgentIntent = str


class HelpAgentSupervisorDecision(BaseModel):
    """LLM output for the Help Agent supervisor node.

    ``intent`` drives top-level routing:
      - ASK_HELP / UNKNOWN → faq_node
      - CREATE / UPDATE / APPROVE RDD SR intents → registry → stage entry node
      - CHECK_STATUS / PREVIEW → preview_node (via sr_status_sync)

    ``service_category`` and ``sub_category`` are only meaningful for SR action
    intents and are used by the registry for agent lookup.
    """

    intent: HelpAgentIntent
    service_category: str | None = None
    sub_category: str | None = None
    confidence: float = 1.0
    reasoning: str = ""
