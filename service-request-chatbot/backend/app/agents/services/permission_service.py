"""Centralised authorization checks for graph transitions and API calls.

Permission model
----------------
Each *action* string (e.g. ``"CREATE_HANDOVER_SR"``) maps to a required
*permission string* that the caller's ``AuthContext.roles`` frozenset must
contain.  If the caller does not hold the required permission a
``PermissionDeniedError`` is raised — never silently allowed.

``AuthContext.roles`` is populated from JWT claims (portal
``POST /auth/ai-session-token`` or chatbot ``/api/auth/login``), never from
the chat request body. Role names are expanded via ``ROLE_PERMISSION_MAP``.
"""

from __future__ import annotations

from typing import Any

from app.types.chat import AuthContext

# ---------------------------------------------------------------------------
# Permission map — action → required role/permission string
# ---------------------------------------------------------------------------

ACTION_PERMISSION_MAP: dict[str, str] = {
    # ── Handover: CREATE stage (Mall Manager) ─────────────────────────────────
    "CREATE_HANDOVER_SR": "CAN_RAISE_HANDOVER_SR",

    # ── Handover: FM_REVIEW stage (FM Manager / Operations) ───────────────────
    "UPLOAD_FM_HANDOVER_DOCUMENT": "CAN_FM_REVIEW_HANDOVER_SR",
    "SAVE_FM_HANDOVER_PROGRESS": "CAN_FM_REVIEW_HANDOVER_SR",
    "APPROVE_FM_HANDOVER": "CAN_APPROVE_FM_HANDOVER_SR",
    "REJECT_FM_HANDOVER": "CAN_APPROVE_FM_HANDOVER_SR",

    # ── Handover: RDD_REVIEW stage (DD Engineer) ──────────────────────────────
    "UPLOAD_RDD_HANDOVER_REPORT": "CAN_RDD_REVIEW_HANDOVER_SR",
    "SUBMIT_RDD_HANDOVER_REPORT": "CAN_RDD_REVIEW_HANDOVER_SR",
    "APPROVE_RDD_FINAL": "CAN_RDD_REVIEW_HANDOVER_SR",

    # ── Handover: Read / view ─────────────────────────────────────────────────
    "VIEW_HANDOVER_SR": "VIEW_FIT_OUT_HANDOVER",

    # ── Work Permit: CREATE stage (Mall Manager / contractor) ─────────────────
    "CREATE_WORK_PERMIT_SR": "CAN_RAISE_WORK_PERMIT_SR",

    # ── General Inquiry: optional attachment (tenant or admin creator) ───────
    "CREATE_GENERAL_INQUIRY": "CAN_RAISE_GENERAL_INQUIRY",

    # ── Work Permit: Read / view ──────────────────────────────────────────────
    "VIEW_WORK_PERMIT_SR": "VIEW_WORK_PERMIT",

    # ── Legacy entries (kept for backward compatibility) ──────────────────────
    "FM_APPROVAL": "CAN_APPROVE_HANDOVER_SR",
    "RDD_FINAL_APPROVAL": "CAN_APPROVE_HANDOVER_SR",

    # ── Sales Data (platform permission names — same strings as cenomi-api) ──
    # ENTER_SALES_DATA_AGENT is the front-door gate — same SALES_AGENT
    # permission the frontend's "Upload with AI" / "Review with AI" buttons
    # check — enforced once, before any sales-data routing, independent of
    # the finer SUBMIT_*/APPROVE_SALES_DATA checks below (those still apply
    # per-action for a caller who did get past the front door).
    "ENTER_SALES_DATA_AGENT": "SALES_AGENT",
    "SUBMIT_SALES_DATA": "SALES_DATA_UPDATE",
    "SUBMIT_SALES_DATA_SUPPORTING_DOC": "UPLOAD_SALES_DATA",
    "APPROVE_SALES_DATA": "SALES_DATA_APPROVE",
}

# ---------------------------------------------------------------------------
# Role permission map — role → frozenset of permission strings
#
# Work Permit CREATE via chatbot (`CAN_RAISE_WORK_PERMIT_SR`) is Mall Manager /
# Admin plus tenant COMPANY_ADMIN_CENTRAL / COMPANY_ADMIN / AUTHORIZED_SIGNATORY.
# Other tenants (e.g. BILLING_CONTACT, PRIMARY_TENANT_CONTACT) stay sales-data
# only here; contractors create WP via the portal form when granted platform
# CAN_RAISE. See 3638.
# ---------------------------------------------------------------------------

