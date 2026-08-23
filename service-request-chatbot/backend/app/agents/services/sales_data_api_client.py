"""HTTP client for the Cenomi Sales Data API (cenomi-api).

In the demo environment this client calls the mock-backend on port 8001.
In production it calls the real cenomi-api (SERVICE_REQUEST_API_BASE_URL).

Configuration
-------------
SALES_DATA_API_BASE_URL  — base URL of the sales-data API server.
                           Defaults to http://localhost:8001 (mock backend).

All calls forward the user's Bearer token from the inbound request so that
the backend can apply per-tenant scoping.
"""

from __future__ import annotations

import io
import logging
import os
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base URL
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL = "http://localhost:8001"


def _base_url() -> str:
    return os.getenv("SALES_DATA_API_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class SalesDataApiClient:
    """Thin HTTP wrapper around the Cenomi sales-data endpoints.

    All methods accept a ``bearer_token`` parameter and forward it as the
    ``Authorization: Bearer <token>`` header.  When ``bearer_token`` is None
    the header is omitted (useful for tests).
    """

    def __init__(self, bearer_token: str | None = None):
        self._token = bearer_token
        self._base = _base_url()

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    # ── GET /api/v1/sales-data ──────────────────────────────────────────────

    async def get_sales_data(
        self,
        *,
        disable_pagination: bool = False,
        status: str | None = None,
        lease_code: str | None = None,
        page: int = 1,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return sales-data rows visible to the authenticated user.

        With ``disable_pagination=True`` (Layer 2 validation), all rows are
        returned at once so the agent can check existence and access in one call.
        """
        params: dict[str, Any] = {"page": page, "limit": limit}
        if disable_pagination:
            params["disable_pagination"] = "true"
        if status:
            params["status"] = status
        if lease_code:
            params["lease_code"] = lease_code

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self._base}/api/v1/sales-data",
                headers=self._headers(),
                params=params,
            )

        resp.raise_for_status()
        body = resp.json()
        return body.get("data", body) if isinstance(body, dict) else body

    # ── POST /api/v1/sales-data ─────────────────────────────────────────────

    async def submit_sales_data(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Bulk-submit sales data rows (all-or-nothing).

        Returns ``{"success": True}`` on HTTP 200.
        Raises ``httpx.HTTPStatusError`` with per-row ``errors`` on HTTP 400.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._base}/api/v1/sales-data",
                headers=self._headers(),
                json=rows,
            )

        if resp.status_code == 400:
            body = resp.json()
            errors = body.get("errors") or body.get("detail", {}).get("errors", [])
            return {"success": False, "errors": errors}

        resp.raise_for_status()
        return {"success": True}

    # ── PATCH /api/v1/sales-data ────────────────────────────────────────────

    async def update_sales_data_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Update a single row's status (admin approval / tenant reupload)."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.patch(
                f"{self._base}/api/v1/sales-data",
                headers=self._headers(),
                json=payload,
            )

        if resp.status_code == 400:
            return {"success": False, "error": resp.text}

        resp.raise_for_status()
        return {"success": True}

    # ── PUT /files ──────────────────────────────────────────────────────────

    async def upload_file(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        """Upload a file and return the document_id."""
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.put(
                f"{self._base}/files",
                headers=headers,
                files={"file": (filename, io.BytesIO(file_bytes), content_type)},
            )

        resp.raise_for_status()
        return resp.json()

    # ── GET /api/v1/sales-data/template ────────────────────────────────────

    async def get_template(self) -> bytes:
        """Download the CSV template."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self._base}/api/v1/sales-data/template",
                headers=self._headers(),
            )
        resp.raise_for_status()
        return resp.content


# ---------------------------------------------------------------------------
# Layer 2 validation helpers
# ---------------------------------------------------------------------------


def partition_rows_by_lease_access(
    csv_rows: list[dict[str, Any]],
    backend_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Partition CSV rows into (valid, no_access, no_pending_row) buckets.

    Args:
        csv_rows: Rows from the parsed/validated CSV.
                  Each row must have: lease_code, sales_period_start, sales_period_end.
        backend_rows: Rows returned by GET /api/v1/sales-data (user-scoped).

    Returns a dict:
      {
        "valid":          [rows that have a matching PENDING/NEED_REUPLOAD backend row],
        "no_access":      [rows whose lease_code is not in any backend row],
        "no_pending_row": [rows accessible but no PENDING/NEED_REUPLOAD for this period],
        "accessible_lease_codes": set of lease codes visible to this user,
      }
    """
    accessible: set[str] = {r["lease_code"] for r in backend_rows}
    # Index backend rows by (lease_code, start, end)
    pending_index: dict[tuple[str, str, str], dict] = {}
    for r in backend_rows:
        if r["status"] in ("PENDING", "NEED_REUPLOAD"):
            key = (r["lease_code"], r["sales_period_start"], r["sales_period_end"])
            pending_index[key] = r

    valid: list[dict] = []
    no_access: list[dict] = []
    no_pending_row: list[dict] = []

    for row in csv_rows:
        lc = row.get("lease_code", "")
        start = row.get("sales_period_start", "")
        end = row.get("sales_period_end", "")

        if lc not in accessible:
            no_access.append({**row, "_error": f"You don't have access to lease {lc}."})
        elif (lc, start, end) not in pending_index:
            no_pending_row.append(
                {
                    **row,
                    "_error": (
                        f"No pending submission found for lease {lc} "
                        f"in period {start} – {end}."
                    ),
                }
            )
        else:
            # Enrich with backend row data (lease_id, exact dates for PATCH)
            backend_row = pending_index[(lc, start, end)]
            valid.append({
                **row,
                "lease_id": backend_row["lease_id"],
                "sales_period_start": backend_row["sales_period_start"],
                "sales_period_end": backend_row["sales_period_end"],
                "current_status": backend_row["status"],
            })

    return {
        "valid": valid,
        "no_access": no_access,
        "no_pending_row": no_pending_row,
        "accessible_lease_codes": accessible,
    }
