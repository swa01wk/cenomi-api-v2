"""Deterministic validation for Handover Service Request drafts.

All validation is performed in code — never by the LLM.

Each individual validation function returns a ``ValidationResult`` dict (or a
list of them for multi-field checks) using the canonical shape::

    {
        "field":           str,      # field name; "_form" for cross-field rules
        "validation_type": str,      # "required" | "enum" | "date_range" | ...
        "status":          str,      # "PASSED" | "FAILED"
        "message":         str,
        "blocking":        bool,     # True → prevents confirmation and submission
    }

``ValidationService.validate_draft()`` orchestrates all applicable rules and
returns only FAILED results to populate ``state.validation_errors``.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from app.agents.schemas.handover_schema import (
    ALL_DOCUMENT_TYPES,
    FM_ALLOWED_DOCUMENTS,
    HANDED_OVER_DATE_CHAIN,
    RDD_ALLOWED_DOCUMENTS,
    RDD_REQUIRED_DOCUMENTS,
    STAGE_REGISTRY,
    role_can_act_on_stage,
)
from app.agents.schemas.work_permit_schema import (
    STAGE_REGISTRY as WP_STAGE_REGISTRY,
    VALID_WORK_PERMIT_TYPES,
)
from app.types.service_request import ValidationIssueDTO

# Minimum number of documents required at FM_REVIEW before save/approve.
_FM_MIN_DOCUMENT_COUNT = 1

logger = logging.getLogger(__name__)

# ── Canonical type alias ───────────────────────────────────────────────────────

ValidationResult = dict[str, Any]

# ── Constants ──────────────────────────────────────────────────────────────────

_INSPECTION_ALLOWED: frozenset[str] = frozenset({"FM_MANAGER", "OPERATIONS"})

# Individual date fields whose value must be parseable (DD/MM/YYYY or ISO).
# Extraction can occasionally route a non-date token (e.g. the user typing a
# lease code when asked for a date) straight into the field slot — this list
# lets validate_date_formats catch that class of bug and re-ask the user.
_SINGLE_DATE_FIELDS: tuple[str, ...] = (
    "startDate",
    "endDate",
    "unit_readiness_date",
    "expected_handover_date",
    "actual_handover_date",
    "fitout_start_date",
    "fitout_end_date",
    "trading_date",
    "actual_shop_handover_date",
    "actual_fitout_start_date",
    "actual_fitout_end_date",
    "actual_trading_date",
    # Work Permit create (snake_case — not Handover startDate/endDate).
    "start_date",
    "end_date",
)

_STAGE_DOCUMENT_ALLOWLIST: dict[str, frozenset[str]] = {
    "FM_REVIEW": frozenset(FM_ALLOWED_DOCUMENTS),
    "RDD_REVIEW": frozenset(RDD_ALLOWED_DOCUMENTS),
}

# Ordered pairs that define the RDD date chain (earlier, later).
_RDD_DATE_CHAIN: tuple[tuple[str, str], ...] = (
    ("actual_handover_date", "fitout_start_date"),
    ("fitout_start_date", "fitout_end_date"),
    ("fitout_end_date", "trading_date"),
)

# ── Internal helpers ───────────────────────────────────────────────────────────


def _result(
    field: str,
    validation_type: str,
    passed: bool,
    message: str,
    *,
    blocking: bool = True,
) -> ValidationResult:
    """Build a canonical ValidationResult dict."""
    return {
        "field": field,
        "validation_type": validation_type,
        "status": "PASSED" if passed else "FAILED",
        "message": message,
        "blocking": blocking,
    }


def _parse_date(value: Any, field_name: str) -> date | None:
    """Parse *value* as a calendar date.

    Accepts ``YYYY-MM-DD``, full ISO-8601 datetimes (with ``Z`` or offset),
    and platform form formats ``DD/MM/YYYY`` / ``DD-MM-YYYY``.
    Datetimes are truncated to their calendar date so Formik/UTC values that
    round-trip via ``corrected_fields`` still validate.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    # Fast path: date-only or datetime whose prefix is YYYY-MM-DD.
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            pass

    # Platform Handover payloads use DD/MM/YYYY (and sometimes DD-MM-YYYY).
    if len(text) >= 10 and text[2] in "/-" and text[5] in "/-":
        for fmt in ("%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(text[:10], fmt).date()
            except ValueError:
                continue

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        logger.debug(
            "Could not parse '%s' as date for field '%s'", value, field_name
        )
        return None


# ── Individual validation functions ───────────────────────────────────────────


def validate_required_fields(
    data: dict[str, Any],
    required_fields: list[str] | tuple[str, ...],
    optional_fields: frozenset[str] | None = None,
) -> list[ValidationResult]:
    """Return one ValidationResult per required field — FAILED when absent/empty.

    For non-optional fields: missing when value is ``None``, ``""``, or ``[]``.
    For optional fields: only missing when value is ``None`` — an explicit empty
    string means the user intentionally left the field blank (e.g. "no comments").
    Integer ``0`` and boolean ``False`` are treated as valid (present) values.
    """
    from app.agents.schemas.handover_schema import OPTIONAL_FIELDS as _OPTIONAL_FIELDS  # avoid circular

    _optional = optional_fields if optional_fields is not None else _OPTIONAL_FIELDS

    results: list[ValidationResult] = []
    for field_name in required_fields:
        value = data.get(field_name)
        if field_name in _optional:
            # Optional field — only None is treated as missing; "" is acceptable.
            missing = value is None
        else:
            missing = value is None or value == "" or value == []
        results.append(
            _result(
                field=field_name,
                validation_type="required",
                passed=not missing,
                message=(
                    f"'{field_name}' is required but missing."
                    if missing
                    else f"'{field_name}' is present."
                ),
                blocking=True,
            )
        )
    return results


def validate_inspection_done_by(value: Any) -> ValidationResult:
    """Validate that ``inspection_done_by`` is one of the allowed values.

    Allowed values: ``FM_MANAGER``, ``OPERATIONS``.
    """
    passed = value in _INSPECTION_ALLOWED
    allowed_display = ", ".join(sorted(_INSPECTION_ALLOWED))
    return _result(
        field="inspection_done_by",
        validation_type="enum",
        passed=passed,
        message=(
            f"'inspection_done_by' must be one of: {allowed_display}. Got: '{value}'."
            if not passed
            else f"'inspection_done_by' value '{value}' is valid."
        ),
        blocking=True,
    )


def validate_guideline_link(value: Any) -> ValidationResult:
    """Validate that ``guideLineLink`` is an absolute http(s) URL."""
    text = str(value or "").strip()
    passed = text.startswith("http://") or text.startswith("https://")
    return _result(
        field="guideLineLink",
        validation_type="url",
        passed=passed,
        message=(
            "Guideline link must be a valid URL starting with http:// or https://."
            if not passed
            else "Guideline link is a valid URL."
        ),
        blocking=True,
    )


def validate_date_formats(data: dict[str, Any]) -> list[ValidationResult]:
    """For each known single-date field with a non-empty value, ensure it
    parses as a real calendar date. Rejects unparseable tokens (e.g. a lease
    code accidentally routed into a date slot) so missing_field can re-ask.

    Returns a list of ValidationResult; empty when every populated date is
    parseable. Each failure attaches to its own field so the re-ask targets
    the correct slot.
    """
    results: list[ValidationResult] = []
    for field_name in _SINGLE_DATE_FIELDS:
        raw = data.get(field_name)
        if raw in (None, ""):
            continue
        parsed = _parse_date(raw, field_name)
        if parsed is None:
            results.append(
                _result(
                    field=field_name,
                    validation_type="date_format",
                    passed=False,
                    message=(
                        f"'{field_name}' value {raw!r} is not a valid date. "
                        "Please enter it as DD/MM/YYYY (e.g. 25/07/2026)."
                    ),
                    blocking=True,
                )
            )
    return results


def validate_start_end_date(start_date: Any, end_date: Any) -> ValidationResult:
    """Validate that ``startDate`` is strictly before ``endDate``.

    Both values must be ISO-8601 date strings (``YYYY-MM-DD``).
    Equal dates are considered invalid (start must be *before* end).
    """
    start = _parse_date(start_date, "startDate")
    end = _parse_date(end_date, "endDate")

    if start is None and end is None:
        return _result(
            field="startDate",
            validation_type="date_range",
            passed=False,
            message="Cannot validate date range: both 'startDate' and 'endDate' are missing or unparseable.",
            blocking=True,
        )
    if start is None:
        return _result(
            field="startDate",
            validation_type="date_range",
            passed=False,
            message="Cannot validate date range: 'startDate' is missing or unparseable.",
            blocking=True,
        )
    if end is None:
        return _result(
            field="endDate",
            validation_type="date_range",
            passed=False,
            message="Cannot validate date range: 'endDate' is missing or unparseable.",
            blocking=True,
        )

    passed = start < end
    return _result(
        field="endDate",
        validation_type="date_range",
        passed=passed,
        message=(
            f"The end date ({end}) must be after the start date ({start}). "
            "Please provide a later end date."
            if not passed
            else f"Start date ({start}) is before end date ({end})."
        ),
        blocking=True,
    )


def validate_wp_date_range(start_date: Any, end_date: Any) -> ValidationResult:
    """Work Permit dates: ``start_date`` must not be after ``end_date``.

    Same-day permits are valid (unlike Handover ``startDate < endDate``).
    """
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")

    if start is None and end is None:
        return _result(
            field="start_date",
            validation_type="date_range",
            passed=False,
            message="Cannot validate date range: both start and end dates are missing or unparseable.",
            blocking=True,
        )
    if start is None:
        return _result(
            field="start_date",
            validation_type="date_range",
            passed=False,
            message="Cannot validate date range: start date is missing or unparseable.",
            blocking=True,
        )
    if end is None:
        return _result(
            field="end_date",
            validation_type="date_range",
            passed=False,
            message="Cannot validate date range: end date is missing or unparseable.",
            blocking=True,
        )

    passed = start <= end
    return _result(
        field="end_date",
        validation_type="date_range",
        passed=passed,
        message=(
            f"The end date ({end}) must be on or after the start date ({start}). "
            "Please provide a later end date."
            if not passed
            else f"Start date ({start}) is on or before end date ({end})."
        ),
        blocking=True,
    )


def validate_handed_over_dates(data: dict[str, Any]) -> list[ValidationResult]:
    """Validate the Path B (HANDED_OVER / Already Trading) date chain ordering.

    Required ordering::

        actual_shop_handover_date ≤ actual_fitout_start_date
        ≤ actual_fitout_end_date ≤ actual_trading_date

    Only pairs where **both** dates are present are checked.
    """
    results: list[ValidationResult] = []

    for earlier_field, later_field in HANDED_OVER_DATE_CHAIN:
        earlier_raw = data.get(earlier_field)
        later_raw = data.get(later_field)
        if earlier_raw in (None, "") or later_raw in (None, ""):
            continue

        earlier_val = _parse_date(earlier_raw, earlier_field)
        later_val = _parse_date(later_raw, later_field)

        if earlier_val is None or later_val is None:
            bad_field = earlier_field if earlier_val is None else later_field
            results.append(
                _result(
                    field=bad_field,
                    validation_type="handed_over_date_order",
                    passed=False,
                    message=(
                        f"Cannot validate date ordering: '{bad_field}' is unparseable."
                    ),
                    blocking=True,
                )
            )
            continue

        passed = earlier_val <= later_val
        # Attach the error to the *later* field so the missing-field flow re-asks
        # the value that violates the chain (which is almost always the one the
        # user just typed) rather than the older, already-accepted earlier date.
        results.append(
            _result(
                field=later_field,
                validation_type="handed_over_date_order",
                passed=passed,
                message=(
                    f"'{later_field}' ({later_val}) must be on or after "
                    f"'{earlier_field}' ({earlier_val})."
                    if not passed
                    else f"'{earlier_field}' ({earlier_val}) ≤ '{later_field}' ({later_val})."
                ),
                blocking=True,
            )
        )

    return results


def validate_rdd_date_order(data: dict[str, Any]) -> list[ValidationResult]:
    """Validate the RDD date chain ordering.

    Required ordering::

        actual_handover_date ≤ fitout_start_date ≤ fitout_end_date ≤ trading_date

    Only pairs where **both** dates are present are checked. Incomplete pairs
    are skipped — missing dates are already covered by required-field
    validation. Emitting a blocking ``rdd_date_order`` error on a filled
    earlier date when the later date is still missing caused the bot to
    re-ask for ``actual_handover_date`` in a loop.
    """
    results: list[ValidationResult] = []

    for earlier_field, later_field in _RDD_DATE_CHAIN:
        earlier_raw = data.get(earlier_field)
        later_raw = data.get(later_field)
        # Skip until both sides exist; required-field checks handle absences.
        if earlier_raw in (None, "") or later_raw in (None, ""):
            continue

        earlier_val = _parse_date(earlier_raw, earlier_field)
        later_val = _parse_date(later_raw, later_field)

        if earlier_val is None or later_val is None:
            bad_field = earlier_field if earlier_val is None else later_field
            results.append(
                _result(
                    field=bad_field,
                    validation_type="rdd_date_order",
                    passed=False,
                    message=(
                        f"Cannot validate RDD date ordering: '{bad_field}' is unparseable."
                    ),
                    blocking=True,
                )
            )
            continue

        passed = earlier_val <= later_val
        results.append(
            _result(
                field=earlier_field,
                validation_type="rdd_date_order",
                passed=passed,
                message=(
                    f"'{earlier_field}' ({earlier_val}) must not be after "
                    f"'{later_field}' ({later_val})."
                    if not passed
                    else f"'{earlier_field}' ({earlier_val}) ≤ '{later_field}' ({later_val})."
                ),
                blocking=True,
            )
        )

    return results


def validate_document_count(
    documents: list[dict[str, Any]],
    workflow_stage: str,
) -> list[ValidationResult]:
    """Validate that the minimum required documents are present for *workflow_stage*.

    Rules
    -----
    FM_REVIEW:
        At least one document must be uploaded (any FM-allowed type).
    RDD_REVIEW:
        ``DR_SR_HANDOVER_REPORT`` must be present in the uploaded documents.
    Other stages:
        No document count requirement — returns empty list.
    """
    results: list[ValidationResult] = []

    if workflow_stage == "FM_REVIEW":
        count = len(documents or [])
        passed = count >= _FM_MIN_DOCUMENT_COUNT
        results.append(
            _result(
                field="_documents",
                validation_type="document_count",
                passed=passed,
                message=(
                    "At least one FM document must be uploaded before saving or approving."
                    if not passed
                    else f"{count} FM document(s) uploaded."
                ),
                blocking=True,
            )
        )

    elif workflow_stage == "RDD_REVIEW":
        doc_types = {
            (doc.get("document_type") or doc.get("type") or doc.get("document_type_id") or "")
            for doc in (documents or [])
        }
        has_report = any(dt in RDD_REQUIRED_DOCUMENTS for dt in doc_types)
        results.append(
            _result(
                field="_documents",
                validation_type="document_count",
                passed=has_report,
                message=(
                    "Please upload either the Handover Meeting Report or the "
                    "Rejected Fitout and Handover Report before submitting."
                    if not has_report
                    else "Required RDD handover report is present."
                ),
                blocking=True,
            )
        )

    return results


def validate_document_type(
    document_type: str,
    workflow_stage: str,
    role: str | None = None,  # noqa: ARG001 — reserved for future role-level checks
) -> ValidationResult:
    """Validate that *document_type* is permitted in *workflow_stage*.

    Two-step check:
    1. The document type must be a known type in ``ALL_DOCUMENT_TYPES``.
    2. The document type must be in the allowlist for *workflow_stage*.

    The *role* parameter is a permission hook reserved for future role-level
    document restrictions (e.g. preventing a MALL_MANAGER from uploading RDD
    documents).  It is accepted but not yet enforced at the document-type level
    since the current schema does not define per-role document restrictions.
    """
    if document_type not in ALL_DOCUMENT_TYPES:
        return _result(
            field="document_type",
            validation_type="document_type",
            passed=False,
            message=f"Unknown document type '{document_type}'. Not in the allowed document registry.",
            blocking=True,
        )

    allowed = _STAGE_DOCUMENT_ALLOWLIST.get(workflow_stage, frozenset())
    passed = document_type in allowed
    return _result(
        field="document_type",
        validation_type="document_type",
        passed=passed,
        message=(
            f"Document type '{document_type}' is not permitted in stage '{workflow_stage}'."
            if not passed
            else f"Document type '{document_type}' is valid for stage '{workflow_stage}'."
        ),
        blocking=True,
    )


def validate_permission(role: str, workflow_stage: str) -> ValidationResult:
    """Permission hook: verify that *role* is authorised to act on *workflow_stage*.

    Uses ``PERMISSION_MAP`` from ``handover_schema`` as the authority.
    Blocking — an unauthorised role must not be allowed to submit.
    """
    passed = role_can_act_on_stage(role, workflow_stage)
    return _result(
        field="_permission",
        validation_type="permission",
        passed=passed,
        message=(
            f"Role '{role}' is not authorised to act on stage '{workflow_stage}'."
            if not passed
            else f"Role '{role}' is authorised for stage '{workflow_stage}'."
        ),
        blocking=True,
    )


# ── ValidationService orchestrator ────────────────────────────────────────────


class ValidationService:
    """Orchestrates deterministic validations for a Handover Service Request draft.

    ``validate_draft()`` is the primary entry point.  It runs all applicable
    rules for the current workflow stage and returns only FAILED results,
    which are stored directly into ``state.validation_errors`` by the
    validation node.
    """

    def validate_draft(
        self,
        data: dict[str, Any],
        workflow_stage: str = "CREATE_SR",
        documents: list[dict] | None = None,
        role: str | None = None,
    ) -> list[ValidationResult]:
        """Run all applicable validations; return only FAILED ValidationResult dicts.

        Rule application order
        ----------------------
        1. Required field validation for the current stage.
        2. ``inspection_done_by`` enum check (when the field is present).
        3. ``startDate`` < ``endDate`` (when both dates are present).
        4. RDD date chain ordering (RDD_REVIEW stage only).
        5. Document type validation for each uploaded document.
        6. Permission hook (when *role* is provided).

        Only FAILED results are returned so callers can directly store them in
        ``state.validation_errors`` without further filtering.
        """
        errors: list[ValidationResult] = []

        # Resolve stage definition — handover stages first, then Work Permit.
        stage_def = STAGE_REGISTRY.get(workflow_stage) or WP_STAGE_REGISTRY.get(workflow_stage)

        # 1. Required fields
        if stage_def:
            for r in validate_required_fields(data, stage_def.required_fields):
                if r["status"] == "FAILED":
                    errors.append(r)

        # 1a. Work Permit type enum check
        if workflow_stage == "CREATE_WORK_PERMIT":
            wp_type = data.get("work_permit_type")
            if wp_type is not None and wp_type not in VALID_WORK_PERMIT_TYPES:
                errors.append(
                    _result(
                        field="work_permit_type",
                        validation_type="enum",
                        passed=False,
                        message=(
                            f"Invalid work permit type '{wp_type}'. "
                            f"Allowed values: {sorted(VALID_WORK_PERMIT_TYPES)}."
                        ),
                        blocking=True,
                    )
                )

        # 2. inspection_done_by (only when the field is present in data)
        idb_value = data.get("inspection_done_by")
        if idb_value is not None:
            r = validate_inspection_done_by(idb_value)
            if r["status"] == "FAILED":
                errors.append(r)

        # 2b. guideLineLink must be http(s) when present
        guideline = data.get("guideLineLink")
        if guideline not in (None, ""):
            r = validate_guideline_link(guideline)
            if r["status"] == "FAILED":
                errors.append(r)

        # 2c. Per-field date format check — catches non-date tokens routed
        # into a date slot (e.g. user typing a lease code when asked for
        # startDate). Runs before the ordering checks so an unparseable
        # value is re-asked at its own field, not blamed on a chain
        # counterpart.
        for r in validate_date_formats(data):
            if r["status"] == "FAILED":
                errors.append(r)

        # 3. startDate < endDate (only when both fields are present)
        if data.get("startDate") and data.get("endDate"):
            r = validate_start_end_date(data["startDate"], data["endDate"])
            if r["status"] == "FAILED":
                errors.append(r)

        # 3b. Work Permit start_date ≤ end_date (same-day permits are allowed).
        # Skip when either value already failed format so we don't double-report.
        if (
            data.get("start_date")
            and data.get("end_date")
            and _parse_date(data["start_date"], "start_date") is not None
            and _parse_date(data["end_date"], "end_date") is not None
        ):
            r = validate_wp_date_range(data["start_date"], data["end_date"])
            if r["status"] == "FAILED":
                errors.append(r)

        # 4. RDD date ordering (RDD_REVIEW stage only)
        if workflow_stage == "RDD_REVIEW":
            for r in validate_rdd_date_order(data):
                if r["status"] == "FAILED":
                    errors.append(r)

        # 4b. Path B (HANDED_OVER) date ordering
        if workflow_stage == "CREATE_HANDED_OVER_SR":
            for r in validate_handed_over_dates(data):
                if r["status"] == "FAILED":
                    errors.append(r)

        # 5a. Document count validation (FM must have ≥1 doc; RDD must have report)
        for r in validate_document_count(documents or [], workflow_stage):
            if r["status"] == "FAILED":
                errors.append(r)

        # 5b. Document type validation (per-document allowlist check)
        for doc in documents or []:
            doc_type = doc.get("document_type") or doc.get("type")
            if doc_type:
                r = validate_document_type(doc_type, workflow_stage, role)
                if r["status"] == "FAILED":
                    errors.append(r)

        # 6. Permission hook
        if role and workflow_stage:
            r = validate_permission(role, workflow_stage)
            if r["status"] == "FAILED":
                errors.append(r)

        logger.debug(
            "validate_draft: stage=%s total_failures=%d blocking=%d",
            workflow_stage,
            len(errors),
            sum(1 for e in errors if e["blocking"]),
        )
        return errors

    def validate(self, draft: dict[str, object]) -> list[ValidationIssueDTO]:
        """Backward-compatible API: run validations and return ``ValidationIssueDTO`` list.

        Delegates to ``validate_draft`` and maps each FAILED result to a
        ``ValidationIssueDTO`` for consumers that predate the richer output format.
        """
        rich = self.validate_draft(
            data=dict(draft),
            workflow_stage=str(draft.get("workflow_stage", "CREATE_SR")),
        )
        return [
            ValidationIssueDTO(
                code=r["validation_type"].upper(),
                message=r["message"],
                field_key=r["field"] if r["field"] != "_permission" else None,
            )
            for r in rich
        ]
