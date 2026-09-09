"""Validate draft using code + schemas — not the LLM.

This node runs all deterministic validation rules against ``collected_data``
and updates two state fields:

* ``validation_errors`` — list of FAILED ``ValidationResult`` dicts (see
  ``validation_service.py`` for the canonical shape).
* ``status`` — set to ``"READY_TO_SUBMIT"`` when there are no blocking errors,
  or kept at ``"IN_PROGRESS"`` when blocking errors must be resolved first.

Responsibilities
----------------
* Invoke ``ValidationService.validate_draft()`` for the current workflow stage.
* Populate ``state.validation_errors`` with all FAILED validation results.
* Set ``state.status`` based on whether any blocking errors were found.
* Log a traceable summary (stage, total failures, blocking count).

Non-responsibilities
--------------------
* Does not call the LLM.
* Does not collect missing fields (that is ``missing_field_node``'s job).
* Does not submit the service request (that is ``api_submission_node``'s job).
* Does not generate a user-facing response message (that is
  ``response_generation_node``'s job).
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.fitout_confirm_schema import (
    get_missing_fields as fitout_get_missing_fields,
)
from app.agents.services.validation_service import ValidationService
from app.observability.decorators import trace_node

logger = logging.getLogger(__name__)

_validation_service = ValidationService()

# Fitout confirmation agents have their own required-field schema; skip the
# handover STAGE_REGISTRY validation entirely and use fitout schema instead.
_FITOUT_CONFIRM_AGENTS: frozenset[str] = frozenset({
    "rdd_fitout_start_agent",
    "rdd_fitout_end_agent",
    "rdd_trading_confirmation_agent",
})


@trace_node("validation", "AGENT")
async def validation_node(state: ServiceRequestState) -> dict[str, Any]:
    """Run deterministic validation; update ``validation_errors`` and ``status``.

    Reads from state
    ----------------
    ``collected_data``  — the draft field values to validate.
    ``workflow_stage``  — determines which required fields and document types apply.
    ``documents``       — uploaded documents whose types must be validated.
    ``role``            — optional user role for permission hook (not in TypedDict;
                          injected at runtime by the API layer when available).
    ``active_agent``    — agent name; fitout agents use fitout-schema validation.
    ``sub_category``    — fitout sub-category (FOS / END / TRADING).

    Writes to state
    ---------------
    ``validation_errors`` — list of FAILED ValidationResult dicts; empty when all
                            rules pass.
    ``status``            — ``"READY_TO_SUBMIT"`` (no blocking errors) or
                            ``"IN_PROGRESS"`` (blocking errors present).
    """
    collected_data: dict[str, Any] = state.get("collected_data") or {}
    workflow_stage: str = state.get("workflow_stage") or "CREATE_SR"
    documents: list[dict] = state.get("documents") or []
    role: str | None = state.get("role")  # type: ignore[arg-type]
    active_agent: str | None = state.get("active_agent")

    if active_agent in _FITOUT_CONFIRM_AGENTS:
        # Use fitout-specific required-field check — the handover STAGE_REGISTRY
        # requires description / startDate / title which fitout does not use.
        sub_category: str = state.get("sub_category") or ""  # type: ignore[assignment]
        missing_fields = fitout_get_missing_fields(workflow_stage, sub_category, collected_data)
        from app.agents.schemas.fitout_confirm_schema import OPTIONAL_FIELDS as _FITOUT_OPTIONAL
        validation_errors: list[dict[str, Any]] = [
            {
                "field": f,
                "validation_type": "required",
                "status": "FAILED",
                "message": f"Required field '{f}' is missing.",
                # Optional fields (comments, delay_reason for TRADING) must not block
                # submission — the user may leave them blank. Setting blocking=False here
                # ensures fitout_confirm_api_submission can proceed even when they are None.
                "blocking": f not in _FITOUT_OPTIONAL,
            }
            for f in missing_fields
        ]
    else:
        validation_errors = _validation_service.validate_draft(
            data=collected_data,
            workflow_stage=workflow_stage,
            documents=documents,
            role=role,
        )

    blocking_errors = [e for e in validation_errors if e["blocking"]]
    has_blocking = len(blocking_errors) > 0

    logger.info(
        "validation_node: stage=%s total_errors=%d blocking_errors=%d",
        workflow_stage,
        len(validation_errors),
        len(blocking_errors),
    )

    # Drop unparseable date-slot values so they don't linger in
    # draft_preview / Formik after the bot re-asks. Without this, a bogus
    # entry (e.g. lease code typed into startDate) stays visible in the
    # form even though the AI rejected it, and the next valid answer looks
    # to the bridge like "no change" for the field it re-asked. Only clear
    # date_format failures — required / ordering / enum errors are handled
    # by the normal collection flow.
    cleared_fields: list[str] = []
    for err in blocking_errors:
        if err.get("validation_type") == "date_format":
            field_name = err.get("field")
            if field_name and field_name in collected_data:
                collected_data.pop(field_name, None)
                cleared_fields.append(field_name)
    if cleared_fields:
        logger.info(
            "validation_node: cleared invalid date fields %s (re-ask on next turn)",
            cleared_fields,
        )

    new_status: str = "IN_PROGRESS" if has_blocking else "READY_TO_SUBMIT"

    result: dict[str, Any] = {
        "validation_errors": validation_errors,
        "status": new_status,
    }
    if cleared_fields:
        result["collected_data"] = collected_data

    # A confirmation the flow refuses is void, and must not fire later.
    #
    # Reported live on SR 320998. The user set an end date before the start,
    # clicked Confirm & Submit, and was correctly refused — but
    # ``confirmation_status`` stayed CONFIRMED. Seven seconds later a silent
    # form-sync turn carried a valid date, validation passed, and every entry
    # route sends a standing CONFIRMED straight to merge_state and on to
    # submission. The SR was created by a background sync, from a confirmation
    # that had already been turned down.
    #
    # The exemption in _route_after_validation is mirrored exactly: an optional
    # field reported missing does not refuse a confirmed draft, so it must not
    # void the confirmation either.
    if has_blocking and state.get("confirmation_status") == "CONFIRMED":
        if active_agent in _FITOUT_CONFIRM_AGENTS:
            from app.agents.schemas.fitout_confirm_schema import (
                OPTIONAL_FIELDS as _OPTIONAL,
            )
        else:
            from app.agents.schemas.handover_schema import (
                OPTIONAL_FIELDS as _OPTIONAL,
            )
        refusing = [
            e
            for e in blocking_errors
            if not (
                e.get("validation_type") == "required"
                and e.get("field") in _OPTIONAL
            )
        ]
        if refusing:
            logger.info(
                "validation_node: voiding a confirmation refused by %d error(s)"
                " — the user must confirm again",
                len(refusing),
            )
            result["confirmation_status"] = None

    return result