ROLE_PERMISSION_MAP: dict[str, frozenset[str]] = {
    "MALL_MANAGER": frozenset({
        "CAN_RAISE_HANDOVER_SR",
        "VIEW_FIT_OUT_HANDOVER",
        "CAN_RAISE_WORK_PERMIT_SR",
        "VIEW_WORK_PERMIT",
    }),
    "FM_MANAGER": frozenset({
        "CAN_FM_REVIEW_HANDOVER_SR",
        "CAN_APPROVE_FM_HANDOVER_SR",
        "VIEW_FIT_OUT_HANDOVER",
        "VIEW_WORK_PERMIT",
    }),
    "OPERATIONS": frozenset({
        "CAN_FM_REVIEW_HANDOVER_SR",
        "CAN_APPROVE_FM_HANDOVER_SR",
        "VIEW_FIT_OUT_HANDOVER",
        "VIEW_WORK_PERMIT",
    }),
    # Cold L2 Work Permit hop (workflow 6). No create permission.
    "OPERATION_EXECUTIVES": frozenset({
        "VIEW_WORK_PERMIT",
    }),
    "DD_ENGINEER": frozenset({
        "CAN_RDD_REVIEW_HANDOVER_SR",
        "VIEW_FIT_OUT_HANDOVER",
        "VIEW_WORK_PERMIT",
    }),
    "ADMIN": frozenset({
        "CAN_RAISE_HANDOVER_SR",
        "CAN_FM_REVIEW_HANDOVER_SR",
        "CAN_APPROVE_FM_HANDOVER_SR",
        "CAN_RDD_REVIEW_HANDOVER_SR",
        "VIEW_FIT_OUT_HANDOVER",
        "CAN_RAISE_WORK_PERMIT_SR",
        "VIEW_WORK_PERMIT",
        "SALES_AGENT",
        "SALES_DATA_UPDATE",
        "UPLOAD_SALES_DATA",
        "REUPLOAD_SALES_DATA",
        "SALES_DATA_APPROVE",
    }),
}

# SALES_AGENT only (no write permission) — company-level contacts who can
# open the sales-data agent, see pending items, and navigate the flow, but
# are denied at the actual submit/approve step by the write-permission sets
# below, same as on the portal.
_SALES_AGENT_ONLY_ROLES: frozenset[str] = frozenset({"SHOP_MANAGER", "FINANCIAL_CONTROLLER"})
for _role in _SALES_AGENT_ONLY_ROLES:
    ROLE_PERMISSION_MAP[_role] = ROLE_PERMISSION_MAP.get(_role, frozenset()) | frozenset(
        {"SALES_AGENT"}
    )

# Platform CSV submit (POST /sales-data) — SALES_DATA_UPDATE only.
# FINANCIAL_CONTROLLER / SHOP_MANAGER are company-level contacts but do not
# get this permission on the portal; they are omitted on purpose.
_SALES_DATA_CSV_ROLES: frozenset[str] = frozenset(
    {
        "TENANT_CSV_UPLOADER",
        "TENANT_DOC_UPLOADER",
        "BILLING_CONTACT",
        "LEASING_MANAGER",
        "PRIMARY_TENANT_CONTACT",
        "AUTHORIZED_SIGNATORY",
        "COMPANY_ADMIN_CENTRAL",
        "COMPANY_ADMIN",
    }
)
# Platform PATCH supporting-doc / re-upload file — UPLOAD_SALES_DATA.
# Billing Contact and Leasing Manager have SALES_DATA_UPDATE only.
_SALES_DATA_DOC_ROLES: frozenset[str] = frozenset(
    {
        "TENANT_DOC_UPLOADER",
        "PRIMARY_TENANT_CONTACT",
        "AUTHORIZED_SIGNATORY",
        "COMPANY_ADMIN_CENTRAL",
        "COMPANY_ADMIN",
    }
)
_SALES_DATA_APPROVE_ROLES: frozenset[str] = frozenset(
    {"SALES_DATA_APPROVER", "SALES_DATA_TEAM"}
)

_SALES_CSV_PERMS = frozenset({"SALES_AGENT", "SALES_DATA_UPDATE"})
_SALES_DOC_PERMS = frozenset(
    {"SALES_AGENT", "SALES_DATA_UPDATE", "UPLOAD_SALES_DATA", "REUPLOAD_SALES_DATA"}
)
_SALES_APPROVE_PERMS = frozenset({"SALES_AGENT", "SALES_DATA_APPROVE"})

for _role in _SALES_DATA_CSV_ROLES:
    ROLE_PERMISSION_MAP[_role] = ROLE_PERMISSION_MAP.get(_role, frozenset()) | _SALES_CSV_PERMS
