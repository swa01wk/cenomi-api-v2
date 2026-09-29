"""Unit tests — sales-data lookups stay scoped to the active company."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.agents.services import sales_data_api_client as mod
from app.agents.services.sales_data_api_client import SalesDataApiClient


class _FakeClient:
    calls: list[dict[str, Any]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str, headers: dict, params: dict) -> httpx.Response:
        _FakeClient.calls.append(params)
        return httpx.Response(
            200,
            json={"data": {"data": [], "total_count": 0}},
            request=httpx.Request("GET", url),
        )


@pytest.fixture(autouse=True)
def _fake_http(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeClient.calls = []
    monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeClient)


@pytest.mark.asyncio
async def test_get_sales_data_defaults_to_active_company() -> None:
    await SalesDataApiClient("tok").get_sales_data(disable_pagination=True)
    assert "all_my_companies" not in _FakeClient.calls[-1]


@pytest.mark.asyncio
async def test_get_sales_data_page_defaults_to_active_company() -> None:
    await SalesDataApiClient("tok").get_sales_data_page(status="SUBMITTED")
    assert "all_my_companies" not in _FakeClient.calls[-1]


@pytest.mark.asyncio
async def test_all_my_companies_only_when_explicit() -> None:
    await SalesDataApiClient("tok").get_sales_data(all_my_companies=True)
    assert _FakeClient.calls[-1]["all_my_companies"] == "true"


def test_no_access_error_names_current_company() -> None:
    result = mod.partition_rows_by_lease_access(
        [{"lease_code": "t0000001", "sales_period_start": "2026-08-01"}], []
    )
    assert "current company" in result["no_access"][0]["_error"]
    assert "don't have access" in result["no_access"][0]["_error"]
