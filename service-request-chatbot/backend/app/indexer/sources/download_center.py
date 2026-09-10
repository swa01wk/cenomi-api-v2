"""download_center PDF source — DISABLED by default (INDEXER_ENABLE_DOWNLOAD_CENTER)."""

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
SELECT d.document_id, d.file_name, dt.document_type_id, dt.type_name,
       dt.type_name_arabic, dt.section AS section_code, dcs.section_name
FROM cenomi.documents d
JOIN cenomi.document_types dt ON d.document_type_id = dt.document_type_id
LEFT JOIN cenomi.download_center_sections dcs ON dt.section = dcs.section_code
WHERE dt.category = 'Download Center'
"""

_LIVE_IDS_SQL = """
SELECT d.document_id
FROM cenomi.documents d
JOIN cenomi.document_types dt ON d.document_type_id = dt.document_type_id
WHERE dt.category = 'Download Center'
"""


def build_download_page_docs(
    row: dict,
    pages: list[tuple[int, str]],
    *,
    max_tokens: int | None = None,
    overlap: int | None = None,
) -> list[dict[str, Any]]:
    """(row, extracted pages) → un-embedded docs.  Pure builder."""
    max_tokens = max_tokens or settings.indexer_chunk_max_tokens
    overlap = overlap if overlap is not None else settings.indexer_chunk_overlap

    title = row.get("type_name") or row.get("file_name") or str(row["document_id"])
    section = row.get("section_name") or row.get("section_code")

    docs: list[dict[str, Any]] = []
    for page_number, page_text in pages:
        for i, chunk in enumerate(chunk_text(page_text, max_tokens, overlap)):
            docs.append(
                build_doc(
                    id=f"download_center_{row['document_id']}_{page_number}_{i}",
                    source_type="download_center",
                    language="en",
                    title=title,
                    content=f"{title}\n{chunk}",
                    document_id=str(row["document_id"]),
                    section=section,
                    page_number=page_number,
                    chunk_index=i,
                )
            )
    return docs


class DownloadCenterSource:
    name = "downloads"
    source_type = "download_center"

    @property
    def enabled(self) -> bool:
        return settings.indexer_enable_download_center

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
            pending.extend(build_download_page_docs(row, pages))
        vectors = await embed_texts(
            provider, [d["content"] for d in pending], source=self.source_type
        )
        for doc, vector in zip(pending, vectors):
            doc["content_vector"] = vector
        return pending

    def scoped_filters(self, docs: list[dict[str, Any]]) -> list[str]:
        ids = sorted({d["document_id"] for d in docs})
        return [f"source_type eq 'download_center' and document_id eq '{i}'" for i in ids]

    def id_filter(self, entity_id: int) -> str:
        return f"source_type eq 'download_center' and document_id eq '{entity_id}'"
