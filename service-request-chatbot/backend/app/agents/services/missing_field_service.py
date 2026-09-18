"""Missing field detection utilities for the handover workflow.

Provides:
- ``get_missing_fields``: standalone function for detecting absent/empty fields.
- ``HANDOVER_FIELD_QUESTIONS``: mapping of field names to user-facing questions.
- ``MissingFieldService``: thin class kept for backward compatibility.
"""

from __future__ import annotations

from app.types.service_request import ValidationIssueDTO

# ---------------------------------------------------------------------------
# User-facing questions map
# ---------------------------------------------------------------------------

HANDOVER_FIELD_QUESTIONS: dict[str, str] = {
    # ── CREATE_SR — Mall Manager ──────────────────────────────────────────────
    "lease_code": "Ask the user for the lease code for this handover request.",
    "lease_brand_mall": (
        "Ask the user for the lease code, brand name, company/tenant name, or mall name "
        "so you can look up the lease."
    ),
    "company": (
        "Ask the user for the tenant company name to search for matching leases."
    ),
    "title": (
        "Ask the user for a short title for this handover request. "
        "It is OPTIONAL — they can reply 'skip' or 'none' and the system "
        "will auto-generate one from the lease code and description."
    ),
    "description": (
        "Ask the user for a short description of this handover request "
        "(or say 'none' to skip)."
    ),
    "startDate": (
        "Ask the user when the inspection should start — date and time together. "
        "Instruct them to use DD/MM/YYYY format and include the time, "
        "e.g. '20/08/2026 9:00 AM' or '20 Aug 2:00 PM'. "
        "If they provide the time here, no separate time question will be needed."
    ),
    "endDate": (
        "Ask the user when the inspection should end — date AND time together. "
        "Instruct them to use DD/MM/YYYY format and include the time, "
        "e.g. '21/08/2026 6:00 PM'. "
        "Do NOT tell them the time is optional and do NOT say the start time "
        "will be reused — if they omit the time they will be asked for it next."
    ),
    "inspectionTime": (
        "The user gave a start date but no time. Ask what time the inspection "
        "should start on that date (e.g. '10:00 AM', '14:30'). "
        "Ask ONLY for the time — do not re-ask for the date, and do not move on "
        "to any other field. "
        "Mention they can reply 'skip' to use 9:00 AM as the default."
    ),
    "inspectionEndTime": (
        "The user gave an end date but no time. Ask what time the inspection "
        "should end on that date (e.g. '6:00 PM', '18:30'). "
        "Ask ONLY for the time — do not re-ask for the date, and do not move on "
        "to any other field. "
        "Mention they can reply 'skip' to reuse the inspection start time."
    ),
    "inspection_done_by": (
        "Ask the user who will perform the inspection. "
        "They must choose one of exactly two options using the buttons below: "
        "'FM Manager' or 'Operations'. "
        "Do not accept any other role name — if they type anything else, "
        "ask them to use the selection buttons."
    ),
    "notes": (
        "Ask the user if they have any additional notes for this handover request. "
        "Accept 'none', 'skip', or any similar declination as a valid answer."
    ),
    "comments": (
        "Ask the user if they have any additional comments for this handover request. "
        "Accept 'no comments', 'none', 'skip', or any similar declination as a valid answer."
    ),
    # ── FM_REVIEW — FM Manager / Operations ───────────────────────────────────
    "unit_readiness_date": "Ask the FM Manager for the unit readiness date.",
    "expected_handover_date": "Ask the FM Manager for the expected handover date.",
    # ── RDD_REVIEW — RDD Project Manager ──────────────────────────────────────
    "guideLineLink": "Ask the RDD Project Manager to provide the guideline link for the handover report.",
    "rdd_upload_doc_type": (
        "Ask which document they want to upload next. "
        "They must choose one of: Handover Meeting Report, "
        "Rejected Fitout and Handover Report, or Other. "
        "Make the three choices explicit."
    ),
    "document_status": (
        "Ask the RDD Project Manager for the Handover Meeting Report status. "
        "They must choose one of exactly two options: "
        "'Approve' (APPROVED) if the report is accepted as-is, or "
        "'Approve with changes' (APPROVED_WITH_CHANGES) if it needs amendments. "
        "Make both choices explicit."
    ),
    "actual_handover_date": "Ask the RDD Project Manager for the actual handover date.",
    "fitout_start_date": "Ask the RDD Project Manager for the fitout start date.",
    "fitout_end_date": (
        "Ask the RDD Project Manager for the fitout end date. "
        "The trading start date will be automatically calculated as fitout end date + 8 days."
    ),
    # trading_date intentionally omitted — auto-computed as fitout_end_date + 8 days.
    # missing_field_node will never ask for it (also excluded via auto_generated_fields in WorkflowConfig).
    # ── CREATE_HANDED_OVER_SR — Path B (Already Trading) ──────────────────────
    "already_handovered": (
        "Ask the user which handover type they want. "
        "Options: 'Already Trading' (already_trading) — for units that are already open and trading, "
        "or 'Initiate Handover' (initiate_handover) — to start a new handover inspection flow. "
        "Make both choices explicit."
    ),
    "actual_shop_handover_date": (
        "Ask the user for the actual shop handover date (DD/MM/YYYY). "
        "This is the date the unit was physically handed over."
    ),
    "actual_fitout_start_date": (
        "Ask the user for the actual fitout start date (DD/MM/YYYY). "
        "This must be on or after the shop handover date."
    ),
    "actual_fitout_end_date": (
        "Ask the user for the actual fitout end date (DD/MM/YYYY). "
        "This must be on or after the fitout start date."
    ),
    "actual_trading_date": (
        "Ask the user for the actual trading date — the date the unit opened for business (DD/MM/YYYY). "
        "This must be on or after the fitout end date."
    ),
}

# ---------------------------------------------------------------------------
# Standalone helper
# ---------------------------------------------------------------------------


def get_missing_fields(data: dict, required_fields: list[str]) -> list[str]:
    """Return entries from *required_fields* that are absent or empty in *data*.

    A field is considered missing when any of the following hold:
    - The key does not exist in *data*
    - The value is ``None``
    - The value is an empty string (``""``) — **unless** it is in
      ``OPTIONAL_FIELDS``, where any value other than ``None`` is accepted
      (the user explicitly declined to provide it).
    - The value is an empty list (``[]``)

    Mirrors the logic in ``handover_schema.get_missing_fields`` so both
    callers produce a consistent missing-field list.
    """
    from app.agents.schemas.handover_schema import OPTIONAL_FIELDS  # local import avoids circular

    missing: list[str] = []
    for field in required_fields:
        if field not in data:
            missing.append(field)
            continue
        value = data[field]
        if value is None:
            missing.append(field)
        elif isinstance(value, str) and value == "" and field not in OPTIONAL_FIELDS:
            missing.append(field)
        elif isinstance(value, list) and len(value) == 0:
            missing.append(field)
    return missing


# ---------------------------------------------------------------------------
# Backward-compatible class
# ---------------------------------------------------------------------------


class MissingFieldService:
    def next_prompt(self, issues: list[ValidationIssueDTO]) -> str | None:
        if not issues:
            return None
        return "Ask the user to provide the missing information highlighted in the validation errors."
