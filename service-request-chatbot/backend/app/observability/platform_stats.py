"""Read-only platform facts the AI Command Center needs.

Two questions only the platform database (``CENOMI_DATABASE_URL``) can answer:

* how many service requests were created in a window, and how many of them
  the AI assistant created (``service_request.created_via``, platform
  migration 1789482400);
* which platform roles hold a permission (``roles.permission_list``) — used to
  gate the Command Center API on ``VIEW_ASSISTANT_ANALYTICS``.

Both degrade quietly: when the platform database is not configured, not
reachable, or not yet migrated, callers get ``None`` and decide what to do.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import structlog

from app.core.config import settings

log = structlog.get_logger(__name__)

# The flows the assistant runs, as platform (category, sub_category).
AI_FLOW_SUB_CATEGORIES: dict[str, str] = {
    "HANDOVER": "handover",
    "HANDED_OVER": "handover",
    "FIT_OUT_CONFIRM_FOS": "fitout_start",
    "FIT_OUT_CONFIRM_END": "fitout_end",
    "FIT_OUT_CONFIRM_TRADING": "trading_confirmation",
}

_reader: Any = None
_ROLES_TTL_SECONDS = 300.0
_roles_cache: dict[str, tuple[float, frozenset[str]]] = {}


def _get_reader() -> Any:
    """One pooled reader per process, created on first use."""
    global _reader
    if not settings.cenomi_database_url:
        return None
    if _reader is None:
        from app.indexer.readers.pg_reader import CenomiPgReader

        _reader = CenomiPgReader(settings.cenomi_database_url)
    return _reader


_AI_SHARE_SQL = """
    SELECT sr.sub_category,
           count(*) AS total,
           count(*) FILTER (WHERE sr.created_via = 'AI_ASSISTANT') AS ai
    FROM cenomi.service_request sr
    WHERE sr.created_at >= :start AND sr.created_at <= :end
      AND sr.deleted_at IS NULL
      AND sr.sub_category IN :sub_categories
    GROUP BY sr.sub_category
"""


async def ai_share_of_service_requests(start: datetime, end: datetime) -> dict[str, Any] | None:
    """SRs created on the platform in the assistant's flows, and the AI share.

    Scoped to the sub-categories the assistant can create, so the share is
    "of the requests the assistant could have raised, how many did it raise".
    Returns ``None`` when the platform data is unavailable (including before
    the ``created_via`` migration).
    """
    reader = _get_reader()
    if reader is None:
        return None
    try:
        rows = await reader.fetch_all(
            _AI_SHARE_SQL,
            {"start": start, "end": end, "sub_categories": tuple(AI_FLOW_SUB_CATEGORIES)},
        )
    except Exception as exc:
        log.warning("platform_stats.ai_share_unavailable", error=str(exc)[:200])
        return None

    by_function: dict[str, dict[str, int]] = {}
    for row in rows:
        function = AI_FLOW_SUB_CATEGORIES.get(row["sub_category"], "other")
        bucket = by_function.setdefault(function, {"total": 0, "ai": 0})
        bucket["total"] += int(row["total"] or 0)
        bucket["ai"] += int(row["ai"] or 0)
    total = sum(b["total"] for b in by_function.values())
    ai = sum(b["ai"] for b in by_function.values())
    return {
        "total": total,
        "ai": ai,
        "by_function": [
            {"function": function, "total": b["total"], "ai": b["ai"]}
            for function, b in sorted(by_function.items(), key=lambda kv: -kv[1]["total"])
        ],
    }


_ROLES_WITH_PERMISSION_SQL = """
    SELECT r.role_name
    FROM cenomi.roles r
    JOIN cenomi.permissions p ON p.permission = :permission
    WHERE p.permission_id = ANY(COALESCE(r.permission_list, '{}'))
"""


async def roles_with_permission(permission: str) -> frozenset[str] | None:
    """Platform role names granted ``permission``; cached for five minutes."""
    now = time.monotonic()
    hit = _roles_cache.get(permission)
    if hit is not None and hit[0] > now:
        return hit[1]
    reader = _get_reader()
    if reader is None:
        return None
    try:
        rows = await reader.fetch_all(_ROLES_WITH_PERMISSION_SQL, {"permission": permission})
    except Exception as exc:
        log.warning("platform_stats.roles_unavailable", error=str(exc)[:200])
        return None
    roles = frozenset(str(r["role_name"]) for r in rows if r.get("role_name"))
    _roles_cache[permission] = (now + _ROLES_TTL_SECONDS, roles)
    return roles


def reset_caches() -> None:
    """Drop cached role grants — used by tests."""
    _roles_cache.clear()
