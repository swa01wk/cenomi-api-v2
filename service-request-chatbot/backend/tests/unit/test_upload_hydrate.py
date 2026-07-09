"""Unit tests for upload route backend-ref hydration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.services.lease_lookup_service import LeaseLookupResult, LeaseRecord
from app.agents.services.platform_api_client import ServiceRequestGetResult
from app.api.routes.upload import _hydrate_backend_refs_from_platform
from app.types.chat import AuthContext


@pytest.mark.asyncio
async def test_hydrate_fetches_lease_from_platform_when_draft_empty() -> None:
    auth = AuthContext(subject_id="u1", roles=frozenset({"FM_MANAGER"}), bearer_token="tok")
    mock_result = ServiceRequestGetResult(
        success=True,
        sr_id="11711",
        status="IN_PROCESS",
        service_request_operations=[],
        payload={
            "lease_id": "9001",
            "brand_id": "42",
            "property_id": "7",
            "lease_code": "t001",
            "tenant_profile_id": "100",
        },
        endpoint="http://localhost/api/v1/service-requests/11711",
        status_code=200,
        latency_ms=10,
    )

    with patch(
        "app.api.routes.upload.get_service_request_api_service"
    ) as mock_factory:
        mock_factory.return_value = AsyncMock(
            get_service_request=AsyncMock(return_value=mock_result)
        )
        merged = await _hydrate_backend_refs_from_platform("11711", {}, auth)

    assert merged["lease_id"] == "9001"
    assert merged["brand_id"] == "42"
    assert merged["lease_code"] == "t001"


@pytest.mark.asyncio
async def test_hydrate_skips_get_when_lease_already_present() -> None:
    auth = AuthContext(subject_id="u1", roles=frozenset({"FM_MANAGER"}))
    backend = {"lease_id": "existing"}

    with patch(
        "app.api.routes.upload.get_service_request_api_service"
    ) as mock_factory:
        merged = await _hydrate_backend_refs_from_platform("11711", backend, auth)

    mock_factory.assert_not_called()
    assert merged["lease_id"] == "existing"


@pytest.mark.asyncio
async def test_hydrate_uses_merged_platform_payload_refs() -> None:
    """Mirrors get_service_request after top-level lease_id merge."""
    auth = AuthContext(subject_id="u1", roles=frozenset({"FM_MANAGER"}), bearer_token="tok")
    mock_result = ServiceRequestGetResult(
        success=True,
        sr_id="11711",
        status="IN_PROCESS",
        service_request_operations=[],
        payload={
            "lease_id": 9001,
            "lease_code": "t0208831",
            "tenant_profile_id": 55,
            "property_id": 7,
            "brand_id": 42,
            "description": "Handover",
        },
        endpoint="http://localhost/api/v1/service-requests/11711",
        status_code=200,
        latency_ms=10,
    )

    with patch(
        "app.api.routes.upload.get_service_request_api_service"
    ) as mock_factory:
        mock_factory.return_value = AsyncMock(
            get_service_request=AsyncMock(return_value=mock_result)
        )
        merged = await _hydrate_backend_refs_from_platform("11711", {}, auth)

    assert merged["lease_id"] == 9001
    assert merged["brand_id"] == 42
    assert merged["property_id"] == 7


@pytest.mark.asyncio
async def test_hydrate_uses_contract_id_when_lease_id_missing() -> None:
    """Existing handover SRs omit lease_id; contract_id is the same numeric id."""
    auth = AuthContext(subject_id="u1", roles=frozenset({"FM_MANAGER"}), bearer_token="tok")
    mock_result = ServiceRequestGetResult(
        success=True,
        sr_id="11711",
        status="IN_PROCESS",
        service_request_operations=[],
        payload={
            "lease": "t0028641",
            "contract_id": 88210,
            "brand_id": 42,
            "property_id": 7,
            "tenant_profile_id": 55,
        },
        endpoint="http://localhost/api/v1/service-requests/11711",
        status_code=200,
        latency_ms=10,
    )

    with (
        patch("app.api.routes.upload.get_service_request_api_service") as mock_sr,
        patch("app.agents.services.lease_lookup_service.get_lease_lookup_service") as mock_lease,
    ):
        mock_sr.return_value = AsyncMock(
            get_service_request=AsyncMock(return_value=mock_result)
        )
        merged = await _hydrate_backend_refs_from_platform("11711", {}, auth)

    mock_lease.assert_not_called()
    assert merged["lease_id"] == 88210
    assert merged["lease_code"] == "t0028641"
    assert merged["brand_id"] == 42


@pytest.mark.asyncio
async def test_hydrate_resolves_lease_id_from_nested_lease_code() -> None:
    """Fallback: no contract_id — look up lease_code via GET /leases."""
    auth = AuthContext(subject_id="u1", roles=frozenset({"FM_MANAGER"}), bearer_token="tok")
    mock_result = ServiceRequestGetResult(
        success=True,
        sr_id="11711",
        status="IN_PROCESS",
        service_request_operations=[],
        payload={
            "lease": "t0208831",
            "brand_id": 42,
            "property_id": 7,
            "tenant_profile_id": 55,
        },
        endpoint="http://localhost/api/v1/service-requests/11711",
        status_code=200,
        latency_ms=10,
    )
    lease_match = LeaseRecord(
        lease_code="t0208831",
        lease_id=9001,
        contract_id=1,
        brand="Nike",
        brand_id=42,
        mall="Riyadh Park",
        property_id=7,
        tenant_profile_id=55,
        unit_codes=["A1"],
        contracted_area=100.0,
        city="Riyadh",
        lease_brand_mall="t0208831 | Nike | Riyadh Park",
    )
    lookup_result = LeaseLookupResult(
        matches=[lease_match],
        endpoint="http://localhost/api/v1/leases",
        request_payload={"lease_code": "t0208831"},
        response_payload=None,
        latency_ms=5,
        status_code=200,
    )

    with (
        patch("app.api.routes.upload.get_service_request_api_service") as mock_sr,
        patch("app.agents.services.lease_lookup_service.get_lease_lookup_service") as mock_lease,
    ):
        mock_sr.return_value = AsyncMock(
            get_service_request=AsyncMock(return_value=mock_result)
        )
        mock_lease.return_value = MagicMock(
            lookup=AsyncMock(return_value=lookup_result)
        )
        merged = await _hydrate_backend_refs_from_platform("11711", {}, auth)

    assert merged["lease_id"] == 9001
    assert merged["lease_code"] == "t0208831"
    assert merged["brand_id"] == 42
