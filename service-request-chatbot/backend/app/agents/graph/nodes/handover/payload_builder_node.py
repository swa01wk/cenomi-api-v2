"""Build API payload from validated draft.

Responsibilities
----------------
* For the CREATE_SR stage: call ``build_create_handover_payload`` with the
  current ``collected_data`` and store the result in ``backend_refs``.
* Guard against missing required fields by catching ``ValueError`` from the
  payload builder and setting ``status = "FAILED"``.

Non-responsibilities
--------------------
* Does not call the LLM.
* Does not submit the payload to the API (that is ``api_submission_node``).
* Does not handle FM_REVIEW or RDD_REVIEW payload shapes.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.services.payload_builder_service import (
    build_create_handover_payload,
    build_create_handed_over_payload,
)
from app.observability.decorators import trace_node

logger = logging.getLogger(__name__)


_FIELD_LABELS: dict[str, str] = {
    "inspection_done_by": "who will perform the inspection (FM Manager or Operations)",
    "startDate": "inspection start date",
    "endDate": "inspection end date",
    "unit_readiness_date": "unit readiness date",
    "expected_handover_date": "expected handover date",
    "actual_handover_date": "actual handover date",
    "fitout_start_date": "fitout start date",
    "fitout_end_date": "fitout end date",
    "trading_date": "trading date",
    "lease_code": "lease code",
    "lease_id": "lease ID",
    "brand_id": "brand",
    "tenant_profile_id": "tenant",
    "contract_id": "contract",
    "property_id": "property",
    "unit_codes": "unit codes",
    "contracted_area": "contracted area",
    "city": "city",
    "brand": "brand",
    "mall": "mall",
    "title": "title",
    "description": "description",
    "lease_brand_mall": "lease / brand / mall",
}


def _humanize_payload_error(raw: str) -> str:
    """Turn ``build_*_payload`` ValueError messages into a user-facing prompt.

    The raw error text is a repr like
    ``build_create_handover_payload: missing required data keys: ['inspection_done_by']``.
    Extract the field list and phrase it in plain English so the user knows
    which field to supply / retry.
    """
    # Extract the bracketed list of keys after "missing required data keys:".
    marker = "missing required data keys:"
    idx = raw.find(marker)
    if idx < 0:
        return (
            "Some required details are missing before I can submit. Please review "
            "the form and try again."
        )
    tail = raw[idx + len(marker):].strip().strip("[]")
    keys = [k.strip().strip("'\"") for k in tail.split(",") if k.strip()]
    if not keys:
        return (
            "Some required details are missing before I can submit. Please review "
            "the form and try again."
        )
    labels = [_FIELD_LABELS.get(k, k) for k in keys]
    if len(labels) == 1:
        return (
            f"I can't submit yet — the {labels[0]} is still empty. "
            "Please provide it and try again."
        )
    joined = ", ".join(labels[:-1]) + f", and {labels[-1]}"
    return (
        f"I can't submit yet — these details are still empty: {joined}. "
        "Please provide them and try again."
    )


@trace_node("payload_builder", "TOOL")
async def payload_builder_node(state: ServiceRequestState) -> dict[str, Any]:
    """Build the CREATE_SR API payload and store it in ``backend_refs``.

    Reads from state
    ----------------
    ``collected_data``  — validated draft field values.
    ``workflow_stage``  — only acts for ``"CREATE_SR"``; skips other stages.

    Writes to state
    ---------------
    ``backend_refs``    — updated with ``{"create_payload": <payload dict>}``.
    ``status``          — set to ``"FAILED"`` if required keys are missing.
    """
    collected_data: dict[str, Any] = state.get("collected_data") or {}
    workflow_stage: str = state.get("workflow_stage") or "CREATE_SR"

    if workflow_stage not in ("CREATE_SR", "CREATE_HANDED_OVER_SR"):
        logger.info(
            "payload_builder_node: skipped for stage=%s (only handles CREATE_SR / CREATE_HANDED_OVER_SR)",
            workflow_stage,
        )
        return {}

    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    documents = list(state.get("documents") or [])

    if workflow_stage == "CREATE_HANDED_OVER_SR":
        try:
            payload = build_create_handed_over_payload(collected_data, documents=documents)
        except ValueError as exc:
            logger.warning(
                "payload_builder_node: handed_over payload build failed — %s", exc
            )
            # Keep confirmation_status intact so api_submission's confirmation
            # guard doesn't fire first and mask this specific error. The
            # payload guard downstream surfaces the field-level message below.
            return {
                "status": "FAILED",
                "response_message": _humanize_payload_error(str(exc)),
            }
        backend_refs["create_handed_over_payload"] = payload
        backend_refs["create_payload"] = payload  # unified key for api_submission_node
        logger.info(
            "payload_builder_node: handed_over payload built for lease_code=%s",
            collected_data.get("lease_code"),
        )
        return {"backend_refs": backend_refs}

    try:
        payload = build_create_handover_payload(collected_data, documents=documents)
    except ValueError as exc:
        logger.warning("payload_builder_node: payload build failed — %s", exc)
        return {
            "status": "FAILED",
            "response_message": _humanize_payload_error(str(exc)),
        }

    backend_refs["create_payload"] = payload

    logger.info(
        "payload_builder_node: payload built for lease_code=%s",
        collected_data.get("lease_code"),
    )

    return {"backend_refs": backend_refs}
