"""Unit tests — lease-verified company fallback for the Work Permit summary card."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

from app.agents.services import work_permit_company_service
from app.agents.services.lease_lookup_service import (
    LeaseLookupResult,
    LeaseRecord,
    normalise_lease_code,
)
from app.agents.services.work_permit_company_service import with_wp_company


def _record(lease_code: str = "t0099892", company_name: str = "Retail Co") -> LeaseRecord:
    return LeaseRecord(
        lease_code=lease_code,
        lease_id=1,
        contract_id=2,
        brand="Zara",
        brand_id=3,
        mall="Mall of Dhahran",
        property_id=4,
        tenant_profile_id=4012,
        unit_codes=["GF001"],
        contracted_area=10.0,
        city="Dhahran",
        lease_brand_mall=f"{lease_code}-Zara-Mall of Dhahran",
        company_name=company_name,
    )


def _result(
    matches: list[LeaseRecord] | None = None,
    *,
    error: str | None = None,
    status_code: int | None = 200,
    reason: str | None = None,
) -> LeaseLookupResult:
    return LeaseLookupResult(
        matches=matches or [],
        endpoint="http://api.example.com/leases",
        request_payload={},
        response_payload=None,
        latency_ms=1,
        status_code=status_code,
        error=error,
        reason=reason,
    )


@pytest.fixture()
def lookup(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    service = MagicMock()
    service.lookup = AsyncMock(return_value=_result([_record()]))
    monkeypatch.setattr(work_permit_company_service, "get_lease_lookup_service", lambda: service)
    return service.lookup


def _collected(**extra: Any) -> dict[str, Any]:
    return {"lease_code": "t0099892", "company_name": "4012", "brand": "Zara", **extra}


class TestNormaliseLeaseCode:
    def test_blank_and_none_are_empty(self) -> None:
        assert normalise_lease_code(None) == ""
        assert normalise_lease_code("   ") == ""

    def test_trims_lowers_and_drops_spaces(self) -> None:
        assert normalise_lease_code(" T0 105712 ") == "t0105712"


class TestWithWpCompany:
    @pytest.mark.asyncio
    async def test_fills_company_and_marker_from_matching_record(self, lookup: AsyncMock) -> None:
        lookup.return_value = _result([_record(lease_code="T0099892")])
        collected = _collected()
        out = await with_wp_company(collected)
        assert out is not collected
        assert out == {**collected, "company": "Retail Co", "company_lease_code": "t0099892"}
        # The tenant profile id stays as the form binds it.
        assert out["company_name"] == "4012"
        query = lookup.await_args.args[0]
        assert query.lease_code == "t0099892"
        assert query.purpose is None

    @pytest.mark.asyncio
    async def test_lease_code_normalised_for_matching_and_marker(self, lookup: AsyncMock) -> None:
        out = await with_wp_company(_collected(lease_code=" T0099892 "))
        assert out["company_lease_code"] == "t0099892"
        assert lookup.await_args.args[0].lease_code == "t0099892"

    @pytest.mark.asyncio
    async def test_lease_picker_value_used_without_lease_code(self, lookup: AsyncMock) -> None:
        out = await with_wp_company({"lease": "t0099892"})
        assert out["company"] == "Retail Co"

    @pytest.mark.asyncio
    async def test_no_call_when_already_verified(self, lookup: AsyncMock) -> None:
        collected = _collected(company="Retail Co", company_lease_code="T0099892")
        assert await with_wp_company(collected) is collected
        lookup.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_call_without_lease_code(self, lookup: AsyncMock) -> None:
        collected = {"company_name": "4012", "brand": "Zara"}
        assert await with_wp_company(collected) is collected
        lookup.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_marker_for_another_lease_looks_up_again(self, lookup: AsyncMock) -> None:
        collected = _collected(company="Old Co", company_lease_code="t0011111")
        out = await with_wp_company(collected)
        lookup.assert_awaited_once()
        assert out["company"] == "Retail Co"
        assert out["company_lease_code"] == "t0099892"

    @pytest.mark.asyncio
    async def test_replaces_user_search_text(self, lookup: AsyncMock) -> None:
        out = await with_wp_company(_collected(company="zara"))
        assert out["company"] == "Retail Co"

    @pytest.mark.asyncio
    async def test_disagreeing_matches_write_nothing(self, lookup: AsyncMock) -> None:
        lookup.return_value = _result([_record(), _record(company_name="Other Co")])
        collected = _collected()
        with capture_logs() as logs:
            assert await with_wp_company(collected) is collected
        assert logs[-1]["event"] == "wp_company.unresolved"
        assert logs[-1]["outcome"] == "ambiguous"
        assert logs[-1]["match_count"] == 2

    @pytest.mark.asyncio
    async def test_duplicate_rows_for_one_company_are_not_ambiguous(
        self, lookup: AsyncMock
    ) -> None:
        lookup.return_value = _result([_record(), _record(company_name=" Retail Co ")])
        out = await with_wp_company(_collected())
        assert out["company"] == "Retail Co"

    @pytest.mark.asyncio
    async def test_records_for_other_leases_are_ignored(self, lookup: AsyncMock) -> None:
        lookup.return_value = _result([_record(lease_code="t0011111")], reason="not_found")
        collected = _collected()
        with capture_logs() as logs:
            assert await with_wp_company(collected) is collected
        assert logs[-1]["outcome"] == "no_match"
        assert logs[-1]["match_count"] == 0
        assert logs[-1]["reason"] == "not_found"

    @pytest.mark.asyncio
    async def test_match_without_company_name_writes_nothing(self, lookup: AsyncMock) -> None:
        lookup.return_value = _result([_record(company_name="")], reason="handover_exists")
        collected = _collected()
        assert await with_wp_company(collected) is collected

    @pytest.mark.asyncio
    async def test_error_result_degrades_without_logging_names(self, lookup: AsyncMock) -> None:
        lookup.return_value = _result(
            [_record()], error="HTTP 500", status_code=500, reason="server_error"
        )
        collected = _collected(company="zara")
        with capture_logs() as logs:
            assert await with_wp_company(collected) is collected
        assert logs == [
            {
                "event": "wp_company.unresolved",
                "log_level": "info",
                "outcome": "error",
                "status_code": 500,
                "reason": "server_error",
                "match_count": 0,
            }
        ]
        text = repr(logs)
        assert "Retail Co" not in text
        assert "zara" not in text
        assert "4012" not in text

    @pytest.mark.asyncio
    async def test_raised_exception_degrades(self, lookup: AsyncMock) -> None:
        lookup.side_effect = RuntimeError("boom")
        collected = _collected()
        with capture_logs() as logs:
            assert await with_wp_company(collected) is collected
        assert logs[-1]["outcome"] == "error"
        assert logs[-1]["reason"] == "RuntimeError"
