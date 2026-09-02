"""Writer tests against a fake async SearchClient — no Azure SDK required."""

from __future__ import annotations

import pytest

from app.indexer.index.writer import (
    delete_by_filter,
    delete_by_source,
    upsert_documents,
)


class _Result:
    def __init__(self, key: str, succeeded: bool = True) -> None:
        self.key = key
        self.succeeded = succeeded


class _AsyncSearchIter:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeSearchClient:
    """Holds an in-memory doc store keyed by id; supports the writer's calls."""

    def __init__(self, docs: dict[str, dict] | None = None, fail_keys: set | None = None):
        self.store: dict[str, dict] = dict(docs or {})
        self.fail_keys = fail_keys or set()
        self.upload_batches: list[int] = []
        self.deleted_ids: list[str] = []

    async def merge_or_upload_documents(self, documents: list[dict]):
        self.upload_batches.append(len(documents))
        results = []
        for doc in documents:
            ok = doc["id"] not in self.fail_keys
            if ok:
                self.store[doc["id"]] = doc
            results.append(_Result(doc["id"], ok))
        return results

    async def search(self, *, search_text, filter, select, top, skip=0):
        matched = [
            {"id": k, "source_id": v.get("source_id")}
            for k, v in sorted(self.store.items())
            if self._matches(v, filter)
        ]
        return _AsyncSearchIter(matched[skip : skip + top])

    @staticmethod
    def _matches(doc: dict, filter_expression: str) -> bool:
        # Supports the two shapes the writer emits:
        #   source_type eq 'x'
        #   source_type eq 'x' and <field> eq <int>
        parts = [p.strip() for p in filter_expression.split(" and ")]
        for part in parts:
            field, _, value = part.split(" ", 2)
            value = value.strip("'")
            actual = doc.get(field)
            if str(actual) != value:
                return False
        return True

    async def delete_documents(self, documents: list[dict]):
        for d in documents:
            self.deleted_ids.append(d["id"])
            self.store.pop(d["id"], None)
        return [_Result(d["id"]) for d in documents]


class TestUpsert:
    @pytest.mark.asyncio
    async def test_batching(self) -> None:
        client = FakeSearchClient()
        docs = [{"id": f"d{i}"} for i in range(25)]
        uploaded = await upsert_documents(client, docs, source="t", batch_size=10)
        assert uploaded == 25
        assert client.upload_batches == [10, 10, 5]

    @pytest.mark.asyncio
    async def test_empty(self) -> None:
        assert await upsert_documents(FakeSearchClient(), [], source="t") == 0

    @pytest.mark.asyncio
    async def test_any_failure_raises(self) -> None:
        client = FakeSearchClient(fail_keys={"d3"})
        docs = [{"id": f"d{i}"} for i in range(5)]
        with pytest.raises(RuntimeError, match="upsert failed"):
            await upsert_documents(client, docs, source="t", batch_size=10)


class TestDelete:
    def _seed(self, n: int, source_type: str = "help_content") -> FakeSearchClient:
        return FakeSearchClient(
            {
                f"{source_type}_{i}": {"source_type": source_type, "source_id": i}
                for i in range(n)
            }
        )

    @pytest.mark.asyncio
    async def test_delete_by_source_pages(self, monkeypatch) -> None:
        import app.indexer.index.writer as writer_mod
        monkeypatch.setattr(writer_mod, "_PAGE_SIZE", 100)
        client = self._seed(250)
        deleted = await delete_by_source(client, "help_content")
        assert deleted == 250
        assert client.store == {}

    @pytest.mark.asyncio
    async def test_delete_by_source_only_that_source(self) -> None:
        client = self._seed(3)
        client.store["mall_info_1"] = {"source_type": "mall_info", "source_id": 1}
        await delete_by_source(client, "help_content")
        assert list(client.store) == ["mall_info_1"]

    @pytest.mark.asyncio
    async def test_delete_by_filter_scoped(self) -> None:
        client = FakeSearchClient(
            {
                "help_content_42_en_0": {"source_type": "help_content", "help_content_id": 42},
                "help_content_42_en_1": {"source_type": "help_content", "help_content_id": 42},
                "help_content_43_en_0": {"source_type": "help_content", "help_content_id": 43},
            }
        )
        deleted = await delete_by_filter(
            client, "source_type eq 'help_content' and help_content_id eq 42"
        )
        assert deleted == 2
        assert list(client.store) == ["help_content_43_en_0"]

    @pytest.mark.asyncio
    async def test_delete_nothing_matching(self) -> None:
        client = self._seed(2)
        assert await delete_by_filter(client, "source_type eq 'event'") == 0
