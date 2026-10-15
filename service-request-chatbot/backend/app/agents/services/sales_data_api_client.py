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


def _normalize_row_dates(row: dict[str, Any]) -> dict[str, Any]:
    """Bare-date sales_period_start/end on a single GET /sales-data row.

    Live cenomi-api returns these as a full datetime-with-offset
    ("2026-05-01T04:00:00+04:00"). Fixed at this one shared source instead
    of in each consuming node — that had already been done three separate
    times in three separate call sites (CSV/manual-entry validation,
    supporting-doc upload, admin review/approve) after the same raw string
    kept leaking into a PATCH/POST payload cenomi-api rejects (expects a
    plain date) and into every message shown to the user. Any code reading
    a row through this module now gets an already-clean date and never
    needs to re-derive or re-validate it.
    """
    if row.get("sales_period_start"):
        row["sales_period_start"] = normalize_period_date(row["sales_period_start"])
    if row.get("sales_period_end"):
        row["sales_period_end"] = normalize_period_date(row["sales_period_end"])
    return row


def unwrap_sales_data_rows(body: Any) -> list[dict[str, Any]]:
    """Normalise live Cenomi and mock GET /sales-data envelopes to a row list.

    Live: ``{ success, data: { list, total_count, ... } }``
    Mock: ``{ data: [ ...rows ] }`` or a bare list.
    """
    if isinstance(body, list):
        rows = [r for r in body if isinstance(r, dict)]
    elif not isinstance(body, dict):
        rows = []
    else:
        data = body.get("data", body)
        if isinstance(data, list):
            rows = [r for r in data if isinstance(r, dict)]
        elif isinstance(data, dict) and isinstance(data.get("list"), list):
            rows = [r for r in data["list"] if isinstance(r, dict)]
        else:
            rows = []
    return [_normalize_row_dates(r) for r in rows]


def extract_total_count(body: Any, fallback: int) -> int:
    """Pull ``total_count`` out of the live GET /sales-data envelope.

    Falls back to the given row-count length (mock backend / bare list
    shapes carry no total_count at all) so callers can still show an exact
    figure when the row list already is the whole result.
    """
    if isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, dict):
            total = data.get("total_count")
            if isinstance(total, int):
                return total
    return fallback


def _extract_backend_error_message(resp: httpx.Response) -> str:
    """Pull a clean, human-readable message out of a 4xx cenomi-api body.

    Never surfaces the raw response body to the user — that used to happen
    verbatim in update_sales_data_status's 400 handler (``error: resp.text``),
    which put a raw JSON/Go error string directly into the chat message.
    """
    try:
        body = resp.json()
    except ValueError:
        return "The request was rejected. Please try again."
    if not isinstance(body, dict):
        return "The request was rejected. Please try again."
    errors = body.get("errors") or body.get("detail", {}).get("errors") or []
    if isinstance(errors, list) and errors:
        messages = [
            str(e.get("message")) for e in errors
            if isinstance(e, dict) and e.get("message")
        ]
        if messages:
            return "; ".join(messages)
    for key in ("message", "error", "detail"):
        value = body.get(key)
        if isinstance(value, str) and value:
            return value
    return "The request was rejected. Please try again."


