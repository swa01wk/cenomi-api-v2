"""Who conducts a ceiling inspection — read from the platform form.

The OCI / CCI form shows "Inspection conducted by" from its own skeleton
(field ``inspection_done_by``, a ReadOnlyRoleField holding a role id). The
assistant reads the same value so its summary card and the submitted payload
match the form, instead of assuming RDD PM.
"""

from __future__ import annotations

import time

import structlog

from app.agents.schemas import ceiling_schema as cs

log = structlog.get_logger(__name__)

_FIELD_NAME = "inspection_done_by"
# The form config changes only with a platform deploy or migration.
_CACHE_TTL_S = 300.0
_cache: dict[str, tuple[float, int]] = {}


async def resolve_inspection_done_by(sub_category: str | None) -> int:
    """Role id of the ceiling form's "Inspection conducted by" field.

    Falls back to ``INSPECTION_DONE_BY_ROLE_ID`` when the form can't be read.
    Only successful reads are cached, so a transient failure retries next turn.
    """
    if not sub_category or not cs.is_resolved_sub_category(sub_category):
        return cs.INSPECTION_DONE_BY_ROLE_ID

    cached = _cache.get(sub_category)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    from app.agents.services.service_request_api_service import (
        get_service_request_api_service,
    )

    raw = await get_service_request_api_service().get_form_field_value(
        cs.SERVICE_CATEGORY, sub_category, _FIELD_NAME
    )
    try:
        role_id = int(raw)
    except (TypeError, ValueError):
        return cs.INSPECTION_DONE_BY_ROLE_ID
    if role_id <= 0:
        return cs.INSPECTION_DONE_BY_ROLE_ID

    _cache[sub_category] = (time.monotonic() + _CACHE_TTL_S, role_id)
    log.info("ceiling.inspection_done_by.resolved", sub_category=sub_category, role_id=role_id)
    return role_id


def clear_cache() -> None:
    """Forget cached form values (tests)."""
    _cache.clear()
