"""Suggested RDD milestone dates, derived from the SR's expected handover date.

The RDD Project Manager records what actually happened: the handover date, the
fit-out window, and (derived) the trading date. Nobody can guess an actual, but
the plan is known — so the quick picks offer the policy dates and the reviewer
overtypes whichever ones reality moved.

The policy is the platform's, and so is the arithmetic. ``cenomi.leases`` holds
four ``suggested_*`` columns written by a trigger, but 1,515 of the populated
rows disagree with the documented rule — one suggests a handover a week before
its lease starts — so the values are recomputed here from the same rule, using
the platform's own ``cenomi.add_working_days``. Should those columns be
corrected at source, this becomes a plain column read.

Anchored on the SR's ``expected_handover_date`` (set at FM review as unit
readiness + 7) rather than the lease start date: it is this request's own
number, and the reviewer has already seen it.

    actual_handover_date  = expected_handover_date
    fitout_start_date     = expected handover + 14 working days
    fitout_end_date       = expected handover + free_period calendar days
    trading_date          = derived downstream (fit-out end + 8 days)

Only 2,016 of 14,203 leases carry a free period, so the fit-out end falls back
to the lease's own planned or contract fit-out end when the agreed period is
zero. When neither is set — the common case — no end date is offered rather
than one invented from a rule nobody wrote down, and the reviewer is asked for
it on its own.

Working days are Sunday-Thursday: the Saudi weekend is Friday and Saturday.
Read-only, best effort, and never load-bearing — no platform database, no lease
row, or no expected handover date simply means fewer suggestions, and the
question is asked without them.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Fit-out starts this many working days after the handover.
FITOUT_START_WORKING_DAYS = 14

# One statement so the working-day arithmetic stays with the platform that
# defines it. free_period is the agreed rent-free / fit-out period in days.
_SUGGEST_SQL = """
    SELECT
        cenomi.add_working_days(:anchor, :fitout_start_wd)::date
            AS fitout_start_date,
        CASE
            WHEN COALESCE(free_period, 0) > 0
                THEN (:anchor + (free_period * INTERVAL '1 day'))::date
            ELSE COALESCE(planned_fitout_end_date, base_fitout_end_date)::date
        END AS fitout_end_date,
        COALESCE(free_period, 0) AS free_period
    FROM cenomi.leases
    WHERE lease_id = :lease_id
    LIMIT 1
