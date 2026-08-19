"""async_main orchestration tests — engine, DB, search all faked."""

from __future__ import annotations

from typing import Any

import pytest

import app.indexer.state as wm_state
from app.indexer import cli
from tests.unit.indexer.conftest import StubEmbeddingProvider


class FakeConn:
    def __init__(self) -> None:
        self.executed: list[str] = []

    async def close(self) -> None: ...


class FakeEngine:
    def __init__(self) -> None:
        self.conn = FakeConn()

    async def connect(self) -> FakeConn:
        return self.conn

    async def dispose(self) -> None: ...


class FakeSource:
    """Configurable in-memory IndexSource."""

    def __init__(self, alias: str, source_type: str, *, rows=None, fail=False,
                 enabled=True) -> None:
        self.name = alias
        self.source_type = source_type
        self._rows = rows if rows is not None else [{"content": "x"}]
        self._fail = fail
        self._enabled = enabled
        self.built: int = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def fetch_rows(self, reader, *, since):
        return self._rows

    async def fetch_deleted_ids(self, reader, *, since):
        return []

    async def fetch_live_ids(self, reader):
        return []

    async def build_documents(self, rows, provider, reader):
        if self._fail:
            raise RuntimeError("builder exploded")
        self.built = len(rows)
        return [
            {"id": f"{self.source_type}_{i}", "source_type": self.source_type,
             "content_vector": [0.0]}
            for i in range(len(rows))
        ]

    def scoped_filters(self, docs):
        return []

    def id_filter(self, entity_id):
        return f"source_type eq '{self.source_type}'"


@pytest.fixture
def harness(monkeypatch):
    """Wire async_main to fakes; returns a dict to tweak per-test."""
    state: dict[str, Any] = {
        "lock": True,
        "watermarks": {},
        "saved_success": [],
        "saved_failure": [],
        "run_started": [],
    }

    fake_engine = FakeEngine()
    import sqlalchemy.ext.asyncio as sa_asyncio
    monkeypatch.setattr(sa_asyncio, "create_async_engine", lambda *a, **k: fake_engine)

    async def fake_try_lock(conn):
        return state["lock"]

    async def fake_release(conn): ...

    async def fake_get_wm(conn, source):
        return state["watermarks"].get(source)

    async def fake_run_start(conn, source):
        state["run_started"].append(source)

    async def fake_save_success(conn, source, *, watermark_at, docs_upserted, docs_deleted):
        state["saved_success"].append((source, watermark_at, docs_upserted, docs_deleted))

    async def fake_save_failure(conn, source, error):
        state["saved_failure"].append((source, error))

    monkeypatch.setattr(wm_state, "try_advisory_lock", fake_try_lock)
    monkeypatch.setattr(wm_state, "release_advisory_lock", fake_release)
    monkeypatch.setattr(wm_state, "get_watermark", fake_get_wm)
    monkeypatch.setattr(wm_state, "record_run_start", fake_run_start)
    monkeypatch.setattr(wm_state, "save_watermark_success", fake_save_success)
    monkeypatch.setattr(wm_state, "save_watermark_failure", fake_save_failure)

    monkeypatch.setattr(cli, "require_cenomi_database_url", lambda: "postgresql+asyncpg://x")
    monkeypatch.setattr(cli, "CenomiPgReader", lambda url: type(
        "R", (), {"dispose": staticmethod(_async_noop)}
    )())
    monkeypatch.setattr(cli, "get_embedding_provider", lambda: StubEmbeddingProvider())
    monkeypatch.setattr(cli, "configure_logging", lambda: None)

    return state


async def _async_noop(*a, **k): ...


def _patch_sources(monkeypatch, sources: dict):
    import app.indexer.sources as sources_mod
    monkeypatch.setattr(sources_mod, "SOURCE_MAP", sources)


class TestAsyncMain:
    @pytest.mark.asyncio
    async def test_lock_held_exits_zero_noop(self, harness, monkeypatch) -> None:
        harness["lock"] = False
        _patch_sources(monkeypatch, {"help": FakeSource("help", "help_content")})
        assert await cli.async_main(["--full", "--dry-run"]) == 0
        assert harness["saved_success"] == []

    @pytest.mark.asyncio
    async def test_dry_run_builds_but_saves_nothing(self, harness, monkeypatch) -> None:
        source = FakeSource("help", "help_content", rows=[{"a": 1}, {"a": 2}])
        _patch_sources(monkeypatch, {"help": source})
        assert await cli.async_main(["--full", "--dry-run"]) == 0
        assert source.built == 2
        assert harness["run_started"] == []
        assert harness["saved_success"] == []

    @pytest.mark.asyncio
    async def test_disabled_source_skipped(self, harness, monkeypatch) -> None:
        _patch_sources(monkeypatch, {
            "guides": FakeSource("guides", "user_guide", enabled=False),
        })
        assert await cli.async_main(["--source", "guides", "--dry-run"]) == 0

    @pytest.mark.asyncio
    async def test_incremental_without_watermark_runs_full(self, harness, monkeypatch) -> None:
        source = FakeSource("help", "help_content")
        _patch_sources(monkeypatch, {"help": source})
        # No watermark stored → full build path (delete_by_source), which in
        # dry-run just builds. Verify it did not crash and built docs.
        assert await cli.async_main(["--incremental", "--dry-run"]) == 0
        assert source.built == 1

    @pytest.mark.asyncio
    async def test_failure_records_failure_and_exit_1_when_all_fail(
        self, harness, monkeypatch
    ) -> None:
        _patch_sources(monkeypatch, {
            "help": FakeSource("help", "help_content", fail=True),
        })
        assert await cli.async_main(["--full", "--dry-run"]) == 1
        # dry-run: failure is logged but watermark writes are skipped
        assert harness["saved_failure"] == []

    @pytest.mark.asyncio
    async def test_partial_failure_exit_2(self, harness, monkeypatch) -> None:
        _patch_sources(monkeypatch, {
            "help": FakeSource("help", "help_content", fail=True),
            "mall": FakeSource("mall", "mall_info"),
        })
        assert await cli.async_main(["--full", "--dry-run"]) == 2
