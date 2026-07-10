"""Request-scoped auth context for outbound platform HTTP calls.

Set once per chat turn in ``ChatOrchestrationService.process_turn`` so HTTP
adapters (``ServiceRequestPlatformClient``, ``HttpLeaseLookupService``) can
forward the inbound AI session Bearer without threading tokens through nodes.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from app.types.chat import AuthContext

_current_auth: ContextVar[AuthContext | None] = ContextVar(
    "current_auth_context",
    default=None,
)


def set_current_auth_context(auth: AuthContext | None) -> Token:
    """Bind *auth* to the current async task; return a reset token."""
    return _current_auth.set(auth)


def reset_current_auth_context(token: Token) -> None:
    """Restore the prior auth context after a chat turn."""
    _current_auth.reset(token)


def get_current_auth_context() -> AuthContext | None:
    """Return the active ``AuthContext`` for this async task, if any."""
    return _current_auth.get()


def get_current_bearer_token() -> str | None:
    """Return the inbound AI session JWT for this async task, if present."""
    auth = _current_auth.get()
    if auth is None:
        return None
    raw = auth.bearer_token
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None
