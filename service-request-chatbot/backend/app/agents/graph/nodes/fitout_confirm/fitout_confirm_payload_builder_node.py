"""Build POST / PATCH payloads for the Fitout Confirmation SR workflow.

Responsibilities
----------------
* ``build_create_fitout_confirm_payload`` — constructs the POST body per
  sub_category config (populates or empties the Yes/No branch fields, strips
  ``comments`` from TRADING, formats dates to ``YYYY-MM-DD``).
* ``build_approve_fitout_confirm_payload`` — constructs the PATCH body with
  ``status: APPROVED`` (or ``status: IN_PROCESS`` for "Update only") plus the
  full payload fields.
* ``fitout_confirm_payload_builder_node`` — graph node wrapper.

Non-responsibilities
--------------------
* Does not call the LLM.
* Does not submit the payload (that is fitout_confirm_api_submission_node).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.fitout_confirm_schema import SUBCAT_CONFIG, build_title
from app.observability.decorators import trace_node

logger = logging.getLogger(__name__)

# ISO date pattern YYYY-MM-DD
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _normalise_date(value: str | None) -> str:
    """Return value unchanged if it is already YYYY-MM-DD, else return empty string."""
    if not value:
        return ""
    if _DATE_RE.match(value.strip()):
        return value.strip()
    return ""


def build_create_fitout_confirm_payload(
    collected: dict[str, Any],
    sub_category: str,
    documents: list[dict] | None = None,
) -> dict[str, Any]:
    """Construct the POST /service-requests payload for a fitout confirmation SR.

    Parameters
    ----------
    collected:
        Validated draft field values from state["collected_data"].
    sub_category:
        One of FIT_OUT_CONFIRM_FOS / FIT_OUT_CONFIRM_END / FIT_OUT_CONFIRM_TRADING.
    documents:
        Optional list of uploaded document dicts (from state["documents"]).
        Ignored for TRADING (no documents section).
    """
    cfg = SUBCAT_CONFIG.get(sub_category)
    if not cfg:
        raise ValueError(f"Unknown sub_category for fitout confirm: {sub_category!r}")

    radio_field = cfg["radio_field"]
    radio_value = collected.get(radio_field, "")
    lease_code = collected.get("lease_code", "")
    pms_tenant_code = collected.get("pms_tenant_code", "") or lease_code

    payload_inner: dict[str, Any] = {
        "lease_brand_mall": collected.get("lease_brand_mall", ""),
        "lease_code": lease_code,
        "company_name": collected.get("company_name", "") or collected.get("company", ""),
        "brand_name": collected.get("brand_name", "") or collected.get("brand", ""),
        "mall_name": collected.get("mall_name", "") or collected.get("mall", ""),
        radio_field: radio_value,
    }

    # YES branch — set actual date; empty-string all planned/delay fields
    actual_field = cfg["actual_date_field"]
    if radio_value == "yes":
        payload_inner[actual_field] = _normalise_date(collected.get(actual_field))
        for pf in cfg["planned_date_fields"]:
            payload_inner[pf] = ""
        payload_inner["delay_reason"] = ""
    else:
        # NO branch — empty actual; set planned dates + delay_reason
        payload_inner[actual_field] = ""
        for pf in cfg["planned_date_fields"]:
            payload_inner[pf] = _normalise_date(collected.get(pf))
        payload_inner["delay_reason"] = collected.get("delay_reason", "") or ""

    # Base dates (readonly, from backend)
    for bf in cfg["base_date_fields"]:
        payload_inner[bf] = collected.get(bf, "") or ""

    # Prior milestone readonly date
    prior_field = cfg["prior_milestone_field"]
    payload_inner[prior_field] = collected.get(prior_field, "") or ""

    # Documents — only for FOS and END
    if cfg["has_documents"]:
        doc_ids = []
        if documents:
            doc_ids = [
                d.get("document_id") or d.get("id")
                for d in documents
                if d.get("document_id") or d.get("id")
            ]
        payload_inner["documents_ids"] = doc_ids

    # Comments — only for FOS and END; TRADING has no comments section
    if cfg["has_comments"]:
        payload_inner["comments"] = collected.get("comments", "") or ""

    title = collected.get("title") or build_title(sub_category, pms_tenant_code)
    lease_id = collected.get("lease_id")

    return {
        "payload": payload_inner,
        "title": title,
        "service_category": "FIT_OUT_AND_HANDOVER",
        "sub_category": sub_category,
        "lease_code": lease_code,
        "lease_id": lease_id,
        "service_request_id": "",
    }


def build_approve_fitout_confirm_payload(
    collected: dict[str, Any],
    sub_category: str,
    sr_id: str,
    *,
    approve: bool = True,
    documents: list[dict] | None = None,
) -> dict[str, Any]:
    """Construct the PATCH /service-requests/{sr_id} payload for approving.

    Parameters
    ----------
    approve:
        True  → ``status: APPROVED`` (Update and Approve)
        False → ``status: IN_PROCESS`` (Update only — stays in APPROVE_SR stage)
    """
    base = build_create_fitout_confirm_payload(collected, sub_category, documents=documents)
    base["status"] = "APPROVED" if approve else "IN_PROCESS"
    base["service_request_id"] = sr_id
    return base


# ---------------------------------------------------------------------------
# Graph node
# ---------------------------------------------------------------------------


@trace_node("fitout_confirm_payload_builder", "TOOL")
async def fitout_confirm_payload_builder_node(state: ServiceRequestState) -> dict[str, Any]:
    """Build the fitout confirm payload and store it in backend_refs.

    Reads from state
    ----------------
    ``collected_data``   — validated draft field values.
    ``sub_category``     — determines the sub-category config branch.
    ``workflow_stage``   — CREATE_SR builds a POST payload; APPROVE_SR builds a PATCH.
    ``documents``        — uploaded document list (FOS/END only).

    Writes to state
    ---------------
    ``backend_refs``     — enriched with ``{"create_payload": <payload>}``.
    ``status``           — set to ``"FAILED"`` if required keys are missing.
    """
    collected: dict[str, Any] = state.get("collected_data") or {}
    sub_category: str = state.get("sub_category") or ""
    workflow_stage: str = state.get("workflow_stage") or "CREATE_SR"
    documents = list(state.get("documents") or [])
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})

    if sub_category not in SUBCAT_CONFIG:
        logger.warning(
            "fitout_confirm_payload_builder: unknown sub_category=%s", sub_category
        )
        return {"status": "FAILED"}

    try:
        if workflow_stage == "APPROVE_SR":
            sr_id = backend_refs.get("sr_id", "")
            approve_flag = backend_refs.get("fitout_action") != "update_only"
            payload = build_approve_fitout_confirm_payload(
                collected, sub_category, sr_id=sr_id, approve=approve_flag, documents=documents
            )
        else:
            payload = build_create_fitout_confirm_payload(
                collected, sub_category, documents=documents
            )
    except ValueError as exc:
        logger.warning("fitout_confirm_payload_builder: payload build failed — %s", exc)
        return {"status": "FAILED"}

    backend_refs["create_payload"] = payload
    logger.info(
        "fitout_confirm_payload_builder: payload built for sub_category=%s lease_code=%s",
        sub_category,
        collected.get("lease_code"),
    )
    return {"backend_refs": backend_refs}
