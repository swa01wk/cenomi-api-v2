"""Mock GET / POST / PATCH /api/v1/sales-data endpoints.

Simulates the real cenomi-api behaviour:
- GET  /api/v1/sales-data              — returns rows for the authenticated tenant
- POST /api/v1/sales-data              — bulk submit (all-or-nothing transaction)
- PATCH /api/v1/sales-data             — status update by admin or tenant (reupload)
- GET  /api/v1/sales-data/template     — returns a CSV template
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory store — imported from app state
# ---------------------------------------------------------------------------

def _get_db(request: Request) -> list[dict]:
    return request.app.state.db


# ---------------------------------------------------------------------------
# GET /api/v1/sales-data
# ---------------------------------------------------------------------------

@router.get("/api/v1/sales-data")
async def get_sales_data(
    request: Request,
    disable_pagination: bool = Query(default=False, alias="disable_pagination"),
    status: str | None = Query(default=None),
    lease_code: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1, le=100),
):
    """Return sales data rows.

    Behaviour matches real API:
    - The ``Authorization`` header determines the role.
    - TENANT roles: only rows whose ``tenant_profile_id`` matches the token.
    - ADMIN / SALES_DATA_APPROVER: all rows.
    - ``status`` filter applied if provided.
    - ``disable_pagination=true`` returns all rows without paging.
    """
    db: list[dict] = _get_db(request)
    role, tenant_id = _parse_auth(request)

    rows = list(db)

    # Scope to tenant
    if role in ("TENANT_CSV_UPLOADER", "TENANT_DOC_UPLOADER"):
        rows = [r for r in rows if r["tenant_profile_id"] == tenant_id]

    # Filters
    if status:
        rows = [r for r in rows if r["status"] == status]
    if lease_code:
        rows = [r for r in rows if r["lease_code"] == lease_code]

    total = len(rows)

    if not disable_pagination:
        offset = (page - 1) * limit
        rows = rows[offset: offset + limit]

    return {
        "data": rows,
        "total": total,
        "page": page,
        "limit": limit,
    }


# ---------------------------------------------------------------------------
# POST /api/v1/sales-data  (bulk submit — all-or-nothing)
# ---------------------------------------------------------------------------

@router.post("/api/v1/sales-data")
async def post_sales_data(request: Request, body: list[dict]):
    """Bulk-submit sales data rows.

    Validates all rows against PENDING / NEED_REUPLOAD entries in the DB.
    If ANY row fails, the entire batch is rejected (HTTP 400, per-row errors).
    On success, all matching rows are updated to SUBMITTED / RE_UPLOADED.
    """
    db: list[dict] = _get_db(request)
    role, tenant_id = _parse_auth(request)

    if role not in ("TENANT_CSV_UPLOADER", "TENANT_DOC_UPLOADER", "ADMIN"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    errors: list[dict] = []

    for i, row in enumerate(body):
        lease_code = row.get("lease_code", "")
        start = row.get("sales_period_start", "")
        end = row.get("sales_period_end", "")
        sales_amt = row.get("sales_amt")

        # Find matching pending row
        match = next(
            (
                r for r in db
                if r["lease_code"] == lease_code
                and r["sales_period_start"] == start
                and r["sales_period_end"] == end
                and r["status"] in ("PENDING", "NEED_REUPLOAD")
                and (role == "ADMIN" or r["tenant_profile_id"] == tenant_id)
            ),
            None,
        )

        if match is None:
            errors.append({
                "index": i,
                "field": "lease_code,sales_period_start,sales_period_end",
                "code": "no_pending_row",
                "message": (
                    f"No pending submission found for lease {lease_code} "
                    f"in period {start} – {end}."
                ),
            })
        elif not sales_amt or float(sales_amt) <= 0:
            errors.append({
                "index": i,
                "field": "sales_amt",
                "code": "invalid_amount",
                "message": f"Sales amount must be greater than 0 (got {sales_amt}).",
            })

    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})

    # All rows valid — commit
    now = datetime.utcnow().isoformat() + "Z"
    for row in body:
        lease_code = row["lease_code"]
        start = row["sales_period_start"]
        end = row["sales_period_end"]
        match = next(
            (
                r for r in db
                if r["lease_code"] == lease_code
                and r["sales_period_start"] == start
                and r["sales_period_end"] == end
                and r["status"] in ("PENDING", "NEED_REUPLOAD")
            ),
            None,
        )
        if match:
            match["sales_amt"] = float(row["sales_amt"])
            match["status"] = "SUBMITTED" if match["status"] == "PENDING" else "RE_UPLOADED"
            match["updated_at"] = now

    return ""


# ---------------------------------------------------------------------------
# PATCH /api/v1/sales-data  (status update by admin)
# ---------------------------------------------------------------------------

@router.patch("/api/v1/sales-data")
async def patch_sales_data(request: Request, body: dict):
    """Update a single sales data row's status (admin approval / action request)."""
    db: list[dict] = _get_db(request)
    role, tenant_id = _parse_auth(request)

    lease_id = body.get("lease_id")
    new_status = body.get("status")
    comment = body.get("comment_text")
    document_id = body.get("document_id")

    if not lease_id or not new_status:
        raise HTTPException(status_code=400, detail="lease_id and status are required")

    match = next((r for r in db if r["lease_id"] == lease_id), None)
    if not match:
        raise HTTPException(status_code=404, detail=f"No row found for lease_id {lease_id}")

    # Role check
    _ADMIN_STATUSES = {"APPROVED", "NEED_REUPLOAD", "NEED_UPLOAD_SUPPORTING_DOCUMENT",
                       "NEED_UPLOAD_AUDITED_SALES"}
    _TENANT_STATUSES = {"UPLOADED_SUPPORTING_DOCUMENT", "RE_UPLOADED", "UPLOADED_AUDITED_SALES"}

    if new_status in _ADMIN_STATUSES and role not in ("SALES_DATA_APPROVER", "ADMIN"):
        raise HTTPException(status_code=403, detail="Only admins can set this status")
    if new_status in _TENANT_STATUSES and role not in (
        "TENANT_DOC_UPLOADER", "TENANT_CSV_UPLOADER", "ADMIN"
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    match["status"] = new_status
    if comment:
        match["comment_text"] = comment
    if document_id:
        match["document_id"] = document_id
    match["updated_at"] = datetime.utcnow().isoformat() + "Z"

    return ""


# ---------------------------------------------------------------------------
# GET /api/v1/sales-data/template
# ---------------------------------------------------------------------------

@router.get("/api/v1/sales-data/template")
async def get_template(request: Request):
    """Return a CSV template with the required headers and example rows."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["lease_code", "sales_period_start", "sales_period_end", "sales_amt"])
    writer.writerow(["LC-1001", "2026-08-01", "2026-08-31", "85000"])
    writer.writerow(["LC-1002", "2026-08-01", "2026-08-31", "120000"])
    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=sales_data_template.csv"},
    )


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

# Static map kept for manual curl testing with the old tokens.
_STATIC_TOKEN_MAP: dict[str, tuple[str, int]] = {
    "demo-tenant-csv":  ("TENANT_CSV_UPLOADER", 101),
    "demo-tenant-doc":  ("TENANT_DOC_UPLOADER", 102),
    "demo-admin":       ("SALES_DATA_APPROVER", 999),
}

# Role → tenant_profile_id (must match seed_data.py tenant_profile_id values)
_ROLE_TENANT_ID: dict[str, int] = {
    "TENANT_CSV_UPLOADER":  101,
    "TENANT_DOC_UPLOADER":  102,
    "SALES_DATA_APPROVER":  999,
}

_DEFAULT_ROLE = ("TENANT_CSV_UPLOADER", 101)


def _parse_auth(request: Request) -> tuple[str, int]:
    """Extract (role, tenant_profile_id) from the Authorization header token.

    Strategy (in priority order):
    1. Static map — for manual curl testing with old demo tokens.
    2. JWT decode (no signature verification) — extracts `roles[0]` claim
       from the real JWT issued by the AI backend.  Signature is NOT verified
       here because the mock backend does not share the AI backend's
       JWT_SECRET_KEY; it trusts any token forwarded by the AI backend.
    3. Fallback to TENANT_CSV_UPLOADER / tenant_profile_id 101.
    """
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()

    # 1. Static map lookup (old demo tokens)
    if token in _STATIC_TOKEN_MAP:
        return _STATIC_TOKEN_MAP[token]

    # 2. Attempt JWT decode without verification
    try:
        import base64
        import json as _json

        # JWT is header.payload.signature — decode the payload part
        parts = token.split(".")
        if len(parts) == 3:
            # Add padding if needed
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = _json.loads(base64.urlsafe_b64decode(padded))
            roles: list = payload.get("roles", [])
            if roles:
                first_role = roles[0]
                tenant_id = _ROLE_TENANT_ID.get(first_role, 101)
                return (first_role, tenant_id)
    except Exception:
        pass

    # 3. Fallback
    return _DEFAULT_ROLE
