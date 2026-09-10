"""help_content source — help node contents with breadcrumb context.

Documents keep structured breadcrumb prefixes ("Page > Category > Subcategory")
on both body and title — never opaque concatenated blobs — because retrieval
quality for navigation questions depends on the breadcrumb terms.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.config import settings
from app.indexer.documents import build_doc
from app.indexer.embedder import embed_texts
from app.indexer.readers.pg_reader import CenomiPgReader
from app.indexer.transform.chunker import chunk_text
from app.indexer.transform.html_cleaner import clean_html
from app.integrations.embeddings.base import EmbeddingProvider

_BASE_SQL = """
SELECT
    c.help_content_id, c.help_node_id, n.node_type,
    c.title_en, c.title_ar, c.description_en, c.description_ar,
    CASE WHEN n.node_type = 'PAGE'        THEN n.name_en
         WHEN n.node_type = 'CATEGORY'    THEN p1.name_en
         WHEN n.node_type = 'SUBCATEGORY' THEN p2.name_en END AS page_name_en,
    CASE WHEN n.node_type = 'PAGE'        THEN n.name_ar
         WHEN n.node_type = 'CATEGORY'    THEN p1.name_ar
         WHEN n.node_type = 'SUBCATEGORY' THEN p2.name_ar END AS page_name_ar,
    CASE WHEN n.node_type = 'PAGE'        THEN n.url_pattern
         WHEN n.node_type = 'CATEGORY'    THEN p1.url_pattern
         WHEN n.node_type = 'SUBCATEGORY' THEN p2.url_pattern END AS url_pattern,
    CASE WHEN n.node_type = 'CATEGORY'    THEN n.name_en
         WHEN n.node_type = 'SUBCATEGORY' THEN p1.name_en END AS category_name_en,
    CASE WHEN n.node_type = 'CATEGORY'    THEN n.name_ar
         WHEN n.node_type = 'SUBCATEGORY' THEN p1.name_ar END AS category_name_ar,
    CASE WHEN n.node_type = 'SUBCATEGORY' THEN n.name_en END AS subcategory_name_en,
    CASE WHEN n.node_type = 'SUBCATEGORY' THEN n.name_ar END AS subcategory_name_ar,
    GREATEST(
        c.updated_at, n.updated_at,
        COALESCE(p1.updated_at, '-infinity'::timestamptz),
        COALESCE(p2.updated_at, '-infinity'::timestamptz)
    ) AS row_updated_at
FROM cenomi.help_node_contents c
JOIN      cenomi.help_nodes n  ON n.help_node_id  = c.help_node_id
LEFT JOIN cenomi.help_nodes p1 ON p1.help_node_id = n.parent_id
LEFT JOIN cenomi.help_nodes p2 ON p2.help_node_id = p1.parent_id
WHERE n.is_visible = TRUE
{incremental_clause}
ORDER BY c.help_node_id, c.sort_order NULLS LAST, c.help_content_id
"""

_INCREMENTAL_CLAUSE = """
  AND (c.updated_at > :since OR n.updated_at > :since
       OR p1.updated_at > :since OR p2.updated_at > :since)
"""

_DELETED_SQL = """
SELECT DISTINCT c.help_content_id
FROM cenomi.help_node_contents c
JOIN cenomi.help_nodes n ON n.help_node_id = c.help_node_id
WHERE n.is_visible = FALSE
  AND (c.updated_at > :since OR n.updated_at > :since)
"""

_LIVE_IDS_SQL = """
SELECT c.help_content_id
FROM cenomi.help_node_contents c
JOIN cenomi.help_nodes n ON n.help_node_id = c.help_node_id
WHERE n.is_visible = TRUE
"""


def _breadcrumb(row: dict, lang: str) -> str:
    parts = [
        row.get(f"page_name_{lang}"),
        row.get(f"category_name_{lang}"),
        row.get(f"subcategory_name_{lang}"),
    ]
    return " > ".join(p for p in parts if p)


def pending_chunks_from_row(
    row: dict, *, max_tokens: int | None = None, overlap: int | None = None
) -> list[dict[str, Any]]:
    """Row → un-embedded documents (``content_vector`` filled in later).

    Pure builder for unit tests: no DB, no network.
    """
    max_tokens = max_tokens or settings.indexer_chunk_max_tokens
    overlap = overlap if overlap is not None else settings.indexer_chunk_overlap

    pending: list[dict[str, Any]] = []
    for lang in ("en", "ar"):
        text = clean_html(row.get(f"description_{lang}"))
        if not text:
            continue

        title = (row.get(f"title_{lang}") or "").strip()
        breadcrumb = _breadcrumb(row, lang)
        # Breadcrumb prefixes body AND title (retrieval depends on it).
        body = f"{breadcrumb}\n{title}\n{text}" if breadcrumb else f"{title}\n{text}"
        doc_title = f"{breadcrumb} — {title}" if breadcrumb else title

        for i, chunk in enumerate(chunk_text(body, max_tokens, overlap)):
            pending.append(
                build_doc(
                    id=f"help_content_{row['help_content_id']}_{lang}_{i}",
                    source_type="help_content",
                    source_id=row["help_content_id"],
                    language=lang,
                    title=doc_title,
                    content=chunk,
                    # Page name, not category name — legacy quirk kept for parity.
                    category_name_en=row.get("page_name_en"),
                    category_name_ar=row.get("page_name_ar"),
                    url_pattern=row.get("url_pattern"),
                    help_content_id=row["help_content_id"],
                    chunk_index=i,
                )
            )
    return pending


class HelpContentSource:
    name = "help"
    source_type = "help_content"

    @property
    def enabled(self) -> bool:
        return True

    async def fetch_rows(
        self, reader: CenomiPgReader, *, since: datetime | None
    ) -> list[dict]:
        if since is None:
            sql = _BASE_SQL.format(incremental_clause="")
            return await reader.fetch_all(sql)
        sql = _BASE_SQL.format(incremental_clause=_INCREMENTAL_CLAUSE)
        return await reader.fetch_all(sql, {"since": since})

    async def fetch_deleted_ids(
        self, reader: CenomiPgReader, *, since: datetime | None
    ) -> list[int]:
        if since is None:
            return []
        return await reader.fetch_scalars(_DELETED_SQL, {"since": since})

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
            pending.extend(pending_chunks_from_row(row))
        vectors = await embed_texts(
            provider, [d["content"] for d in pending], source=self.source_type
        )
        for doc, vector in zip(pending, vectors):
            doc["content_vector"] = vector
        return pending

    def scoped_filters(self, docs: list[dict[str, Any]]) -> list[str]:
        entity_ids = sorted({d["help_content_id"] for d in docs})
        return [self.id_filter(eid) for eid in entity_ids]

    def id_filter(self, entity_id: int) -> str:
        return f"source_type eq 'help_content' and help_content_id eq {entity_id}"
