"""Centralised authorization checks for graph transitions and API calls.

Permission model
----------------
Each *action* string (e.g. ``"CREATE_HANDOVER_SR"``) maps to a required
*permission string* that the caller's ``AuthContext.roles`` frozenset must
contain.  If the caller does not hold the required permission a
``PermissionDeniedError`` is raised — never silently allowed.

POC note: ``AuthContext.roles`` is currently populated from the stub
``get_auth_context`` in ``core/security.py``.  When real JWT validation is
wired the roles frozenset will be populated from token claims and this
service will enforce them automatically.
"""

from __future__ import annotations

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

    # ── Work Permit: Read / view ──────────────────────────────────────────────
    "VIEW_WORK_PERMIT_SR": "VIEW_WORK_PERMIT",

    # ── Legacy entries (kept for backward compatibility) ──────────────────────
    "FM_APPROVAL": "CAN_APPROVE_HANDOVER_SR",
    "RDD_FINAL_APPROVAL": "CAN_APPROVE_HANDOVER_SR",
}

# ---------------------------------------------------------------------------
# Role permission map — role → frozenset of permission strings
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
    }),
}


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
