"""user_guide PDF source — DISABLED by default (INDEXER_ENABLE_USER_GUIDES)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.config import settings
from app.indexer.documents import build_doc
from app.indexer.embedder import embed_texts
from app.indexer.readers.blob_reader import download_document_blob
from app.indexer.readers.pg_reader import CenomiPgReader
from app.indexer.transform.chunker import chunk_text
from app.indexer.transform.pdf_extractor import extract_pdf_pages
from app.integrations.embeddings.base import EmbeddingProvider

_BASE_SQL = """
SELECT ug.guide_id, ug.title_en, ug.title_ar, d.document_id, d.file_name
FROM cenomi.user_guides ug JOIN cenomi.documents d ON ug.document_id = d.document_id
"""

_LIVE_IDS_SQL = "SELECT guide_id FROM cenomi.user_guides"


def build_guide_page_docs(
    row: dict,
    pages: list[tuple[int, str]],
    *,
    max_tokens: int | None = None,
    overlap: int | None = None,
) -> list[dict[str, Any]]:
    """(row, extracted pages) → un-embedded docs.  Pure builder."""
    max_tokens = max_tokens or settings.indexer_chunk_max_tokens
    overlap = overlap if overlap is not None else settings.indexer_chunk_overlap

    docs: list[dict[str, Any]] = []
    for lang in ("en", "ar"):
        title = (row.get(f"title_{lang}") or "").strip()
        if not title:
            continue
        for page_number, page_text in pages:
            for i, chunk in enumerate(chunk_text(page_text, max_tokens, overlap)):
                docs.append(
                    build_doc(
                        id=f"user_guide_{row['guide_id']}_{lang}_{page_number}_{i}",
                        source_type="user_guide",
                        source_id=row["guide_id"],
                        language=lang,
                        title=title,
                        content=f"{title}\n{chunk}",
                        document_id=str(row.get("document_id") or "") or None,
                        guide_id=row["guide_id"],
                        page_number=page_number,
                        chunk_index=i,
                    )
                )
    return docs


class UserGuideSource:
    name = "guides"
    source_type = "user_guide"

    @property
    def enabled(self) -> bool:
        return settings.indexer_enable_user_guides

    async def fetch_rows(
        self, reader: CenomiPgReader, *, since: datetime | None
    ) -> list[dict]:
        return await reader.fetch_all(_BASE_SQL)

    async def fetch_deleted_ids(
        self, reader: CenomiPgReader, *, since: datetime | None
    ) -> list[int]:
        return []

    async def fetch_live_ids(self, reader: CenomiPgReader) -> list[int]:
        return await reader.fetch_scalars(_LIVE_IDS_SQL)

    async def build_documents(
        self,
        rows: list[dict],
        provider: EmbeddingProvider,
        reader: CenomiPgReader,
    ) -> list[dict[str, Any]]:
        pending: list[dict[str, Any]] = []
        for row in rows:
            data = await download_document_blob(row["document_id"])
            if not data:
                continue
            pages = extract_pdf_pages(data)
            pending.extend(build_guide_page_docs(row, pages))
        vectors = await embed_texts(
            provider, [d["content"] for d in pending], source=self.source_type
        )
        for doc, vector in zip(pending, vectors):
            doc["content_vector"] = vector
        return pending

    def scoped_filters(self, docs: list[dict[str, Any]]) -> list[str]:
        entity_ids = sorted({d["guide_id"] for d in docs})
        return [self.id_filter(eid) for eid in entity_ids]

    def id_filter(self, entity_id: int) -> str:
        return f"source_type eq 'user_guide' and guide_id eq {entity_id}"