def unwrap_file_upload_response(body: Any) -> dict[str, Any]:
    """Flatten live Cenomi PUT /files ``{ success, data }`` to a dict with document_id.

    Live cenomi-api returns::

        {"success": true, "data": {"document_id": "<uuid>", "file_path": "...", ...}}

    Mock backends and already-flat payloads return ``document_id`` at the top
    level. Callers (supporting-doc node) read ``result["document_id"]``.
    """
    if not isinstance(body, dict):
        return {}
    data = body.get("data")
    if isinstance(data, dict):
        merged = {**body, **data}
        doc_id = data.get("document_id") or data.get("id")
        if doc_id:
            merged["document_id"] = doc_id
        return merged
    return body


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
        lease_code: str | list[str] | None = None,
        page: int = 1,
        limit: int = 100,
        all_my_companies: bool = False,
        sales_period_start: str | None = None,
        sales_period_end: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return sales-data rows visible to the authenticated user.

        With ``disable_pagination=True`` (Layer 2 validation), all rows are
        returned at once so the agent can check existence and access in one call.

        ``all_my_companies`` (default False) keeps the lookup scoped to the
        company currently active in the portal's company switcher — the AI
        session token is minted with that tenant_profile_id, and the
        frontend re-mints it on a company switch — so the agent only ever
        lists, validates and submits leases of the active company, the same
        as the Sales Data pages. Pass True only for a deliberate
        cross-company lookup.
        """
        resp = await self._get_sales_data_raw(
            disable_pagination=disable_pagination,
            status=status,
            lease_code=lease_code,
            page=page,
            limit=limit,
            all_my_companies=all_my_companies,
            sales_period_start=sales_period_start,
            sales_period_end=sales_period_end,
        )
        return unwrap_sales_data_rows(resp.json())

    async def get_sales_data_page(
        self,
        *,
        status: str | None = None,
        lease_code: str | list[str] | None = None,
        page: int = 1,
        limit: int = 10,
        all_my_companies: bool = False,
        sales_period_start: str | None = None,
        sales_period_end: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Like get_sales_data, but also returns the real total_count.

        For a bounded/paginated listing (admin review's table) where the
        displayed "N submissions" figure needs to be exact even though only
        one page of rows is fetched — unlike get_sales_data, which discards
        total_count and only returns the row list.
        """
        resp = await self._get_sales_data_raw(
            disable_pagination=False,
            status=status,
            lease_code=lease_code,
            page=page,
            limit=limit,
            all_my_companies=all_my_companies,
            sales_period_start=sales_period_start,
            sales_period_end=sales_period_end,
        )
        body = resp.json()
        rows = unwrap_sales_data_rows(body)
        return rows, extract_total_count(body, fallback=len(rows))

    async def _get_sales_data_raw(
        self,
        *,
        disable_pagination: bool,
        status: str | None,
        lease_code: str | list[str] | None,
        page: int,
        limit: int,
        all_my_companies: bool,
        sales_period_start: str | None = None,
        sales_period_end: str | None = None,
    ) -> httpx.Response:
        params: dict[str, Any] = {"page": page, "limit": limit}
        if disable_pagination:
            params["disable_pagination"] = "true"
        if status:
            params["status"] = status
        if lease_code:
            # cenomi-api parses this as r.URL.Query()["lease_code"] — a
            # repeated-query-param slice, not comma-split like status — so a
            # list value here must produce ?lease_code=A&lease_code=B, which
            # httpx does natively when the params value is a list.
            params["lease_code"] = lease_code
        if all_my_companies:
            params["all_my_companies"] = "true"
        # YYYY-MM-DD; cenomi-api filters start >= from AND end <= to.
        if sales_period_start:
            params["sales_period_start"] = sales_period_start
        if sales_period_end:
            params["sales_period_end"] = sales_period_end

        # all_my_companies=true made this call 15-30s+ (vs ~200ms); it is now
        # off by default, but the timeout stays generous for large
        # disable_pagination listings.
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.get(
                f"{self._base}/api/v1/sales-data",
                headers=self._headers(),
                params=params,
            )

        resp.raise_for_status()
        return resp

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

        if resp.status_code >= 500:
            log.error(
                "submit_sales_data.backend_5xx",
                status_code=resp.status_code,
                response_body=resp.text[:2000],
                request_payload=rows,
            )

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
            log.error(
                "update_sales_data_status.backend_400",
                response_body=resp.text[:2000],
                request_payload=payload,
            )
            message = _extract_backend_error_message(resp)
            return {"success": False, "error": message}

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
        lease_code: str | None = None,
    ) -> dict[str, Any]:
        """Upload a file via PUT /files and return a flat dict with document_id.

        Live Cenomi expects **raw binary** in the body (same as the portal and
        the ops-agent platform client), not multipart. Query string carries
        ``file_extension``, ``document_type_id``, ``lease_id`` / ``lease_code``,
        ``file_name``. The JSON envelope is ``{ success, data: { document_id } }``.
        """
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if content_type:
            headers["Content-Type"] = content_type

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        params: dict[str, Any] = {"file_name": filename}
        if ext:
            params["file_extension"] = ext
        if document_type_id:
            params["document_type_id"] = document_type_id
        if lease_id is not None and str(lease_id).strip() not in ("", "0", "None"):
            params["lease_id"] = lease_id
        if lease_code:
            params["lease_code"] = lease_code

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.put(
                f"{self._base}/api/v1/files",
                headers=headers,
                params=params,
                content=file_bytes,
            )

        resp.raise_for_status()
        return unwrap_file_upload_response(resp.json())

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


