"""event source — announcements & events, bug-compatible mall fan-out.

KNOWN QUIRK (kept deliberately, spec §10.4): the LEFT JOIN to
``announcement_event_malls`` fans an event across N malls into N rows that
build documents with the SAME id — so the last mall written wins and the
document carries only one mall_name.  The legacy job has this bug; fixing it
here would change document counts and retrieval behaviour mid-cutover.
Do NOT fix in this ticket.
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
SELECT ae.announcement_event_id, ae.title_en, ae.title_ar,
       ae.description_en, ae.description_ar,
       ae.start_date, ae.end_date, ae.is_event, ae.is_announcement,
       ae.applies_to_all_malls, ae.deleted_at,
       ae.updated_at AS row_updated_at,
       up.marketing_name AS mall_name
FROM cenomi.announcements_events ae
LEFT JOIN cenomi.announcement_event_malls aem
       ON ae.announcement_event_id = aem.announcement_event_id
LEFT JOIN cenomi.unique_properties up
       ON aem.unique_property_id = up.unique_property_id
{where_clause}
"""

_FULL_WHERE = "WHERE ae.deleted_at IS NULL"
_INCREMENTAL_WHERE = "WHERE ae.updated_at > :since OR ae.deleted_at > :since"

_DELETED_SQL = """
SELECT DISTINCT announcement_event_id
FROM cenomi.announcements_events
WHERE deleted_at IS NOT NULL AND deleted_at > :since
"""

_LIVE_IDS_SQL = """
SELECT announcement_event_id FROM cenomi.announcements_events
WHERE deleted_at IS NULL
"""


def _type_line(row: dict) -> str:
    is_event = bool(row.get("is_event"))
    is_announcement = bool(row.get("is_announcement"))
    if is_event and is_announcement:
        return "Event, Announcement"
    if is_announcement:
        return "Announcement"
    return "Event"  # default when neither flag is set


def _date_line(row: dict) -> str | None:
    start = row.get("start_date")
    end = row.get("end_date")
    if start and end:
        return f"Date: {start} to {end}"
    if start:
        return f"Date: {start}"
    return None


def build_event_docs(
    row: dict, *, max_tokens: int | None = None, overlap: int | None = None
) -> list[dict[str, Any]]:
    """Row → un-embedded documents.  Pure builder for unit tests.

    Soft-deleted rows build nothing (they are handled as deletions).
    """
    if row.get("deleted_at") is not None:
        return []

    max_tokens = max_tokens or settings.indexer_chunk_max_tokens
    overlap = overlap if overlap is not None else settings.indexer_chunk_overlap

    event_id = row["announcement_event_id"]
    mall_name = row.get("mall_name") or (
        "All Malls" if row.get("applies_to_all_malls") else ""
    )

    docs: list[dict[str, Any]] = []
    for lang in ("en", "ar"):
        title = (row.get(f"title_{lang}") or "").strip()
        if not title:
            continue

        lines = [f"{_type_line(row)}: {title}"]
        date_line = _date_line(row)
        if date_line:
            lines.append(date_line)
        lines.append(f"Mall: {mall_name}")
        description = clean_html(row.get(f"description_{lang}"))
        if description:
            lines.append(description)
        content = "\n".join(lines)

        for i, chunk in enumerate(chunk_text(content, max_tokens, overlap)):
            docs.append(
                build_doc(
                    id=f"event_{event_id}_{lang}_{i}",
                    source_type="event",
                    source_id=event_id,
                    language=lang,
                    title=title,
                    content=chunk,
                    mall_name=mall_name or None,
                    announcement_event_id=event_id,
                    chunk_index=i,
                )
            )
    return docs


class EventSource:
    name = "events"
    source_type = "event"

    @property
    def enabled(self) -> bool:
        return True

    async def fetch_rows(
        self, reader: CenomiPgReader, *, since: datetime | None
    ) -> list[dict]:
        if since is None:
            return await reader.fetch_all(_BASE_SQL.format(where_clause=_FULL_WHERE))
        return await reader.fetch_all(
            _BASE_SQL.format(where_clause=_INCREMENTAL_WHERE), {"since": since}
        )

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
        # Same-id docs from the mall fan-out overwrite each other in dict
        # order — bug-compatible last-write-wins, matching the legacy upsert.
        by_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            for doc in build_event_docs(row):
                by_id[doc["id"]] = doc
        pending = list(by_id.values())

        vectors = await embed_texts(
            provider, [d["content"] for d in pending], source=self.source_type
        )
        for doc, vector in zip(pending, vectors):
            doc["content_vector"] = vector
        return pending

    def scoped_filters(self, docs: list[dict[str, Any]]) -> list[str]:
        entity_ids = sorted({d["announcement_event_id"] for d in docs})
        return [self.id_filter(eid) for eid in entity_ids]

    def id_filter(self, entity_id: int) -> str:
        return f"source_type eq 'event' and announcement_event_id eq {entity_id}"
