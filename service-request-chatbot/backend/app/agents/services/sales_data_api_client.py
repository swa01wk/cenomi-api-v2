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


def unwrap_sales_data_rows(body: Any) -> list[dict[str, Any]]:
    """Normalise live Cenomi and mock GET /sales-data envelopes to a row list.

    Live: ``{ success, data: { list, total_count, ... } }``
    Mock: ``{ data: [ ...rows ] }`` or a bare list.
    """
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    if not isinstance(body, dict):
        return []
    data = body.get("data", body)
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        lst = data.get("list")
        if isinstance(lst, list):
            return [r for r in lst if isinstance(r, dict)]
    return []


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
        return unwrap_sales_data_rows(resp.json())

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

    async def update_sales_data_statuses(
        self, payloads: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Apply one PATCH per payload. Live Cenomi PATCH is a single object.

        Sequential on purpose (notifications + Yardi are per-row). Swap this
        method later if a bulk endpoint is added — callers stay the same.
        """
        results: list[dict[str, Any]] = []
        for payload in payloads:
            try:
                result = await self.update_sales_data_status(payload)
                results.append({
                    **payload,
                    "success": bool(result.get("success")),
                    "error": result.get("error"),
                })
            except Exception as exc:
                results.append({
                    **payload,
                    "success": False,
                    "error": str(exc),
                })
        return results

    # ── PUT /files ──────────────────────────────────────────────────────────

    async def upload_file(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str = "application/octet-stream",
        *,
        document_type_id: str | None = None,
        lease_id: int | None = None,
    ) -> dict[str, Any]:
        """Upload a file via PUT /files and return the document_id.

        Live Cenomi: ``PUT /api/v1/files?file_extension=&document_type_id=&lease_id=&file_name=``.
        Mock backend aliases the same path (and still serves ``PUT /files``).
        """
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        params: dict[str, Any] = {"file_name": filename}
        if ext:
            params["file_extension"] = ext
        if document_type_id:
            params["document_type_id"] = document_type_id
        if lease_id is not None:
            params["lease_id"] = lease_id

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.put(
                f"{self._base}/api/v1/files",
                headers=headers,
                params=params,
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


def _norm_lease_name(value: str | None) -> str:
    """Case-fold and collapse whitespace so CSV names can match platform names."""
    return " ".join(str(value or "").casefold().split())


def canonical_brand_name(backend_row: dict[str, Any]) -> str:
    """Brand on a sales-data row from mock or live Cenomi."""
    return str(
        backend_row.get("brand_name")
        or backend_row.get("brand")
        or ""
    ).strip()


def canonical_property_name(backend_row: dict[str, Any]) -> str:
    """Property / mall on a sales-data row from mock or live Cenomi."""
    return str(
        backend_row.get("property_name")
        or backend_row.get("mall_name")
        or backend_row.get("property")
        or backend_row.get("mall")
        or ""
    ).strip()


def _csv_name_matches_lease(csv_value: str | None, canonical: str) -> bool:
    """Empty CSV or empty platform name is not a mismatch; otherwise ignore case/spaces."""
    csv_n = _norm_lease_name(csv_value)
    can_n = _norm_lease_name(canonical)
    if not csv_n or not can_n:
        return True
    return csv_n == can_n


def _identity_mismatch_error(
    row: dict[str, Any],
    *,
    brand_ok: bool,
    property_ok: bool,
    expected_brand: str,
    expected_property: str,
) -> str:
    lease = row.get("lease_code") or ""
    csv_brand = (row.get("brand") or "").strip()
    csv_property = (row.get("property") or "").strip()
    parts: list[str] = []
    if not brand_ok:
        parts.append(
            f'Brand "{csv_brand}" does not match this lease '
            f'(expected "{expected_brand}")'
        )
    if not property_ok:
        parts.append(
            f'Property "{csv_property}" does not match this lease '
            f'(expected "{expected_property}")'
        )
    detail = "; ".join(parts) or "Brand/Property do not match this lease"
    return (
        f"{detail} for {lease}. "
        "Please correct Brand and Property to the names on this lease and upload the file again."
    )


def partition_rows_by_lease_access(
    csv_rows: list[dict[str, Any]],
    backend_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Partition CSV rows into Layer 2 buckets.

    Matching is by ``(lease_code, sales_period_start, sales_period_end)`` against
    GET /sales-data rows (mock or live Cenomi). Brand / Property, when the tenant
    filled them in, must match the lease's platform names ignoring case and
    extra spaces. Canonical platform spelling is kept on valid rows.

    Returns:
      valid, no_access, no_pending_row, identity_mismatch, accessible_lease_codes
    """
    accessible: set[str] = {r["lease_code"] for r in backend_rows}
    pending_index: dict[tuple[str, str, str], dict] = {}
    for r in backend_rows:
        if r["status"] in ("PENDING", "NEED_REUPLOAD"):
            key = (r["lease_code"], r["sales_period_start"], r["sales_period_end"])
            pending_index[key] = r

    valid: list[dict] = []
    no_access: list[dict] = []
    no_pending_row: list[dict] = []
    identity_mismatch: list[dict] = []

    for row in csv_rows:
        lc = row.get("lease_code", "")
        start = row.get("sales_period_start", "")
        end = row.get("sales_period_end", "")

        if lc not in accessible:
            no_access.append({**row, "_error": f"You don't have access to lease {lc}."})
            continue
        if (lc, start, end) not in pending_index:
            no_pending_row.append(
                {
                    **row,
                    "_error": (
                        f"No pending submission found for lease {lc} "
                        f"in period {start} – {end}."
                    ),
                }
            )
            continue

        backend_row = pending_index[(lc, start, end)]
        expected_brand = canonical_brand_name(backend_row)
        expected_property = canonical_property_name(backend_row)
        brand_ok = _csv_name_matches_lease(row.get("brand"), expected_brand)
        property_ok = _csv_name_matches_lease(row.get("property"), expected_property)
        if not brand_ok or not property_ok:
            identity_mismatch.append({
                **row,
                "_error": _identity_mismatch_error(
                    row,
                    brand_ok=brand_ok,
                    property_ok=property_ok,
                    expected_brand=expected_brand,
                    expected_property=expected_property,
                ),
            })
            continue

        valid.append({
            **row,
            "lease_id": backend_row["lease_id"],
            "sales_period_start": backend_row["sales_period_start"],
            "sales_period_end": backend_row["sales_period_end"],
            "current_status": backend_row["status"],
            "brand": expected_brand or (row.get("brand") or ""),
            "property": expected_property or (row.get("property") or ""),
        })

    return {
        "valid": valid,
        "no_access": no_access,
        "no_pending_row": no_pending_row,
        "identity_mismatch": identity_mismatch,
        "accessible_lease_codes": accessible,
    }
