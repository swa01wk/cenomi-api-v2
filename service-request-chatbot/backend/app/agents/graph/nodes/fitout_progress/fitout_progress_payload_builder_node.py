"""Build the POST / PATCH payload for the Fitout Progress SR workflow.

Responsibilities
----------------
* ``build_create_fitout_progress_payload`` — the POST body.
* ``build_approve_fitout_progress_payload`` — the PATCH body, ``APPROVED`` or
  ``REJECTED``, starting from the payload the platform already stored.
* ``fitout_progress_payload_builder_node`` — the graph node wrapper.

Non-responsibilities
--------------------
* Does not call the LLM, and does not submit (that is the submission node).

Shape notes the platform enforces
---------------------------------
* ``tenant_profile_id`` must be non-zero: ``FIT_OUT_AND_HANDOVER`` is not in the
  allow-zero list, and a ``0`` is a 400 (*should provide a valid tenant profile
  id*). Raised locally instead, so the user gets a question.
* The percentage travels as a **string** — ``extractFitoutProgressPercentage``
  accepts a JSON string or number, and the form sends a string.
* ``lease_brand_mall`` is truncated at ``--``, dropping the Arabic half, exactly
  as the platform scheduler does when it builds the same payload.
* On the approve path only ``fitout_progress_percentage`` and ``comment`` may
  differ from the stored payload. ``ValidateEditableFields`` compares key by
  key, so every other key is echoed back untouched — which is why the builder
  starts from the stored payload rather than rebuilding it from scratch.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.fitout_progress_schema import (
    COMMENT_FIELD,
    PROGRESS_FIELD,
    SERVICE_CATEGORY,
    SUB_CATEGORY,
    build_title,
)
from app.agents.services.fitout_progress_suggestions_service import (
    format_percentage,
    parse_percentage,
    resolve_window,
)
from app.observability.decorators import trace_node

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Keys the platform lets level 1 change (action_rules.UPDATE.editable_fields).
EDITABLE_PAYLOAD_KEYS: tuple[str, ...] = (PROGRESS_FIELD, COMMENT_FIELD)


def _iso_or_blank(value: Any) -> str:
    text = str(value or "").strip()
    return text if _DATE_RE.match(text) else ""


def _lease_brand_mall(collected: dict[str, Any]) -> str:
    """The English half of the label; the scheduler truncates at ``--`` too."""
    raw = str(collected.get("lease_brand_mall") or "")
    return raw.split("--", 1)[0].strip()


def _percentage_string(collected: dict[str, Any]) -> str:
    """The percentage as the platform wants it, or "" when it is unusable.

    An unusable value is not silently dropped: the submission guard refuses the
    turn because validation has already flagged it, and an empty string is what
    ``require_fields`` rejects if it somehow gets that far.
    """
    parsed = parse_percentage(collected.get(PROGRESS_FIELD))
    return format_percentage(parsed) if parsed is not None else ""


def build_create_fitout_progress_payload(collected: dict[str, Any]) -> dict[str, Any]:
    """Construct the ``POST /service-requests`` body."""
    tenant_profile_id = collected.get("tenant_profile_id")
    if not tenant_profile_id:
        raise ValueError("tenant_profile_id is required for a Fitout Progress SR")

    lease_code = str(collected.get("lease_code") or "")
    pms_tenant_code = str(collected.get("pms_tenant_code") or "") or lease_code
    lease_id = collected.get("lease_id")
    start, end = resolve_window(collected)

    payload_inner: dict[str, Any] = {
        "lease_brand_mall": _lease_brand_mall(collected),
        "lease_code": lease_code,
        "company_name": str(
            collected.get("company_name") or collected.get("company") or ""
        ),
        "brand_name": str(collected.get("brand_name") or collected.get("brand") or ""),
        "mall_name": str(collected.get("mall_name") or collected.get("mall") or ""),
        "fitout_start_date": _iso_or_blank(start),
        "fitout_end_date": _iso_or_blank(end),
        PROGRESS_FIELD: _percentage_string(collected),
        COMMENT_FIELD: str(collected.get(COMMENT_FIELD) or ""),
        # The scheduler sets contract_id to the lease id; the form's readonly
        # lease reference is built from the same value.
        "contract_id": lease_id,
    }

    body: dict[str, Any] = {
        "payload": payload_inner,
        "title": str(collected.get("title") or build_title(pms_tenant_code)),
        "tenant_profile_id": tenant_profile_id,
        "service_category": SERVICE_CATEGORY,
        "sub_category": SUB_CATEGORY,
        "lease_code": lease_code,
        "lease_id": lease_id,
        "service_request_id": "",
    }
    property_id = collected.get("property_id")
    if property_id:
        body["property_id"] = property_id
    return body


def build_approve_fitout_progress_payload(
    collected: dict[str, Any],
    sr_id: str,
    *,
    stored_payload: dict[str, Any] | None = None,
    approve: bool = True,
    comment: str = "",
) -> dict[str, Any]:
    """Construct the ``PATCH /service-requests/{sr_id}`` body.

    *stored_payload* is the payload the platform holds for this SR. When it is
    given, it is echoed back with only the two editable keys changed — anything
    else would come back as ``field "<key>" is not editable at level 1``. When
    it is absent the payload is rebuilt from ``collected_data``, which is the
    best that can be done and is why the submission node prefers to fetch it.
    """
    if stored_payload:
        payload_inner = dict(stored_payload)
        payload_inner[PROGRESS_FIELD] = _percentage_string(collected)
        payload_inner[COMMENT_FIELD] = str(collected.get(COMMENT_FIELD) or "")
    else:
        payload_inner = build_create_fitout_progress_payload(collected)["payload"]

    lease_code = str(collected.get("lease_code") or payload_inner.get("lease_code") or "")
    pms_tenant_code = str(collected.get("pms_tenant_code") or "") or lease_code

    return {
        "payload": payload_inner,
        "status": "APPROVED" if approve else "REJECTED",
        "comment": comment,
        "title": str(collected.get("title") or build_title(pms_tenant_code)),
        "sub_category": SUB_CATEGORY,
        "service_request_id": sr_id,
    }


@trace_node("fitout_progress_payload_builder", "TOOL")
async def fitout_progress_payload_builder_node(state: ServiceRequestState) -> dict[str, Any]:
    """Build the payload and store it in ``backend_refs.create_payload``."""
    collected: dict[str, Any] = state.get("collected_data") or {}
    workflow_stage: str = state.get("workflow_stage") or "CREATE_SR"
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})

    try:
        if workflow_stage == "APPROVE_SR":
            payload = build_approve_fitout_progress_payload(
                collected,
                sr_id=str(backend_refs.get("sr_id") or ""),
                stored_payload=backend_refs.get("stored_payload"),
                approve=backend_refs.get("fitout_progress_action") != "reject",
                # The actor comment is the note the user attached to the
                # percentage, when they gave one; ``comment`` is the same key the
                # payload carries, and REJECT has no require_fields at all.
                comment=str(collected.get(COMMENT_FIELD) or "")
                or _default_comment(state),
            )
        else:
            payload = build_create_fitout_progress_payload(collected)
    except ValueError as exc:
        logger.warning("fitout_progress_payload_builder: build failed — %s", exc)
        if workflow_stage == "APPROVE_SR":
            # Reached only when the platform's stored payload never arrived, so
            # asking about the lease would be nonsense — the SR has one.
            return {
                "status": "FAILED",
                "response_message": (
                    "I couldn't read this request's details back from the "
                    "platform, so I haven't sent your decision. Please try "
                    "again in a moment."
                ),
            }
        return {
            "status": "FAILED",
            "response_message": (
                "I couldn't put the request together because the lease details "
                "are incomplete. Let's start from the lease — which lease is it?"
            ),
        }

    backend_refs["create_payload"] = payload
    logger.info(
        "fitout_progress_payload_builder: built stage=%s lease_code=%s percentage=%s",
        workflow_stage,
        collected.get("lease_code"),
        payload["payload"].get(PROGRESS_FIELD),
    )
    return {"backend_refs": backend_refs}


def _default_comment(state: ServiceRequestState) -> str:
    """What to send as the actor comment when the user gave no words of their own."""
    backend_refs = state.get("backend_refs") or {}
    if backend_refs.get("fitout_progress_action") == "reject":
        return "Rejected via assistant"
    return "Approved via assistant"
