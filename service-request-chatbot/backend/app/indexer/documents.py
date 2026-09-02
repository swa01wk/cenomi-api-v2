"""Search document construction: make_null_doc / build_doc.

Every upserted document starts from ``make_null_doc()`` so optional fields a
previous version populated are explicitly overwritten with ``None`` — Azure
Search merges by key, and omitting a field would leave the stale value live.
"""

from __future__ import annotations

from typing import Any

# All fields of cenomi-help-index (see app/indexer/index/schema.py).
INDEX_FIELDS: tuple[str, ...] = (
    "id",
    "source_type",
    "source_id",
    "language",
    "category_name_en",
    "category_name_ar",
    "url_pattern",
    "title",
    "content",
    "content_vector",
    "mall_name",
    "unique_property_id",
    "document_id",
    "guide_id",
    "help_content_id",
    "announcement_event_id",
    "section",
    "chunk_index",
    "page_number",
)


def make_null_doc() -> dict[str, Any]:
    """Return a document with every index field present and set to ``None``."""
    return {field: None for field in INDEX_FIELDS}


def build_doc(**fields: Any) -> dict[str, Any]:
    """Return a null doc overlaid with *fields*; unknown keys raise."""
    unknown = set(fields) - set(INDEX_FIELDS)
    if unknown:
        raise ValueError(f"Unknown index fields: {sorted(unknown)}")
    doc = make_null_doc()
    doc.update(fields)
    return doc