"""

_SUGGEST_SQL_BY_CODE = _SUGGEST_SQL.replace(
    "WHERE lease_id = :lease_id", "WHERE pms_tenant_code = :lease_code"
)


def _to_date(value: Any) -> date | None:
    """Parse a stored date value into a ``date``; ``None`` when unusable.

    The driver binds a timestamp parameter from a real date object, never from
    a string, so the anchor has to be parsed before it reaches the query.
    """
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _as_iso_date(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "").strip()[:10]


async def suggest_rdd_dates(collected: dict[str, Any]) -> dict[str, str]:
    """Return suggested ``{actual_handover_date, fitout_start_date, fitout_end_date}``.

    Empty when there is nothing trustworthy to suggest.
    """
    from app.core.config import get_settings

    anchor_date = _to_date(collected.get("expected_handover_date"))
    if anchor_date is None:
        # Nothing to anchor on — FM has not set a readiness date yet.
        return {}
    anchor = anchor_date.isoformat()

    url = get_settings().cenomi_database_url
    if not url:
        log.debug("rdd_suggestions.skipped", reason="cenomi_database_url not set")
        # The handover date alone is still worth offering.
        return {"actual_handover_date": anchor}

    lease_id = collected.get("lease_id")
    lease_code = str(collected.get("lease_code") or "").strip()
    if not lease_id and not lease_code:
        return {"actual_handover_date": anchor}

    from app.indexer.readers.pg_reader import CenomiPgReader

    reader = CenomiPgReader(url)
    try:
        rows: list[dict[str, Any]] = []
        if lease_id:
            rows = await reader.fetch_all(
                _SUGGEST_SQL,
                {
                    "anchor": anchor_date,
                    "fitout_start_wd": FITOUT_START_WORKING_DAYS,
                    "lease_id": int(lease_id),
                },
            )
        if not rows and lease_code:
            rows = await reader.fetch_all(
                _SUGGEST_SQL_BY_CODE,
                {
                    "anchor": anchor_date,
                    "fitout_start_wd": FITOUT_START_WORKING_DAYS,
                    "lease_code": lease_code,
                },
            )
    except Exception:
        log.warning("rdd_suggestions.query_failed", exc_info=True)
        return {"actual_handover_date": anchor}
    finally:
        await reader.dispose()

    if not rows:
        log.info("rdd_suggestions.lease_not_found", lease_code=lease_code)
        return {"actual_handover_date": anchor}

    row = rows[0]
    suggested = {
        "actual_handover_date": anchor,
        "fitout_start_date": _as_iso_date(row.get("fitout_start_date")),
        "fitout_end_date": _as_iso_date(row.get("fitout_end_date")),
    }
    # An end that is not after the start is not a window anyone could book, and
    # would fail validation the moment it was accepted. That happens two ways:
    # a lease with no agreed free period puts the end on the handover day
    # itself, and a fallback planned end can predate the computed start. Offer
    # the rest and ask for the end on its own.
    end = suggested.get("fitout_end_date") or ""
    start = suggested.get("fitout_start_date") or ""
    if end and (end <= anchor or (start and end <= start)):
        suggested.pop("fitout_end_date")
    log.info(
        "rdd_suggestions.built",
        lease_code=lease_code,
        free_period=row.get("free_period"),
        fields=sorted(k for k, v in suggested.items() if v),
    )
    return {k: v for k, v in suggested.items() if v}


# ---------------------------------------------------------------------------
# Chips
# ---------------------------------------------------------------------------

# Each chip carries a whole answer: an outcome plus the policy dates. Nobody
# can guess an actual, so these are the plan — the reviewer taps the outcome
# and overtypes whichever date reality moved.
_STATUS_CHIPS: tuple[tuple[str, str], ...] = (
    ("Approve", "Approved"),
    ("Approve with changes", "Approved with changes"),
)


def _display(iso: str) -> str:
    parsed = _to_date(iso)
    return f"{parsed.day} {parsed:%b}" if parsed else str(iso)


def _ddmmyyyy(iso: str) -> str:
    parsed = _to_date(iso)
    return parsed.strftime("%d/%m/%Y") if parsed else str(iso)


async def build_rdd_decorations(
    collected: dict[str, Any],
    missing: set[str],
) -> dict[str, Any]:
    """``response_decorations`` for the combined RDD review question."""
    wants_status = "document_status" in missing
    date_fields = [
        f
        for f in ("actual_handover_date", "fitout_start_date", "fitout_end_date")
        if f in missing
    ]

    suggested = await suggest_rdd_dates(collected) if date_fields else {}
    offered = {f: suggested[f] for f in date_fields if suggested.get(f)}

    options: list[dict[str, Any]] = []

    if wants_status and offered:
        # The whole question in one tap.
        parts = []
        if "actual_handover_date" in offered:
            parts.append(f"handover {_display(offered['actual_handover_date'])}")
        window = [
            _display(offered[f])
            for f in ("fitout_start_date", "fitout_end_date")
            if f in offered
        ]
        if window:
            parts.append("fit-out " + " → ".join(window))
        detail = ", ".join(parts)
        for label, word in _STATUS_CHIPS:
            value = f"{word}. " + ", ".join(
                f"{name} {_ddmmyyyy(offered[key])}"
                for key, name in (
                    ("actual_handover_date", "handover"),
                    ("fitout_start_date", "fitout start"),
                    ("fitout_end_date", "fitout end"),
                )
                if key in offered
            )
            options.append({"label": f"{label} · {detail}", "value": value})
        return {"suggestions": {"kind": "labeled_options", "options": options}}

    if wants_status:
        # No dates to offer — the outcome chips alone, with their direct-set
        # actions, so a tap resolves without an extraction round-trip.
        return {
            "suggestions": {
                "kind": "labeled_options",
                "options": [
                    {
                        "label": "Approve",
                        "value": "APPROVED",
                        "action": "select_rdd_status_approved",
                    },
                    {
                        "label": "Approve with changes",
                        "value": "APPROVED_WITH_CHANGES",
                        "action": "select_rdd_status_approved_with_changes",
                    },
                ],
            }
        }

    if offered:
        # Outcome settled; offer the remaining dates as one chip.
        value = ", ".join(
            f"{name} {_ddmmyyyy(offered[key])}"
            for key, name in (
                ("actual_handover_date", "handover"),
                ("fitout_start_date", "fitout start"),
                ("fitout_end_date", "fitout end"),
            )
            if key in offered
        )
        label = ", ".join(
            f"{name} {_display(offered[key])}"
            for key, name in (
                ("actual_handover_date", "handover"),
                ("fitout_start_date", "fit-out start"),
                ("fitout_end_date", "fit-out end"),
            )
            if key in offered
        )
        options.append({"label": label, "value": value})

    if not options:
        return {}
    return {"suggestions": {"kind": "labeled_options", "options": options}}
