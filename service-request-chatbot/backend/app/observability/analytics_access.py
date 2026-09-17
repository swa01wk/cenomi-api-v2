"""Access control for the AI Command Center API.

The page is gated on the platform permission ``VIEW_ASSISTANT_ANALYTICS``.
This mirrors that gate on the backend, following the app's RBAC mode:

* a caller with a valid token must hold a role granted the permission on the
  platform (``roles.permission_list``) — otherwise ``403``;
* no token / invalid token: ``401`` when ``RBAC_ENFORCE`` is on, allowed in
  shadow mode (the internal-token guard still applies), same as every other
  route;
* platform roles unreadable: ``503`` when enforcing, allowed in shadow mode.
"""

from __future__ import annotations

import structlog
from fastapi import Depends, HTTPException, status

from app.core.config import settings
from app.core.security import get_auth_context
from app.observability.platform_stats import roles_with_permission
from app.types.chat import AuthContext

log = structlog.get_logger(__name__)

ANALYTICS_PERMISSION = "VIEW_ASSISTANT_ANALYTICS"
_ANONYMOUS = ("anonymous", "unauthenticated", "")


async def require_analytics_viewer(
    auth: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    """Allow callers whose platform role grants ``VIEW_ASSISTANT_ANALYTICS``."""
    if auth.subject_id in _ANONYMOUS:
        if settings.rbac_enforce:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Sign in to view assistant analytics.",
            )
        return auth

    allowed = await roles_with_permission(ANALYTICS_PERMISSION)
    if allowed is None:
        log.warning("analytics_access.roles_unavailable", subject=auth.subject_id)
        if settings.rbac_enforce:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not check analytics access. Please try again.",
            )
        return auth

    if allowed.intersection(auth.roles):
        return auth
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have access to assistant analytics.",
    )
