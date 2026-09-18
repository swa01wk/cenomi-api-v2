"""Seed data for the mock Cenomi Sales Data backend.

Tenant.csv (tenant_profile_id 101) — Aug 2026:
  LC-1001 — PENDING (Aug 2026) and PENDING (Jul 2026, for month-mismatch demos)
  LC-1002 — PENDING
  LC-1003 — NEED_REUPLOAD (Sales Data team asked for sales data again)
  LC-1004 — NEED_UPLOAD_SUPPORTING_DOCUMENT
  LC-1005 — NEED_UPLOAD_AUDITED_SALES

Admin review (same tenant 101 — Sales Data Team Submitted tab):
  LC-2001 — SUBMITTED (July)
  LC-2002 — UPLOADED_SUPPORTING_DOCUMENT (July) — badge still reads Submitted; hover is specific
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
        "property_name": "Riyadh Park",
        "company_name": "Noon Fashion Trading Co",
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
        "id": "sd-008",
        "lease_id": 1,
        "lease_code": "LC-1001",
        "brand_name": "Noon Fashion",
        "mall_name": "Riyadh Park",
        "property_name": "Riyadh Park",
        "company_name": "Noon Fashion Trading Co",
        "sales_period_start": "2026-07-01",
        "sales_period_end": "2026-07-31",
        "status": "PENDING",
        "sales_amt": None,
        "tenant_profile_id": 101,
        "document_id": None,
        "comment_text": None,
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-01T00:00:00Z",
    },
    {
        "id": "sd-002",
        "lease_id": 2,
        "lease_code": "LC-1002",
        "brand_name": "Pure Gym",
        "mall_name": "Riyadh Park",
        "property_name": "Riyadh Park",
        "company_name": "Pure Gym Co",
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
        "property_name": "Mall of Arabia",
        "company_name": "Pure Gym Co",
        "sales_period_start": "2026-08-01",
        "sales_period_end": "2026-08-31",
        "status": "NEED_REUPLOAD",
        "sales_amt": 50000.0,
        "tenant_profile_id": 101,
        "document_id": None,
        "comment_text": "Please re-upload with correct figures.",
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-05T00:00:00Z",
    },
    {
        "id": "sd-006",
        "lease_id": 6,
        "lease_code": "LC-1004",
        "brand_name": "Noon Fashion",
        "mall_name": "Riyadh Park",
        "property_name": "Riyadh Park",
        "company_name": "Noon Fashion Trading Co",
        "sales_period_start": "2026-07-01",
        "sales_period_end": "2026-07-31",
        "status": "NEED_UPLOAD_SUPPORTING_DOCUMENT",
        "sales_amt": 72000.0,
        "tenant_profile_id": 101,
        "document_id": None,
        "comment_text": "Please attach the POS report for July.",
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-08-10T00:00:00Z",
    },
    {
        "id": "sd-007",
        "lease_id": 7,
        "lease_code": "LC-1005",
        "brand_name": "Pure Gym",
        "mall_name": "Mall of Arabia",
        "property_name": "Mall of Arabia",
        "company_name": "Pure Gym Co",
        "sales_period_start": "2026-07-01",
        "sales_period_end": "2026-07-31",
        "status": "NEED_UPLOAD_AUDITED_SALES",
        "sales_amt": 185000.0,
        "tenant_profile_id": 101,
        "document_id": None,
        "comment_text": "Please upload audited sales figures vs the submitted actuals.",
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-08-12T00:00:00Z",
    },
    # Admin-review rows (SUBMITTED — visible to SALES_DATA_APPROVER)
    # Same tenant as tenant.csv@cenomi.com (profile 101) so Ask upload is visible
    # after switching from Sales Data Team back to Tenant.
    {
        "id": "sd-004",
        "lease_id": 4,
        "lease_code": "LC-2001",
        "brand_name": "Centrepoint",
        "mall_name": "Riyadh Park",
        "property_name": "Riyadh Park",
        "company_name": "Centrepoint Co",
        "sales_period_start": "2026-07-01",
        "sales_period_end": "2026-07-31",
        "status": "SUBMITTED",
        "sales_amt": 320000.0,
        "tenant_profile_id": 101,
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
        "property_name": "Mall of Arabia",
        "company_name": "Starbucks Saudi Co",
        "sales_period_start": "2026-07-01",
        "sales_period_end": "2026-07-31",
        "status": "UPLOADED_SUPPORTING_DOCUMENT",
        "sales_amt": 95000.0,
        "tenant_profile_id": 101,
        "document_id": "doc-sup-2002",
        "comment_text": None,
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-08-03T00:00:00Z",
    },
]


def get_initial_state() -> list[dict]:
    return copy.deepcopy(_SEED_ROWS)


# Minimal valid PDF for LC-2002 (portal GET /document-view?document_id=doc-sup-2002)
_SEED_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R"
    b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n"
    b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    b"5 0 obj<</Length 79>>stream\n"
    b"BT /F1 16 Tf 72 720 Td (LC-2002 supporting document) Tj ET\n"
    b"endstream\nendobj\n"
    b"trailer<</Root 1 0 R>>\n"
    b"%%EOF\n"
)

SEEDED_SUPPORTING_DOC_ID = "doc-sup-2002"


def get_initial_files() -> dict:
    """In-memory file store keyed by document_id (mirrors SharePoint blob)."""
    return {
        SEEDED_SUPPORTING_DOC_ID: {
            "bytes": _SEED_PDF,
            "filename": "LC-2002-supporting-document.pdf",
            "content_type": "application/pdf",
        }
    }
