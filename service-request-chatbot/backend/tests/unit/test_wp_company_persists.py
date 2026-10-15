"""Unit tests — the lease-verified company reaches the WP cards and survives turns.

Drafts loaded from the platform have no readable company (``company_name`` is
the tenant profile id), so the review, status and details nodes look the lease
up once and return the enriched ``collected_data``. The name must survive
``save_state`` and the next turn's ``sr_status_sync`` merge, and be looked up
again only when the request's lease changes.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.agents.graph.nodes.work_permit.work_permit_confirmation_node import (
    work_permit_confirmation_node,
)
from app.agents.graph.nodes.work_permit.work_permit_details_node import (
    work_permit_details_node,
)
from app.agents.graph.nodes.work_permit.work_permit_status_node import work_permit_status_node
from app.agents.schemas.work_permit_schema import WP_MM_OPS_REVIEW
from app.agents.services import work_permit_company_service
from app.agents.services.conversation_state_service import ConversationStateService
from app.agents.services.lease_lookup_service import LeaseLookupResult, LeaseRecord
from app.types.chat import AuthContext


def _record(lease_code: str, company_name: str) -> LeaseRecord:
    return LeaseRecord(
        lease_code=lease_code,
        lease_id=1,
        contract_id=2,
        brand="Flying Tiger",
        brand_id=3,
        mall="Makkah Mall",
        property_id=4,
        tenant_profile_id=48213,
        unit_codes=["GF009"],
        contracted_area=10.0,
        city="Makkah",
        lease_brand_mall=f"{lease_code}-Flying Tiger-Makkah Mall",
        company_name=company_name,
    )


_COMPANIES = {"t0102106": "Flying Tiger Co", "t0099892": "Retail Co"}


@pytest.fixture()
def lookup(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    async def _lookup(query: Any) -> LeaseLookupResult:
        code = query.lease_code
        return LeaseLookupResult(
            matches=[_record(code, _COMPANIES[code])] if code in _COMPANIES else [],
            endpoint="stub",
            request_payload={},
            response_payload=None,
            latency_ms=0,
            status_code=200,
        )

    service = MagicMock()
    service.lookup = AsyncMock(side_effect=_lookup)
    monkeypatch.setattr(work_permit_company_service, "get_lease_lookup_service", lambda: service)
    return service.lookup


def _collected(**extra: Any) -> dict[str, Any]:
    return {
        "lease_code": "t0102106",
        "company_name": "48213",
        "mall": "Makkah Mall",
        "brand": "Flying Tiger",
        "unit_codes": "GF009",
        "work_permit_type": "OPERATIONS",
        "typeofwork": {"value": ["construction"], "otheroption": ""},
        "effective_starting": {"datefrom": "28/09/2026", "dateto": "30/09/2026"},
        **extra,
    }


def _state(collected: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "workflow_stage": WP_MM_OPS_REVIEW,
        "auth_context": AuthContext(
            subject_id="56716", tenant_id=None, roles=frozenset({"MALL_MANAGER"})
        ),
        "backend_refs": {
            "sr_id": "341524",
            "sr_operations": [{"assigned_role": "MALL_MANAGER", "status": "IN_PROGRESS"}],
        },
        "collected_data": _collected() if collected is None else collected,
        "sub_category": "OPERATIONS",
        "user_message": "show the full request",
        **extra,
    }


def _review_summary(out: dict[str, Any]) -> list[dict[str, Any]]:
    return out["response_ui"]["summary"]


def _assert_enriched(out: dict[str, Any], summary: list[dict[str, Any]]) -> None:
    assert [s["key"] for s in summary][:4] == ["company", "brand", "mall", "unit"]
    assert summary[0]["value"] == "Flying Tiger Co"
    collected = out["collected_data"]
    assert collected["company"] == "Flying Tiger Co"
    assert collected["company_lease_code"] == "t0102106"
    # Unrelated keys are kept and the tenant profile id is untouched.
    assert {k: collected[k] for k in _collected()} == _collected()


class TestNodesShowCompany:
    @pytest.mark.asyncio
    async def test_review_card(self, lookup: AsyncMock) -> None:
        out = await work_permit_confirmation_node(_state())
        _assert_enriched(out, _review_summary(out))

    @pytest.mark.asyncio
    async def test_status_card(self, lookup: AsyncMock) -> None:
        out = await work_permit_status_node(_state())
        _assert_enriched(out, _review_summary(out))

    @pytest.mark.asyncio
    async def test_details_card(self, lookup: AsyncMock) -> None:
        out = await work_permit_details_node(_state())
        _assert_enriched(out, out["response_ui"]["summary"])

    @pytest.mark.asyncio
    async def test_details_section_skips_the_lookup(self, lookup: AsyncMock) -> None:
        out = await work_permit_details_node(_state(user_message="show the schedule"))
        lookup.assert_not_awaited()
        assert "collected_data" not in out

    @pytest.mark.asyncio
    async def test_no_collected_data_returned_when_nothing_changes(
        self, lookup: AsyncMock
    ) -> None:
        verified = _collected(company="Flying Tiger Co", company_lease_code="t0102106")
        for node in (work_permit_confirmation_node, work_permit_status_node):
            out = await node(_state(verified))
            assert "collected_data" not in out
            assert _review_summary(out)[0]["value"] == "Flying Tiger Co"
        lookup.assert_not_awaited()


class _FakeDraftRepo:
    def __init__(self, draft: SimpleNamespace) -> None:
        self.draft = draft

    async def get_by_session(self, session_id: Any) -> SimpleNamespace:
        return self.draft

    async def update(self, draft_id: Any, updates: dict[str, Any]) -> SimpleNamespace:
        for key, value in updates.items():
            setattr(self.draft, key, value)
        return self.draft


def _service() -> ConversationStateService:
    service = ConversationStateService(session=None)  # type: ignore[arg-type]
    service._draft_repo = _FakeDraftRepo(  # type: ignore[assignment]
        SimpleNamespace(
            id=uuid4(),
            service_category="WORK_PERMIT",
            sub_category="OPERATIONS",
            workflow_stage=WP_MM_OPS_REVIEW,
            collected_data={},
            missing_fields=[],
            documents=[],
            ready_to_submit=False,
            sr_id="341524",
            service_request_status="IN_PROCESS",
        )
    )
    return service


def _synced(loaded: dict[str, Any], hydrated: dict[str, Any]) -> dict[str, Any]:
    """The next turn's state after ``sr_status_sync``'s ``{**existing, **hydrated}`` merge."""
    return _state({**(loaded.get("collected_data") or {}), **hydrated})


@pytest.mark.asyncio
async def test_company_survives_save_and_sync_merge(lookup: AsyncMock) -> None:
    service = _service()
    session_id = uuid4()
    out = await work_permit_status_node(_state())
    await service.save_checkpoint(session_id, {**_state(), **out})
    lookup.assert_awaited_once()

    loaded = await service.load(session_id)
    # The platform payload never carries ``company`` / ``company_lease_code``.
    state = _synced(loaded, _collected())
    assert state["collected_data"]["company"] == "Flying Tiger Co"
    assert state["collected_data"]["company_lease_code"] == "t0102106"

    again = await work_permit_status_node(state)
    lookup.assert_awaited_once()
    assert "collected_data" not in again
    assert _review_summary(again)[0]["value"] == "Flying Tiger Co"


@pytest.mark.asyncio
async def test_switching_to_another_lease_looks_up_once_more(lookup: AsyncMock) -> None:
    service = _service()
    session_id = uuid4()
    out = await work_permit_status_node(_state())
    await service.save_checkpoint(session_id, {**_state(), **out})

    loaded = await service.load(session_id)
    state = _synced(loaded, _collected(lease_code="t0099892", company_name="4012"))
    # The previous request's name must not show for the new lease.
    switched = await work_permit_status_node(state)
    assert lookup.await_count == 2
    assert _review_summary(switched)[0]["value"] == "Retail Co"
    assert switched["collected_data"]["company_lease_code"] == "t0099892"
    assert switched["collected_data"]["company_name"] == "4012"