for _role in _SALES_DATA_DOC_ROLES:
    ROLE_PERMISSION_MAP[_role] = ROLE_PERMISSION_MAP.get(_role, frozenset()) | _SALES_DOC_PERMS
for _role in _SALES_DATA_APPROVE_ROLES:
    ROLE_PERMISSION_MAP[_role] = (
        ROLE_PERMISSION_MAP.get(_role, frozenset()) | _SALES_APPROVE_PERMS
    )

# Tenant company admins / authorized signatories: WP create + view (merge with
# sales-data perms already assigned above).
_TENANT_WORK_PERMIT_CREATE_ROLES: frozenset[str] = frozenset(
    {
        "COMPANY_ADMIN_CENTRAL",
        "COMPANY_ADMIN",
        "AUTHORIZED_SIGNATORY",
    }
)
_TENANT_WP_CREATE_PERMS = frozenset({"CAN_RAISE_WORK_PERMIT_SR", "VIEW_WORK_PERMIT"})
for _role in _TENANT_WORK_PERMIT_CREATE_ROLES:
    ROLE_PERMISSION_MAP[_role] = (
        ROLE_PERMISSION_MAP.get(_role, frozenset()) | _TENANT_WP_CREATE_PERMS
    )

# Same roles that may start a General Inquiry may attach its optional file.
# Tenant contacts do not hold CAN_RAISE_HANDOVER_SR, so the upload route must
# not reuse the handover action for SR_GEN_INQ.
from app.agents.schemas.help_agent_schema import ROLE_PERMITTED_INTENTS  # noqa: E402

