"""mall_info source — one document per mall section (overview/services/social).

One embed per section, NO chunking — sections are short and splitting them
degrades hours/contact answers.  Every indexed body carries the mall name
prefix so vector recall stays mall-specific.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from app.indexer.documents import build_doc
from app.indexer.embedder import embed_texts
from app.indexer.readers.pg_reader import CenomiPgReader
from app.indexer.transform.mall_information import parse_mall_information
from app.integrations.embeddings.base import EmbeddingProvider

log = structlog.get_logger(__name__)

_BASE_SQL = """
SELECT
    up.unique_property_id, up.marketing_name, up.city, up.country,
    up.mall_information, up.mall_services, up.total_gross_leasable_area,
    TRIM((up.mall_information::jsonb) -> 'overview' ->> 'mall_manager') AS mall_manager
FROM cenomi.unique_properties up
WHERE up.is_primary = true
{incremental_clause}
"""

_INCREMENTAL_CLAUSE = "  AND up.updated_at > :since"

_LIVE_IDS_SQL = """
SELECT unique_property_id FROM cenomi.unique_properties WHERE is_primary = true
"""


def build_mall_docs(row: dict) -> list[dict[str, Any]]:
    """Row → un-embedded section documents.  Pure builder for unit tests."""
    prop_id = row["unique_property_id"]
    mall_name = row.get("marketing_name") or f"Property {prop_id}"

    sections = parse_mall_information(
        row.get("mall_information"),
        mall_name,
        total_gross_leasable_area=row.get("total_gross_leasable_area"),
        mall_services=row.get("mall_services"),
    )

    docs: list[dict[str, Any]] = []
    for section in sections:
        indexed = f"{mall_name}\n{section['content']}"
        docs.append(
            build_doc(
                id=f"mall_info_{prop_id}_{section['section']}",
                source_type="mall_info",
                source_id=prop_id,
                language="en",
                title=f"{mall_name} — {section['section'].title()}",
                content=indexed,
                mall_name=row.get("marketing_name"),
                unique_property_id=prop_id,
                section=section["section"],
                chunk_index=0,
            )
        )
    return docs


class MallInfoSource:
    name = "mall"
    source_type = "mall_info"

    @property
    def enabled(self) -> bool:
        return True

    async def fetch_rows(
        self, reader: CenomiPgReader, *, since: datetime | None
    ) -> list[dict]:
        if since is None:
            return await reader.fetch_all(_BASE_SQL.format(incremental_clause=""))
        try:
            return await reader.fetch_all(
                _BASE_SQL.format(incremental_clause=_INCREMENTAL_CLAUSE),
                {"since": since},
            )
        except Exception as exc:
            # unique_properties.updated_at may not exist / not be maintained.
            # Fall back to a full fetch rather than blocking on the platform.
            log.warning(
                "indexer.mall_info_updated_at_missing",
                error=str(exc),
            )
            return await reader.fetch_all(_BASE_SQL.format(incremental_clause=""))

    async def fetch_deleted_ids(
        self, reader: CenomiPgReader, *, since: datetime | None
    ) -> list[int]:
        return []  # primary-property rows are not soft-deleted

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
            pending.extend(build_mall_docs(row))
        vectors = await embed_texts(
            provider, [d["content"] for d in pending], source=self.source_type
        )
        for doc, vector in zip(pending, vectors):
            doc["content_vector"] = vector
        return pending

    def scoped_filters(self, docs: list[dict[str, Any]]) -> list[str]:
        entity_ids = sorted({d["unique_property_id"] for d in docs})
        return [self.id_filter(eid) for eid in entity_ids]

    def id_filter(self, entity_id: int) -> str:
        return f"source_type eq 'mall_info' and unique_property_id eq {entity_id}"
