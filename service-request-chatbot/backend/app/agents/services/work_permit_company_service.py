"""Lease-verified tenant company name for the Work Permit summary card.

Drafts loaded from the platform carry no readable company: the form's
``company_name`` is the tenant profile id and the SR payload has no name.
``with_wp_company`` looks the lease up once by lease code and stores the name
in ``company`` with a ``company_lease_code`` marker, so it is only trusted for
the lease it was resolved for.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.services.lease_lookup_service import (
    LeaseLookupQuery,
    get_lease_lookup_service,
    normalise_lease_code,
)
from app.agents.services.work_permit_review_summary import wp_lease_code

log = structlog.get_logger(__name__)


def _log_unresolved(
    outcome: str,
    *,
    status_code: int | None = None,
    reason: str | None = None,
    match_count: int = 0,
) -> None:
    log.info(
        "wp_company.unresolved",
        outcome=outcome,
        status_code=status_code,
        reason=reason,
        match_count=match_count,
    )


async def with_wp_company(collected: dict[str, Any]) -> dict[str, Any]:
    """Return *collected* with a lease-verified ``company``, when one can be found.

    Returns the same object when nothing changes (already verified, no lease
    code, lookup error, no match or ambiguous match) and a new dict only when
    ``company`` / ``company_lease_code`` were added or replaced. Never writes
    ``company_name`` — the form binds it to the tenant profile id.
    """
    lease_code = wp_lease_code(collected)
    if not lease_code:
        return collected
    verified = normalise_lease_code(collected.get("company_lease_code")) == lease_code
    if verified and str(collected.get("company") or "").strip():
        return collected

    try:
        result = await get_lease_lookup_service().lookup(LeaseLookupQuery(lease_code=lease_code))
    except Exception as exc:  # noqa: BLE001 — the card just omits Company
        _log_unresolved("error", reason=type(exc).__name__)
        return collected
    if result.error:
        _log_unresolved("error", status_code=result.status_code, reason=result.reason)
        return collected

    matching = [m for m in result.matches if normalise_lease_code(m.lease_code) == lease_code]
    names = {m.company_name.strip() for m in matching if m.company_name.strip()}
    if len(names) != 1:
        _log_unresolved(
            "ambiguous" if names else "no_match",
            status_code=result.status_code,
            reason=result.reason,
            match_count=len(matching),
        )
        return collected
    return {**collected, "company": names.pop(), "company_lease_code": lease_code}
