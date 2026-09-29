"""Lease / Tenant API integration — adapter, models, and mock implementation.

Architecture
------------
- ``LeaseRecord``            — canonical result shape (matches the upstream API).
- ``LeaseLookupQuery``       — search parameters accepted by both adapters.
- ``LeaseLookupResult``      — wraps matches plus tracing metadata (latency, status
                               code, raw payloads, error).
- ``AbstractLeaseLookupService`` — adapter interface; swap without touching the node.
- ``HttpLeaseLookupService`` — production adapter; hits ``LEASE_TENANT_API_BASE_URL``.
- ``MockLeaseLookupService`` — in-memory POC adapter; zero external dependencies.

Search priority (``HttpLeaseLookupService`` and ``MockLeaseLookupService``)
---------------------------------------------------------------------------
1. ``lease_code``  (exact)
2. ``brand`` + ``mall``  (both present)
3. ``brand`` only
4. ``company`` only (tenant company name — platform ``?company=``)
5. ``mall`` only
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, model_validator

from app.core.config import settings
from app.core.request_context import get_current_bearer_token

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class LeaseRecord(BaseModel):
    """Canonical lease result shape returned by the Lease/Tenant API."""

    lease_code: str
    lease_id: int
    contract_id: int
    brand: str
    brand_id: int
    mall: str
    property_id: int
    tenant_profile_id: int
    unit_codes: list[str]
    contracted_area: float
    city: str
    lease_brand_mall: str
    # Optional — present when platform returns tenant company name
    company_name: str = ""
    # Base milestone dates from the lease contract (YYYY-MM-DD ISO strings,
    # empty when the platform returns null).  Used by FOS/FOE/Trading agents
    # to offer a pre-filled "original date" so the user can confirm or correct it.
    base_fitout_start_date: str = ""
    base_fitout_end_date: str = ""
    base_trading_date: str = ""
    # Confirmed actual dates on the lease (YYYY-MM-DD). Empty when not yet set.
    actual_handover_date: str = ""
    actual_shop_handover_date: str = ""
    actual_fitout_start_date: str = ""
    actual_fitout_end_date: str = ""
    actual_trading_date: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalise_base_dates(cls, values: dict) -> dict:
        """Map common platform field name variants to the canonical base_* names.

        The platform API may return these milestone dates under different key
        names (e.g. ``fitout_start_date``, ``planned_fitout_start_date``).
        This validator captures them before field assignment so they are not
        silently dropped by Pydantic's extra-field policy.
        """
        def _pick(primary: str, *fallbacks: str) -> str:
            for key in (primary, *fallbacks):
                val = values.get(key)
                if val and str(val).strip():
                    return str(val).strip()
            return ""

        if not values.get("base_fitout_start_date"):
            v = _pick(
                "base_fitout_start_date",
                "fitout_start_date",
                "planned_fitout_start_date",
                "fitoutStartDate",
                "plannedFitoutStartDate",
            )
            if v:
                values["base_fitout_start_date"] = v

        if not values.get("base_fitout_end_date"):
            v = _pick(
                "base_fitout_end_date",
                "fitout_end_date",
                "planned_fitout_end_date",
                "fitoutEndDate",
                "plannedFitoutEndDate",
            )
            if v:
                values["base_fitout_end_date"] = v

        if not values.get("base_trading_date"):
            v = _pick(
                "base_trading_date",
                "trading_date",
                "planned_trading_date",
                "tradingDate",
                "plannedTradingDate",
            )
            if v:
                values["base_trading_date"] = v

        if not values.get("actual_shop_handover_date"):
            v = _pick(
                "actual_shop_handover_date",
                "actual_handover_date",
                "actualShopHandoverDate",
                "actualHandoverDate",
            )
            if v:
                values["actual_shop_handover_date"] = v
        if not values.get("actual_handover_date"):
            v = _pick(
                "actual_handover_date",
                "actual_shop_handover_date",
                "actualHandoverDate",
                "actualShopHandoverDate",
            )
            if v:
                values["actual_handover_date"] = v
        if not values.get("actual_fitout_start_date"):
            v = _pick(
                "actual_fitout_start_date",
                "actualFitoutStartDate",
            )
            if v:
                values["actual_fitout_start_date"] = v
        if not values.get("actual_fitout_end_date"):
            v = _pick(
                "actual_fitout_end_date",
                "actualFitoutEndDate",
            )
            if v:
                values["actual_fitout_end_date"] = v
        if not values.get("actual_trading_date"):
            v = _pick(
                "actual_trading_date",
                "actualTradingDate",
            )
            if v:
                values["actual_trading_date"] = v

        return values


class LeaseLookupQuery(BaseModel):
    """Search parameters for a lease lookup — at least one field must be set."""

    lease_code: str | None = None
    brand: str | None = None
    mall: str | None = None
    # Tenant company name — forwarded as platform ``company`` query param
    # (ILIKE against tenant_profiles.company_name).
    company: str | None = None
    # Optional: restrict results to the authenticated user's properties.
    # Passed as a repeated query param to the real Lease/Tenant API so that
    # a Mall Manager at Jawharat Jeddah cannot see leases from other malls.
    # Ignored by MockLeaseLookupService (mock data is already scoped).
    property_ids: list[int] | None = None
    # purpose=fitout_confirm | fitout_end | trading_confirm tells the platform
    # to skip the "no existing handover SR" eligibility check.  Required for
    # FOS/FOE/trading agents whose leases always have an existing handover SR.
    purpose: str | None = None

    def has_identifiers(self) -> bool:
        """Return True when at least one search field is non-empty."""
        return bool(self.lease_code or self.brand or self.mall or self.company)


@dataclass
class LeaseLookupResult:
    """Output of a single lease lookup call — matches plus full trace metadata."""

    matches: list[LeaseRecord]
    endpoint: str
    request_payload: dict[str, Any]
    response_payload: dict[str, Any] | None
    latency_ms: int
    status_code: int | None
    error: str | None = None
    # Platform reason when matches are empty, e.g. not_found / handover_exists.
    reason: str | None = None


# ---------------------------------------------------------------------------
# Abstract adapter
# ---------------------------------------------------------------------------


class AbstractLeaseLookupService(ABC):
    """Interface for lease lookup backends; swap real ↔ mock without node changes."""

    @abstractmethod
    async def lookup(self, query: LeaseLookupQuery) -> LeaseLookupResult:
        """Resolve leases matching *query*; always returns a ``LeaseLookupResult``."""


# ---------------------------------------------------------------------------
# HTTP adapter (production)
# ---------------------------------------------------------------------------

_MAX_RESPONSE_BYTES = 32_768  # cap raw body stored in trace payload


class HttpLeaseLookupService(AbstractLeaseLookupService):
    """Production adapter — issues a single ``GET /leases`` against the tenant API.

    Parameters
    ----------
    base_url:
        Base URL of the Lease/Tenant API.  Defaults to
        ``settings.lease_tenant_api_base_url``.
    timeout:
        ``httpx`` request timeout in seconds.
    """

    _ENDPOINT_PATH = "/leases"

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = (base_url or settings.lease_tenant_api_base_url or "").rstrip("/")
        self._timeout = timeout

    async def lookup(self, query: LeaseLookupQuery) -> LeaseLookupResult:
        params = self._build_params(query)
        endpoint = f"{self._base_url}{self._ENDPOINT_PATH}"
        bearer = get_current_bearer_token()
        headers: dict[str, str] = {}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        else:
            log.warning(
                "lease_lookup.http.missing_bearer",
                endpoint=endpoint,
                hint="cenomi-api GET /leases requires Authorization: Bearer <ai_token>",
            )
        # Bearer-only auth: cenomi-api-2's ValidateAccessToken now accepts
        # AI-session HS256 tokens (PR 4472 cherry-pick), so the earlier
        # x-internal-api-token workaround / NewLeaseInternalHandler wiring
        # has been removed on the platform side. The canonical
        # SearchHandoverLeases route inside the private group runs the
        # per-user RBAC scope via GetPermittedItems — the audit trail
        # correctly attributes the lookup to the caller.
        request_payload: dict[str, Any] = {
            "endpoint": endpoint,
            "params": params,
            "has_authorization": bool(bearer),
        }

        wall_start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(endpoint, params=params, headers=headers)
            latency_ms = int((time.monotonic() - wall_start) * 1000)

            raw_text = response.text[:_MAX_RESPONSE_BYTES]
            try:
                body: dict[str, Any] | None = response.json()
            except Exception:
                body = {"_raw": raw_text}

            if response.status_code != 200:
                log.warning(
                    "lease_lookup.http.non_200",
                    status_code=response.status_code,
                    endpoint=endpoint,
                )
                return LeaseLookupResult(
                    matches=[],
                    endpoint=endpoint,
                    request_payload=request_payload,
                    response_payload=body,
                    latency_ms=latency_ms,
                    status_code=response.status_code,
                    error=f"HTTP {response.status_code}",
                )

            matches = self._parse_matches(body)
            reason = self._parse_reason(body)
            log.info(
                "lease_lookup.http.success",
                match_count=len(matches),
                reason=reason,
                purpose=query.purpose,
                latency_ms=latency_ms,
            )
            return LeaseLookupResult(
                matches=matches,
                endpoint=endpoint,
                request_payload=request_payload,
                response_payload=body,
                latency_ms=latency_ms,
                status_code=response.status_code,
                reason=reason,
            )

        except httpx.RequestError as exc:
            latency_ms = int((time.monotonic() - wall_start) * 1000)
            error_msg = f"{type(exc).__name__}: {exc}"
            log.warning("lease_lookup.http.connection_error", error=error_msg)
            return LeaseLookupResult(
                matches=[],
                endpoint=endpoint,
                request_payload=request_payload,
                response_payload=None,
                latency_ms=latency_ms,
                status_code=None,
                error=error_msg,
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_params(query: LeaseLookupQuery) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if query.lease_code:
            # A lease code identifies one lease on its own — the platform
            # matches it exactly against pms_tenant_code — and every other
            # identifier is ANDed onto it, so they can only narrow it wrongly.
            #
            # Reported live on t0108880 "Brand Taxi Mall Rental". The extractor
            # split that name in two, because it contains the word "Mall", and
            # the lookup went out as
            #     lease_code=t0108880, brand='Brand Taxi', mall='Mall Rental'
            # The mall matched nothing, the AND removed the row, and the user
            # was told no such lease existed — for a lease the picker had just
            # listed and they had selected from it.
            params["lease_code"] = query.lease_code
        else:
            if query.brand:
                params["brand"] = query.brand
            if query.mall:
                params["mall"] = query.mall
            if query.company:
                params["company"] = query.company
        # Forward the user's property scope to the platform so the response is
        # pre-filtered to leases the user is authorised to manage.
        if query.property_ids:
            params["property_id"] = query.property_ids  # httpx repeats list params
        # purpose=fitout_confirm|fitout_end|trading_confirm bypasses the
        # handover-exists eligibility check on the platform side.
        if query.purpose:
            params["purpose"] = query.purpose
        return params

    @staticmethod
    def _parse_reason(body: dict[str, Any] | list[Any] | None) -> str | None:
        """Extract platform ``reason`` from the GET /leases response envelope."""
        if not isinstance(body, dict):
            return None
        reason = body.get("reason")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()
        data = body.get("data")
        if isinstance(data, dict):
            nested = data.get("reason")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
        return None

    @staticmethod
    def _parse_matches(body: dict[str, Any] | list[Any] | None) -> list[LeaseRecord]:
        """Parse the API response body into a list of ``LeaseRecord``.

        Supported response shapes:
        - ``[{...}, ...]``                        — bare list
        - ``{"leases": [...]}``
        - ``{"data": [...]}``
        - ``{"data": {"leases": [...], ...}}``     — cenomi-api Success envelope
        """
        if not body:
            return []
        items: list[Any] = []
        if isinstance(body, list):
            items = body
        elif isinstance(body, dict):
            if isinstance(body.get("leases"), list):
                items = body["leases"]
            else:
                data = body.get("data")
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict) and isinstance(data.get("leases"), list):
                    items = data["leases"]
        records: list[LeaseRecord] = []
        for item in items:
            try:
                records.append(LeaseRecord.model_validate(item))
            except Exception as exc:
                log.warning("lease_lookup.parse_record.failed", error=str(exc), item=item)
        return records


# ---------------------------------------------------------------------------
# Mock adapter (local POC / testing)
# ---------------------------------------------------------------------------

_MOCK_LEASES: list[dict[str, Any]] = [
    {
        "lease_code": "t0105712",
        "lease_id": 95404,
        "contract_id": 95404,
        "brand": "Brand Under Armour",
        "brand_id": 267,
        "mall": "Jawharat Jeddah",
        "property_id": 3041,
        "tenant_profile_id": 116,
        "unit_codes": ["FF050"],
        "contracted_area": 420,
        "city": "Jeddah",
        "company_name": "Under Armour Trading Co",
        "lease_brand_mall": "t0105712 - Brand Under Armour - Jawharat Jeddah",
    },
    {
        "lease_code": "t0208831",
        "lease_id": 88210,
        "contract_id": 88210,
        "brand": "Nike",
        "brand_id": 312,
        "mall": "Riyadh Park",
        "property_id": 2018,
        "tenant_profile_id": 204,
        "unit_codes": ["GF101", "GF102"],
        "contracted_area": 680,
        "city": "Riyadh",
        "company_name": "Nike Saudi Arabia LLC",
        "lease_brand_mall": "t0208831 - Nike - Riyadh Park",
    },
    {
        "lease_code": "t0301144",
        "lease_id": 71033,
        "contract_id": 71033,
        "brand": "Nike",
        "brand_id": 312,
        "mall": "Mall of Arabia",
        "property_id": 1905,
        "tenant_profile_id": 204,
        "unit_codes": ["LG220"],
        "contracted_area": 510,
        "city": "Jeddah",
        "company_name": "Nike Saudi Arabia LLC",
        "lease_brand_mall": "t0301144 - Nike - Mall of Arabia",
    },
    {
        "lease_code": "t0419977",
        "lease_id": 60511,
        "contract_id": 60511,
        "brand": "Zara",
        "brand_id": 88,
        "mall": "Dubai Festival City",
        "property_id": 4422,
        "tenant_profile_id": 77,
        "unit_codes": ["UF301"],
        "contracted_area": 900,
        "city": "Dubai",
        "company_name": "Inditex Fashion Group",
        "lease_brand_mall": "t0419977 - Zara - Dubai Festival City",
    },
]

_MOCK_RECORDS: list[LeaseRecord] = [LeaseRecord.model_validate(r) for r in _MOCK_LEASES]


class MockLeaseLookupService(AbstractLeaseLookupService):
    """In-memory lease lookup for local POC development and unit tests.

    Parameters
    ----------
    records:
        Optional override for the seed data.  Defaults to ``_MOCK_RECORDS``.
    simulated_latency_ms:
        Latency value reported in ``LeaseLookupResult`` (no real sleep).
    """

    _MOCK_ENDPOINT = "mock://lease-tenant-api/leases"

    def __init__(
        self,
        records: list[LeaseRecord] | None = None,
        simulated_latency_ms: int = 12,
    ) -> None:
        self._records: list[LeaseRecord] = records if records is not None else list(_MOCK_RECORDS)
        self._latency_ms = simulated_latency_ms

    async def lookup(self, query: LeaseLookupQuery) -> LeaseLookupResult:
        request_payload: dict[str, Any] = {
            "endpoint": self._MOCK_ENDPOINT,
            **query.model_dump(exclude_none=True),
        }

        matches = self._filter(query)

        response_payload: dict[str, Any] = {
            "leases": [r.model_dump() for r in matches],
            "count": len(matches),
        }

        log.debug(
            "lease_lookup.mock",
            query=query.model_dump(exclude_none=True),
            match_count=len(matches),
        )

        return LeaseLookupResult(
            matches=matches,
            endpoint=self._MOCK_ENDPOINT,
            request_payload=request_payload,
            response_payload=response_payload,
            latency_ms=self._latency_ms,
            status_code=200,
        )

    def _filter(self, query: LeaseLookupQuery) -> list[LeaseRecord]:
        # Priority 1: case-insensitive lease_code match.
        if query.lease_code:
            code_lower = query.lease_code.strip().lower()
            return [r for r in self._records if r.lease_code.lower() == code_lower]

        # Priority 2: brand + mall (both required, bidirectional substring).
        if query.brand and query.mall:
            brand_l = query.brand.lower()
            mall_l = query.mall.lower()
            return [
                r for r in self._records
                if (brand_l in r.brand.lower() or r.brand.lower() in brand_l)
                and (mall_l in r.mall.lower() or r.mall.lower() in mall_l)
            ]

        # Priority 3: brand only (case-insensitive substring, bidirectional).
        # A real search API typically does fuzzy/partial matching in both
        # directions, so we check whether either string contains the other.
        if query.brand:
            brand_l = query.brand.lower()
            return [
                r for r in self._records
                if brand_l in r.brand.lower() or r.brand.lower() in brand_l
            ]

        # Priority 4: company / tenant name (platform ``?company=``).
        if query.company:
            company_l = query.company.lower()
            return [
                r for r in self._records
                if company_l in (r.company_name or "").lower()
                or (r.company_name or "").lower() in company_l
            ]

        # Priority 5: mall only (case-insensitive substring, bidirectional).
        # e.g. query "Jawharat Jeddah mall" must still match record "Jawharat Jeddah".
        if query.mall:
            mall_l = query.mall.lower()
            return [
                r for r in self._records
                if mall_l in r.mall.lower() or r.mall.lower() in mall_l
            ]

        return []


class HttpGiLeaseLookupService(HttpLeaseLookupService):
    """Production adapter — GET /general-inquiry/leases.

    Same response shape as handover GET /leases. Scope comes from the bearer
    token, so ``purpose`` and ``property_id`` are not sent.
    """

    _ENDPOINT_PATH = "/general-inquiry/leases"

    @staticmethod
    def _build_params(query: LeaseLookupQuery) -> dict[str, Any]:
        params = HttpLeaseLookupService._build_params(query)
        params.pop("purpose", None)
        params.pop("property_id", None)
        return params


class MockGiLeaseLookupService(MockLeaseLookupService):
    """In-memory GI lookup. Includes every mock lease, including ones a
    handover picker would hide behind an existing handover SR."""

    _MOCK_ENDPOINT = "mock://lease-tenant-api/general-inquiry/leases"


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------


def get_lease_lookup_service() -> AbstractLeaseLookupService:
    """Return the appropriate lease-lookup adapter.

    ``MockLeaseLookupService`` is used when either:
    - No ``LEASE_TENANT_API_BASE_URL`` is configured (local/POC mode), or
    - ``LEASE_LOOKUP_USE_MOCK=true`` is set (local eval runs where the UAT API
      rejects the chatbot's locally-signed JWT).
    """
    if settings.lease_tenant_api_base_url and not settings.lease_lookup_use_mock:
        return HttpLeaseLookupService()
    return MockLeaseLookupService()


def get_gi_lease_lookup_service() -> AbstractLeaseLookupService:
    """GI lease lookup. Same base URL as handover; path is /general-inquiry/leases."""
    if settings.lease_tenant_api_base_url and not settings.lease_lookup_use_mock:
        return HttpGiLeaseLookupService()
    return MockGiLeaseLookupService()
