"""Azure AI Search index schema — ``ensure_index_exists``.

The ``azure-search-documents`` SDK is imported lazily inside functions: it
ships in the ``indexer`` dependency extra and must not be required to import
this module (CLI ``parse_args`` unit tests run without the extra).

NO semantic configuration — the read path (``azure_search.py``) sends plain
hybrid queries and the index has no semantic config; adding one only on the
write side would drift the two, and adding ``queryType=semantic`` on the read
side without an index config is an HTTP 400.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

VECTOR_DIMENSIONS = 1536  # text-embedding-3-small — must match the read path.
VECTOR_PROFILE = "cenomi-vector-profile"
HNSW_ALGO = "hnsw-algo"


def build_index_definition(index_name: str):
    """Return the ``SearchIndex`` definition for ``cenomi-help-index``."""
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchableField,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="source_type", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="source_id", type=SearchFieldDataType.Int64, filterable=True),
        SimpleField(name="language", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="category_name_en", type=SearchFieldDataType.String),
        SearchableField(
            name="category_name_ar",
            type=SearchFieldDataType.String,
            analyzer_name="ar.microsoft",
        ),
        SearchableField(
            name="url_pattern", type=SearchFieldDataType.String, filterable=True
        ),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=VECTOR_DIMENSIONS,
            vector_search_profile_name=VECTOR_PROFILE,
        ),
        SearchableField(
            name="mall_name", type=SearchFieldDataType.String, filterable=True
        ),
        SimpleField(
            name="unique_property_id", type=SearchFieldDataType.Int64, filterable=True
        ),
        SimpleField(name="document_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="guide_id", type=SearchFieldDataType.Int32, filterable=True),
        SimpleField(
            name="help_content_id", type=SearchFieldDataType.Int32, filterable=True
        ),
        SimpleField(
            name="announcement_event_id", type=SearchFieldDataType.Int32, filterable=True
        ),
        SimpleField(name="section", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="chunk_index", type=SearchFieldDataType.Int32),
        SimpleField(name="page_number", type=SearchFieldDataType.Int32),
    ]

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name=HNSW_ALGO)],
        profiles=[
            VectorSearchProfile(
                name=VECTOR_PROFILE, algorithm_configuration_name=HNSW_ALGO
            )
        ],
    )
    return SearchIndex(name=index_name, fields=fields, vector_search=vector_search)


async def ensure_index_exists(endpoint: str, api_key: str, index_name: str) -> bool:
    """Create the index if absent.  Returns True when it created it.

    Existing indexes are left untouched — no update/migrate here; schema
    changes are a deliberate operation, not a job side effect.
    """
    from azure.core.credentials import AzureKeyCredential
    from azure.core.exceptions import ResourceNotFoundError
    from azure.search.documents.indexes.aio import SearchIndexClient

    client = SearchIndexClient(endpoint=endpoint, credential=AzureKeyCredential(api_key))
    try:
        try:
            await client.get_index(index_name)
            log.debug("indexer.index_exists", index=index_name)
            return False
        except ResourceNotFoundError:
            pass
        await client.create_index(build_index_definition(index_name))
        log.info("indexer.index_created", index=index_name)
        return True
    finally:
        await client.close()
