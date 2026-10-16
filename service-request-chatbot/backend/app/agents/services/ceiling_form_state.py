"""Per-turn form snapshot for the Ceiling Inspection split-screen POC.

The POC renders the platform's OCI / CCI form next to the chat. Every chat
response carries ``form_state`` so the form can fill itself as the agent
collects fields, highlight the field being asked, and lock once the SR has
left the create stage (review and acknowledgement happen in the chat).

Separate from ``draft_preview`` on purpose: that card is Handover-shaped and
the portal's form bridge binds to it.
"""

from __future__ import annotations

from typing import Any

from app.agents.schemas import ceiling_schema as cs

# Values the form renders, in form order. Everything else in collected_data
# (ids, LT display strings, pending markers) stays out.
FORM_VALUE_KEYS: tuple[str, ...] = (
    "company_name",
    "tenant_profile_id",
    "lease_code",
    "lease_brand_mall",
    "brand",
    "mall",
    "city",
    "contracted_area",
    "unit_codes",
    *cs.CREATE_REQUIRED_FIELDS,
    *cs.CREATE_OPTIONAL_FIELDS,
    # HH:MM for the form's date-time pickers. Unset means the 09:00 default
    # the payload builder applies at submit (end falls back to start).
    "inspectionTime",
    "inspectionEndTime",
    "inspection_done_by",
)

_DATE_KEYS: frozenset[str] = frozenset({"startDate", "endDate"})

# Who holds the SR at each stage, for the locked form's banner.
_STAGE_BANNER: dict[str, str] = {
    cs.STAGE_SR_CREATED: "Submitted — with the RDD Project Manager for Level 1 review",
    cs.STAGE_L1_REVIEW: "With the RDD Project Manager — Level 1 review",
    cs.STAGE_L2_REVIEW: "With the FM Manager — Level 2 approval",
    cs.STAGE_REPORT_SUBMITTED: "Report submitted — waiting for the tenant to acknowledge",
    cs.STAGE_COMPLETED: "Completed",
    cs.STAGE_CANCELLED: "Cancelled",
}


def _company_name(tenant_profile_id: Any) -> str | None:
    """Company name for a tenant profile, from the lease records.

    The create payload stores ``company_name`` as the tenant_profile_id (the
    platform form does the same), so a reopened SR only knows the id.
    """
    if tenant_profile_id in (None, ""):
        return None
    from app.core.config import settings

    # Mock lease records only exist for the demo; live, the platform form shows
    # the company itself.
    if not settings.lease_lookup_use_mock:
        return None
    from app.agents.services.lease_lookup_service import list_mock_lease_records

    for record in list_mock_lease_records():
        if str(record.tenant_profile_id) == str(tenant_profile_id):
            return record.company_name or None
    return None


def _form_value(key: str, value: Any) -> Any:
    if key in _DATE_KEYS and isinstance(value, str):
        # Create collects YYYY-MM-DD; a reopened SR carries a full ISO string.
        return value[:10]
    return value


def build_ceiling_form_state(result_state: dict[str, Any]) -> dict[str, Any]:
    """Build the ``form_state`` payload from the graph's final state."""
    collected: dict[str, Any] = dict(result_state.get("collected_data") or {})
    backend_refs: dict[str, Any] = result_state.get("backend_refs") or {}
    stage = result_state.get("workflow_stage")

    values: dict[str, Any] = {}
    for key in FORM_VALUE_KEYS:
        value = collected.get(key)
        if value in (None, "", []):
            continue
        values[key] = _form_value(key, value)
    if "lease_code" not in values and collected.get("lease"):
        values["lease_code"] = collected["lease"]
    company = values.get("company_name")
    if company in (None, "") or str(company) == str(values.get("tenant_profile_id")):
        name = _company_name(values.get("tenant_profile_id"))
        if name:
            values["company_name"] = name

    documents = [
        {
            "document_id": doc.get("document_id"),
            "document_type": doc.get("document_type_id") or doc.get("document_type"),
            "file_name": doc.get("filename") or doc.get("file_name"),
        }
        for doc in (result_state.get("documents") or [])
        if isinstance(doc, dict)
    ]

    sr_id = backend_refs.get("sr_id")
    locked = bool(sr_id) or (stage not in (None, cs.STAGE_CREATE))
    missing = list(result_state.get("missing_fields") or [])
    # The graph keeps the last asked field after everything is collected; at
    # the review card nothing is being asked, so nothing is highlighted.
    active_field = result_state.get("active_field") if missing and not locked else None
    return {
        "agent": cs.AGENT_NAME,
        "sub_category": result_state.get("sub_category"),
        "stage": stage,
        "sr_id": sr_id,
        "locked": locked,
        "banner": _STAGE_BANNER.get(str(stage or "")),
        "values": values,
        "missing": missing,
        "active_field": active_field,
        "documents": documents,
    }


# Lease / company keys the platform form's LeaseBrandMall and TenantSelect bind
# to, beyond the Handover preview rows.
_DRAFT_LEASE_KEYS: tuple[tuple[str, str], ...] = (
    ("lease_brand_mall", "Lease"),
    ("lease_id", "Lease ID"),
    ("tenant_profile_id", "Tenant Profile"),
)


def ceiling_draft_fields(result_state: dict[str, Any]) -> list[dict[str, Any]]:
    """``draft_preview.fields`` for the platform's OCI / CCI form.

    The form uses the Handover field names, so the Handover preview rows are
    reused (including the ``startDateLT`` / ``endDateLT`` pairs the date-time
    pickers need). On top: the lease ids the form's lease and tenant selects
    bind to, ``company_name`` as the tenant_profile_id (what TenantSelect holds),
    and ``sub_category`` — only once open or close is resolved, because the
    portal redirects to that form as soon as it sees it.
    """
    from app.agents.graph.nodes.handover.preview_node import _fields_from_collected

    collected: dict[str, Any] = result_state.get("collected_data") or {}
    rows = _fields_from_collected(collected)
    present = {row["key"] for row in rows}
    for key, label in _DRAFT_LEASE_KEYS:
        value = collected.get(key)
        if key not in present and value not in (None, "", []):
            rows.append({"key": key, "label": label, "value": value})
    tenant_profile_id = collected.get("tenant_profile_id")
    if tenant_profile_id not in (None, ""):
        rows.append({"key": "company_name", "label": "Company", "value": str(tenant_profile_id)})
    sub_category = result_state.get("sub_category")
    if cs.is_resolved_sub_category(sub_category):
        rows.append({"key": "sub_category", "label": "Inspection", "value": sub_category})
    return rows
