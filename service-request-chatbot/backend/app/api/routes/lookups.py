"""Access-scoped dropdown lookups for the Ceiling Inspection split-screen POC.

``GET /api/v1/lookups/companies``      companies the caller can raise for
``GET /api/v1/lookups/leases``         leases, optionally for one company
``GET /api/v1/lookups/ceiling-form``   static options for the form

Scoping mirrors what the agent's own lease lookup sends to the platform: the
JWT's ``unique_property_ids``. A global admin, or a token with no property
scope, sees every lease — the platform's ``GetPermittedItems`` treats them the
same way. Data comes from the POC lease seed; the portal uses the platform's
``tenant-profiles`` and ``contracts`` endpoints instead.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.agents.schemas import ceiling_schema as cs
from app.agents.services.lease_lookup_service import LeaseRecord, list_mock_lease_records
from app.core.config import settings
from app.core.security import get_auth_context
from app.types.chat import AuthContext

router = APIRouter()


def permitted_leases(auth: AuthContext) -> list[LeaseRecord]:
    # These lookups feed the standalone demo app only. The platform form does its
    # own company / lease lookups, so in live mode there is nothing to serve.
    if not settings.lease_lookup_use_mock:
        return []
    records = list_mock_lease_records()
    if auth.is_global_admin or not auth.unique_property_ids:
        return records
    allowed = set(auth.unique_property_ids)
    return [r for r in records if r.property_id in allowed]


def _lease_option(record: LeaseRecord) -> dict[str, Any]:
    return {
        "lease_code": record.lease_code,
        "lease_brand_mall": record.lease_brand_mall,
        "brand": record.brand,
        "mall": record.mall,
        "city": record.city,
        "contracted_area": record.contracted_area,
        "unit_codes": record.unit_codes,
        "company_name": record.company_name,
        "tenant_profile_id": record.tenant_profile_id,
    }


@router.get("/lookups/companies", summary="Companies the caller can access")
async def list_companies(
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, list[dict[str, Any]]]:
    seen: dict[int, str] = {}
    for record in permitted_leases(auth):
        seen.setdefault(record.tenant_profile_id, record.company_name)
    companies = [
        {"tenant_profile_id": tid, "company_name": name}
        for tid, name in sorted(seen.items(), key=lambda item: item[1])
    ]
    return {"companies": companies}


@router.get("/lookups/leases", summary="Leases the caller can access")
async def list_leases(
    tenant_profile_id: int | None = Query(default=None, description="Only this company's leases."),
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, list[dict[str, Any]]]:
    records = permitted_leases(auth)
    if tenant_profile_id is not None:
        records = [r for r in records if r.tenant_profile_id == tenant_profile_id]
    return {"leases": [_lease_option(r) for r in records]}


@router.get("/lookups/ceiling-form", summary="Static options for the ceiling inspection form")
async def ceiling_form_options() -> dict[str, Any]:
    return {
        "sub_categories": [
            {"value": key, "label": config["label"], "short_label": config["short_label"]}
            for key, config in cs.CEILING_SUBCAT_CONFIG.items()
        ],
        "document_types": [{"value": cs.OTHER_DOC_TYPE, "label": "Inspection Other"}],
        "inspection_done_by": {
            "value": cs.INSPECTION_DONE_BY_ROLE_ID,
            "label": "RDD Project Manager",
        },
        "required_fields": list(cs.CREATE_REQUIRED_FIELDS),
        "optional_fields": list(cs.CREATE_OPTIONAL_FIELDS),
    }
