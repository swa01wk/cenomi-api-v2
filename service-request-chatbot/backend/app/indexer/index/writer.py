"""Azure AI Search write operations for the indexer.

Writes use the ``azure-search-documents`` SDK (the read path stays raw httpx).
The SDK is imported lazily inside ``make_search_client`` so importing this
module — e.g. from CLI unit tests — does not require the ``indexer`` extra.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.core.config import settings

log = structlog.get_logger(__name__)

_PAGE_SIZE = 1000


def make_search_client(endpoint: str, api_key: str, index_name: str):
    """Return an async ``SearchClient`` for the target index."""
    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents.aio import SearchClient

    return SearchClient(
        endpoint=endpoint,
        index_name=index_name,
        credential=AzureKeyCredential(api_key),
    )


async def upsert_documents(
    client: Any,
    documents: list[dict[str, Any]],
    *,
    source: str = "",
    batch_size: int | None = None,
) -> int:
    """Upload documents in batches; raise if ANY document fails.

    A partial upsert with an advanced watermark would silently drop the failed
    documents forever, so any failure must fail the source (the watermark then
    stays put and the next run retries the same window — ids are deterministic
    so the retry is idempotent).
    """
    if not documents:
        return 0
    batch_size = batch_size or settings.indexer_upload_batch_size
    uploaded = 0
    for start in range(0, len(documents), batch_size):
        batch = documents[start : start + batch_size]
        results = await client.merge_or_upload_documents(documents=batch)
        failed = [r for r in results if not r.succeeded]
        succeeded = len(batch) - len(failed)
        uploaded += succeeded
        log.info(
            "indexer.upsert_batch",
            source=source,
            batch_size=len(batch),
            succeeded=succeeded,
            failed=len(failed),
        )
        if failed:
            keys = [getattr(r, "key", "?") for r in failed[:5]]
            raise RuntimeError(
                f"upsert failed for {len(failed)} document(s) in source "
                f"{source!r} (first keys: {keys})"
            )
    return uploaded


async def _collect_ids(client: Any, filter_expression: str) -> list[str]:
    """Page through all ids matching *filter_expression*."""
    ids: list[str] = []
    skip = 0
    while True:
        results = await client.search(
            search_text="*",
            filter=filter_expression,
            select=["id"],
            top=_PAGE_SIZE,
            skip=skip,
        )
        page = [doc["id"] async for doc in results]
        ids.extend(page)
        if len(page) < _PAGE_SIZE:
            break
        skip += _PAGE_SIZE
    return ids


async def delete_by_filter(
    client: Any, filter_expression: str, *, source: str = ""
) -> int:
    """Delete every document matching an OData filter (scoped deletes)."""
    ids = await _collect_ids(client, filter_expression)
    if not ids:
        return 0
    for start in range(0, len(ids), _PAGE_SIZE):
        page = ids[start : start + _PAGE_SIZE]
        await client.delete_documents(documents=[{"id": doc_id} for doc_id in page])
    log.info(
        "indexer.delete_by_filter",
        source=source,
        filter=filter_expression,
        deleted=len(ids),
    )
    return len(ids)


async def delete_by_source(client: Any, source_type: str) -> int:
    """Delete ALL documents of one source_type (full-rebuild path)."""
    return await delete_by_filter(
        client, f"source_type eq '{source_type}'", source=source_type
    )


async def fetch_index_source_ids(client: Any, source_type: str) -> set[int]:
    """Return the distinct ``source_id`` values present for *source_type*.

    Used by ``--reconcile`` to diff the index against live DB ids.
    """
    source_ids: set[int] = set()
    skip = 0
    while True:
        results = await client.search(
            search_text="*",
            filter=f"source_type eq '{source_type}'",
            select=["id", "source_id"],
            top=_PAGE_SIZE,
            skip=skip,
        )
        count = 0
        async for doc in results:
            count += 1
            sid = doc.get("source_id")
            if sid is not None:
                source_ids.add(int(sid))
        if count < _PAGE_SIZE:
            break
        skip += _PAGE_SIZE
    return source_ids
