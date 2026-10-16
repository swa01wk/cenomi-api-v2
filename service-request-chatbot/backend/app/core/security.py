"""Authentication and token utilities."""

from __future__ import annotations

import logging
from typing import Any

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from app.core.auth import decode_access_token
from app.core.config import settings
from app.types.chat import AuthContext

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)

security_scheme = HTTPBearer(auto_error=False)


def get_auth_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
) -> AuthContext:
    """Resolve caller identity from the bearer JWT.

    Shadow mode (``RBAC_ENFORCE=false``, default):
        No token → anonymous AuthContext (empty roles, no scoping).
        Invalid/expired token → warn log, anonymous AuthContext.

    Enforce mode (``RBAC_ENFORCE=true``):
        No token → HTTP 401.
        Invalid/expired token → HTTP 401.
    """
    if credentials is None or not credentials.credentials:
        if settings.rbac_enforce:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required. Please log in.",
            )
        return AuthContext(subject_id="anonymous")

    raw_token = credentials.credentials
    try:
        payload = decode_access_token(raw_token)
    except JWTError as exc:
        if settings.rbac_enforce:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token. Please log in again.",
            ) from exc
        logger.warning("security.invalid_jwt: %s", exc)
        # Preserve the raw Bearer so platform lease lookup can still authenticate
        # when chatbot JWT_SECRET_KEY and platform AI_SESSION_TOKEN_SECRET diverge.
        return AuthContext(subject_id="unauthenticated", bearer_token=raw_token)

    tenant_raw = payload.get("tenant_id")
    if tenant_raw in (None, ""):
        tenant_raw = payload.get("tenant_profile_id")
    # A claim present as JSON null is not the same as a missing key.
    # dict.get returns that null, and tuple(None) raises TypeError.
    roles = _string_list(payload.get("roles"))
    mall_names = _string_list(payload.get("mall_names"))
    property_ids: list[int] = []
    for raw in _string_list(payload.get("unique_property_ids")):
        try:
            property_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    log.info(
        "security.auth_context_resolved",
        tenant_id=str(tenant_raw) if tenant_raw not in (None, "") else None,
        roles=roles,
    )
    return AuthContext(
        subject_id=str(payload.get("user_id", payload.get("sub", "unknown"))),
        tenant_id=str(tenant_raw) if tenant_raw not in (None, "") else None,
        roles=frozenset(roles),
        unique_property_ids=tuple(property_ids),
        mall_names=tuple(mall_names),
        is_global_admin=bool(payload.get("is_global_admin", False)),
        first_name=str(payload.get("first_name") or "").strip() or None,
        bearer_token=raw_token,
    )


def _string_list(value: Any) -> list[str]:
    """Return a list of strings. Null or a non-list claim becomes empty."""
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if item is not None]


def require_bearer(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
) -> str:
    """Require a bearer token; raise HTTP 401 if absent."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    return credentials.credentials


def redact_secrets(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove sensitive fields before logging or persisting debug views."""
    blocked = {"password", "token", "authorization", "secret", "api_key", "password_hash"}
    return {k: v for k, v in payload.items() if k.lower() not in blocked}
