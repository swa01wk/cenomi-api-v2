"""IndexSource protocol — the contract every source module implements."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.indexer.readers.pg_reader import CenomiPgReader
from app.integrations.embeddings.base import EmbeddingProvider


@runtime_checkable
class IndexSource(Protocol):
    """One indexable source (help_content, mall_info, key_contact, event, ...).

    Pure document builders are exported separately by each module so unit
    tests can exercise id/prefix/content rules without a database.
    """

    name: str          # CLI alias, e.g. "help"
    source_type: str   # index source_type, e.g. "help_content"

    @property
    def enabled(self) -> bool: ...

    async def fetch_rows(
        self, reader: CenomiPgReader, *, since: datetime | None
    ) -> list[dict]:
        """Candidate rows.  ``since=None`` ⇒ full fetch."""
        ...

    async def fetch_deleted_ids(
        self, reader: CenomiPgReader, *, since: datetime | None
    ) -> list[int]:
        """Entity ids soft-deleted/hidden since *since* (incremental only)."""
        ...

    async def fetch_live_ids(self, reader: CenomiPgReader) -> list[int]:
        """All live entity ids (used by --reconcile to find orphans)."""
        ...

    async def build_documents(
        self,
        rows: list[dict],
        provider: EmbeddingProvider,
        reader: CenomiPgReader,
    ) -> list[dict[str, Any]]:
        """Rows → embedded index documents.

        *reader* is available for sources whose documents need secondary
        lookups (key_contact resolves manager emails to users); pure sources
        ignore it.
        """
        ...

    def scoped_filters(self, docs: list[dict[str, Any]]) -> list[str]:
        """One OData filter per distinct entity present in *docs*."""
        ...

    def id_filter(self, entity_id: int) -> str:
        """OData filter selecting every document of one entity."""
        ...