def normalize_period_date(value: str | None) -> str:
    """Reduce any date-ish string to bare YYYY-MM-DD.

    Handles a live cenomi-api row's raw date string, which may carry a time
    component / offset (e.g. "2026-08-01T04:00:00+04:00") unlike the CSV
    side, which sales_data_csv.parse_sales_period_date already normalizes to
    YYYY-MM-DD.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    for sep in ("T", " "):
        if sep in raw:
            raw = raw.split(sep, 1)[0]
            break
    return raw.replace("/", "-")


def _period_year_month(value: str | None) -> str:
    """First 7 chars (YYYY-MM) of a normalized period date.

    A live PENDING row is not reliably a full calendar-month range — it can
    carry a single-day placeholder as both start and end (observed: a lease
    "December 2024" PENDING row with sales_period_start ==
    sales_period_end == 2024-12-31, not 2024-12-01..2024-12-31). Matching on
    the exact (start, end) tuple therefore fails for real rows even when the
    tenant's CSV is for the right lease and month. pending_months.py's own
    "December 2024" label is built the same way — from just the year-month
    of sales_period_start — so Layer 2 matching mirrors that granularity
    instead of demanding an exact date range.
    """
    return normalize_period_date(value)[:7]


def canonical_brand_name(backend_row: dict[str, Any]) -> str:
    """Brand on a sales-data row from mock or live Cenomi."""
    return str(
        backend_row.get("brand_name")
        or backend_row.get("brand")
        or ""
    ).strip()


def canonical_company_name(backend_row: dict[str, Any]) -> str:
    """Company on a sales-data row from mock or live Cenomi."""
    return str(
        backend_row.get("company_name")
        or backend_row.get("company")
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
    # Lease codes are compared case/whitespace-insensitively, same as
    # brand/property below — a manually-typed or LLM-extracted lease code
    # ("T0106013", stray spaces, ...) must match the platform's canonical
    # casing ("t0106013") or every legitimately accessible lease falsely
    # reports "no access".
    accessible: set[str] = {_norm_lease_name(r["lease_code"]) for r in backend_rows}
    pending_index: dict[tuple[str, str], dict] = {}
    for r in backend_rows:
        if r["status"] in ("PENDING", "NEED_REUPLOAD"):
            key = (_norm_lease_name(r["lease_code"]), _period_year_month(r["sales_period_start"]))
            pending_index[key] = r

    valid: list[dict] = []
    no_access: list[dict] = []
    no_pending_row: list[dict] = []
    identity_mismatch: list[dict] = []

    for row in csv_rows:
        lc = row.get("lease_code", "")
        start = row.get("sales_period_start", "")
        end = row.get("sales_period_end", "")
        norm_key = (_norm_lease_name(lc), _period_year_month(start))

        if _norm_lease_name(lc) not in accessible:
            no_access.append({**row, "_error": f"You don't have access to lease {lc} under your current company."})
            continue
        if norm_key not in pending_index:
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

        backend_row = pending_index[norm_key]
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
            # Canonical casing, not whatever the user typed/LLM extracted
            # ("T0106013" vs "t0106013") — matching accessible/pending_index
            # keys are normalized, but the submission payload should carry
            # the platform's own spelling.
            "lease_code": backend_row["lease_code"],
            "lease_id": backend_row["lease_id"],
            # Bare YYYY-MM-DD, not the raw "2024-12-31T04:00:00+04:00" the
            # live API returns — that datetime-with-offset string was being
            # forwarded verbatim into the review table and the actual
            # POST /sales-data submission payload, which cenomi-api rejected
            # with a 400 ("sales period start date doesn't match the
            # pending record") since it expects a plain date.
            "sales_period_start": normalize_period_date(backend_row["sales_period_start"]),
            "sales_period_end": normalize_period_date(backend_row["sales_period_end"]),
            "current_status": backend_row["status"],
            "brand": expected_brand or (row.get("brand") or ""),
            "property": expected_property or (row.get("property") or ""),
            "company": canonical_company_name(backend_row),
        })

    return {
        "valid": valid,
        "no_access": no_access,
        "no_pending_row": no_pending_row,
        "identity_mismatch": identity_mismatch,
        "accessible_lease_codes": accessible,
    }
