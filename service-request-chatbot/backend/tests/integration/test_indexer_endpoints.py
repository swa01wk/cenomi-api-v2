"""Indexer status / re-index endpoint tests — stubbed DB, no indexer deps."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

_STATUS_URL = "/api/v1/indexer/status"
_REINDEX_URL = "/api/v1/indexer/reindex"

_EPOCH = datetime.fromtimestamp(0, tz=UTC)
_NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def _row(source: str, watermark_at: datetime, status: str = "success") -> dict:
    return {
        "source": source,
        "last_run_at": _NOW,
        "watermark_at": watermark_at,
        "last_status": status,
        "docs_upserted": 12,
        "docs_deleted": 3,
        "error": None,
        "updated_at": _NOW,
    }


def _make_client(rows: list[dict]):
    from app.db.session import _get_db

    db = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()

    def _override():
        yield db

    app.dependency_overrides[_get_db] = _override
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, db


def _teardown() -> None:
    app.dependency_overrides.clear()


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_lists_sources(self) -> None:
        client, _ = _make_client(
            [
                _row("event", _NOW - timedelta(minutes=5)),
                _row("help_content", _NOW - timedelta(minutes=5)),
            ]
        )
        try:
            resp = await client.get(_STATUS_URL)
            assert resp.status_code == 200
            data = resp.json()
            assert [s["source"] for s in data["sources"]] == ["event", "help_content"]
            assert data["sources"][0]["docs_upserted"] == 12
            assert data["sources"][0]["reindex_pending"] is False
        finally:
            _teardown()

    @pytest.mark.asyncio
    async def test_epoch_watermark_reports_reindex_pending(self) -> None:
        client, _ = _make_client([_row("mall_info", _EPOCH, status="reindex_requested")])
        try:
            resp = await client.get(_STATUS_URL)
            assert resp.json()["sources"][0]["reindex_pending"] is True
        finally:
            _teardown()

    @pytest.mark.asyncio
    async def test_empty_table_ok(self) -> None:
        client, _ = _make_client([])
        try:
            resp = await client.get(_STATUS_URL)
            assert resp.status_code == 200
            assert resp.json()["sources"] == []
        finally:
            _teardown()


class TestReindex:
    @pytest.mark.asyncio
    async def test_single_source_reset(self) -> None:
        client, db = _make_client([])
        try:
            resp = await client.post(_REINDEX_URL, json={"source": "mall_info"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["reset_sources"] == ["mall_info"]
            assert db.execute.await_count == 1
            db.commit.assert_awaited_once()
        finally:
            _teardown()

    @pytest.mark.asyncio
    async def test_all_sources_reset(self) -> None:
        client, db = _make_client([])
        try:
            resp = await client.post(_REINDEX_URL, json={"source": "all"})
            assert resp.status_code == 200
            assert len(resp.json()["reset_sources"]) == 6
            assert db.execute.await_count == 6
        finally:
            _teardown()

    @pytest.mark.asyncio
    async def test_default_is_all(self) -> None:
        client, _ = _make_client([])
        try:
            resp = await client.post(_REINDEX_URL, json={})
            assert len(resp.json()["reset_sources"]) == 6
        finally:
            _teardown()

    @pytest.mark.asyncio
    async def test_invalid_source_422(self) -> None:
        client, _ = _make_client([])
        try:
            resp = await client.post(_REINDEX_URL, json={"source": "nope"})
            assert resp.status_code == 422
        finally:
            _teardown()


class TestRBAC:
    @pytest.mark.asyncio
    async def test_forbidden_without_admin_when_enforced(self, monkeypatch) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "rbac_enforce", True)
        client, _ = _make_client([])
        try:
            resp = await client.get(_STATUS_URL)
            # No token at all → 401 from get_auth_context in enforce mode.
            assert resp.status_code == 401
        finally:
            _teardown()