_GI_CREATE_PERMS = frozenset({"CAN_RAISE_GENERAL_INQUIRY"})
for _role, _intents in ROLE_PERMITTED_INTENTS.items():
    if "CREATE_GENERAL_INQUIRY" in _intents:
        ROLE_PERMISSION_MAP[_role] = (
            ROLE_PERMISSION_MAP.get(_role, frozenset()) | _GI_CREATE_PERMS
        )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PermissionDeniedError(Exception):
    """Raised when a caller attempts an action they are not authorised for.

    Attributes
    ----------
    action:         The action that was attempted.
    required_role:  The permission string that was missing from the caller's
                    roles frozenset.
    """

    def __init__(self, action: str, required_role: str) -> None:
        self.action = action
        self.required_role = required_role
        super().__init__(
            f"Permission denied: action '{action}' requires role '{required_role}'."
        )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PermissionService:
    """Evaluate whether a caller may perform a named action.

    All enforcement goes through :meth:`check`.  Convenience wrappers
    (``ensure_can_*``) are provided for existing call-sites.
    """

    # ------------------------------------------------------------------
    # Core check
    # ------------------------------------------------------------------

    def check(self, action: str, auth: AuthContext) -> None:
        """Assert that *auth* holds the permission required for *action*.

        Raises :class:`PermissionDeniedError` when the required permission is
        absent from ``auth.roles``.

        ``auth.roles`` may contain either:
        - Permission strings (e.g. ``"CAN_FM_REVIEW_HANDOVER_SR"``) — used in
          tests and graph nodes that build AuthContext directly.
        - Role names (e.g. ``"FM_MANAGER"``) — used when the JWT from the login
          endpoint populates ``auth.roles`` from the ``roles`` JWT claim.

        Both forms are handled: role names are expanded to their permission sets
        via ``ROLE_PERMISSION_MAP`` before the check.

        Unknown actions (not present in ``ACTION_PERMISSION_MAP``) are
        **fail-closed**: an unrecognised action raises ``PermissionDeniedError``
        with ``required_role="UNKNOWN_ACTION"``.  This prevents unclassified
        paths from silently bypassing permission enforcement.
        """
        if auth.is_global_admin:
            return

        required = ACTION_PERMISSION_MAP.get(action)
        if required is None:
            raise PermissionDeniedError(action=action, required_role="UNKNOWN_ACTION")

        # Fast path: permission string is directly present
        if required in auth.roles:
            return

        # Expand role names to permission strings (JWT populates roles by name)
        for role_name in auth.roles:
            if required in ROLE_PERMISSION_MAP.get(role_name, frozenset()):
                return

        raise PermissionDeniedError(action=action, required_role=required)

    # ------------------------------------------------------------------
    # Convenience wrappers
    # ------------------------------------------------------------------

    def ensure_can_create_request(self, auth: AuthContext) -> None:
        """Assert the caller may raise a new Handover Service Request."""
        self.check("CREATE_HANDOVER_SR", auth)

    def ensure_can_approve_request(self, action: str, auth: AuthContext) -> None:
        """Assert the caller may approve at the given stage.

        *action* should be one of ``"FM_APPROVAL"`` or ``"RDD_FINAL_APPROVAL"``.
        """
        self.check(action, auth)

    def ensure_can_fm_review(self, auth: AuthContext) -> None:
        """Assert the caller may upload FM documents and save FM progress."""
        self.check("SAVE_FM_HANDOVER_PROGRESS", auth)

    def ensure_can_approve_fm(self, auth: AuthContext) -> None:
        """Assert the caller may approve the FM review stage."""
        self.check("APPROVE_FM_HANDOVER", auth)

    def ensure_can_rdd_review(self, auth: AuthContext) -> None:
        """Assert the caller may upload and submit the RDD handover report."""
        self.check("SUBMIT_RDD_HANDOVER_REPORT", auth)

    def ensure_can_view_trace(self, auth: AuthContext) -> None:
        """Assert the caller may read handover SR records."""
        self.check("VIEW_HANDOVER_SR", auth)

    def ensure_can_create_work_permit(self, auth: AuthContext) -> None:
        """Assert the caller may raise a new Work Permit Service Request."""
        self.check("CREATE_WORK_PERMIT_SR", auth)

    def can_approve_work_permit(
        self,
        auth: AuthContext,
        *,
        workflow_stage: str | None = None,
        in_progress_roles: set[str] | frozenset[str] | None = None,
    ) -> bool:
        """True when the caller may PATCH approve/reject the current WP hop."""
        try:
            self.ensure_can_approve_work_permit(
                auth,
                workflow_stage=workflow_stage,
                in_progress_roles=in_progress_roles,
            )
        except PermissionDeniedError:
            return False
        return True

    def ensure_can_approve_work_permit(
        self,
        auth: AuthContext,
        *,
        workflow_stage: str | None = None,
        in_progress_roles: set[str] | frozenset[str] | None = None,
    ) -> None:
        """Assert the caller may PATCH approve/reject the current WP hop.

        Platform rule: the user's role must match an IN_PROGRESS operation's
        ``assigned_role``. There is no ``APPROVE_SR_WORK_PERMIT`` permission.
        """
        from app.agents.schemas.work_permit_schema import WP_REVIEW_ROLES, normalize_wp_role

        expanded: set[str] = {r.upper() for r in auth.roles}
        for role_name in list(expanded):
            expanded |= {p.upper() for p in ROLE_PERMISSION_MAP.get(role_name, frozenset())}
        if "ADMIN" in expanded or auth.is_global_admin:
            return

        allowed: set[str] = set()
        if in_progress_roles:
            allowed = {normalize_wp_role(r) for r in in_progress_roles}
        elif workflow_stage:
            allowed = set(WP_REVIEW_ROLES.get(workflow_stage or "", frozenset()))

        if expanded & allowed:
            return
        raise PermissionDeniedError(
            action="APPROVE_WORK_PERMIT_SR",
            required_role="IN_PROGRESS_ASSIGNED_ROLE",
        )

    def ensure_can_use_sales_data_agent(self, auth: AuthContext) -> None:
        """Assert the caller may even open the Sales Data agent flow.

        The front-door gate — SALES_AGENT — checked once per turn, before
        the entry node routes to any sub-flow. Distinct from the finer
        SUBMIT_*/APPROVE_SALES_DATA checks, which still apply per-action.
        """
        self.check("ENTER_SALES_DATA_AGENT", auth)

    def ensure_can_submit_sales_data(self, auth: AuthContext) -> None:
        """Assert the caller may POST CSV / manual sales data."""
        self.check("SUBMIT_SALES_DATA", auth)

    def ensure_can_submit_sales_data_supporting_doc(self, auth: AuthContext) -> None:
        """Assert the caller may PUT files + PATCH a NEED_* sales-data row."""
        self.check("SUBMIT_SALES_DATA_SUPPORTING_DOC", auth)

    def ensure_can_approve_sales_data(self, auth: AuthContext) -> None:
        """Assert the caller may PATCH admin sales-data statuses."""
        self.check("APPROVE_SALES_DATA", auth)


def graph_permission_guard(state: Any, action: str) -> dict[str, Any] | None:
    """RDD-style mutate-node guard.

    Returns a user-facing denial patch when ``auth_context`` is present and
    the action is not allowed. Returns ``None`` when there is no auth context
    (tests / unauthenticated shadow mode) or the check passes.
    """
    auth = state.get("auth_context") if isinstance(state, dict) else None
    if auth is None:
        return None
    try:
        PermissionService().check(action, auth)
    except PermissionDeniedError as exc:
        return {
            "status": "WAITING_FOR_USER",
            "response_message": f"Permission denied: {exc}",
            "response_ui": {"type": "message"},
        }
    return None
