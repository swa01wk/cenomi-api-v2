"""Seed data for the mock Cenomi Sales Data backend.

Three demo leases for Aug 2026:
  LC-1001 — PENDING (billing_contact user)
  LC-1002 — PENDING (billing_contact user)
  LC-1003 — NEED_REUPLOAD (company_admin user)
"""

import copy
from datetime import datetime

_SEED_ROWS = [
    {
        "id": "sd-001",
        "lease_id": 1,
        "lease_code": "LC-1001",
        "brand_name": "Noon Fashion",
        "mall_name": "Riyadh Park",
        "sales_period_start": "2026-08-01",
        "sales_period_end": "2026-08-31",
        "status": "PENDING",
        "sales_amt": None,
        "tenant_profile_id": 101,
        "document_id": None,
        "comment_text": None,
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
    },
    {
        "id": "sd-002",
        "lease_id": 2,
        "lease_code": "LC-1002",
        "brand_name": "Pure Gym",
        "mall_name": "Riyadh Park",
        "sales_period_start": "2026-08-01",
        "sales_period_end": "2026-08-31",
        "status": "PENDING",
        "sales_amt": None,
        "tenant_profile_id": 101,
        "document_id": None,
        "comment_text": None,
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
    },
    {
        "id": "sd-003",
        "lease_id": 3,
        "lease_code": "LC-1003",
        "brand_name": "Pure Gym",
        "mall_name": "Mall of Arabia",
        "sales_period_start": "2026-08-01",
        "sales_period_end": "2026-08-31",
        "status": "NEED_REUPLOAD",
        "sales_amt": 50000.0,
        "tenant_profile_id": 102,
        "document_id": None,
        "comment_text": "Please re-upload with correct figures.",
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-05T00:00:00Z",
    },
    # Admin-review rows (SUBMITTED — visible to SALES_DATA_APPROVER)
    {
        "id": "sd-004",
        "lease_id": 4,
        "lease_code": "LC-2001",
        "brand_name": "Centrepoint",
        "mall_name": "Riyadh Park",
        "sales_period_start": "2026-07-01",
        "sales_period_end": "2026-07-31",
        "status": "SUBMITTED",
        "sales_amt": 320000.0,
        "tenant_profile_id": 103,
        "document_id": None,
        "comment_text": None,
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-08-02T00:00:00Z",
    },
    {
        "id": "sd-005",
        "lease_id": 5,
        "lease_code": "LC-2002",
        "brand_name": "Starbucks",
        "mall_name": "Mall of Arabia",
        "sales_period_start": "2026-07-01",
        "sales_period_end": "2026-07-31",
        "status": "SUBMITTED",
        "sales_amt": 95000.0,
        "tenant_profile_id": 104,
        "document_id": None,
        "comment_text": None,
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-08-03T00:00:00Z",
    },
]


def get_initial_state() -> list[dict]:
    return copy.deepcopy(_SEED_ROWS)
