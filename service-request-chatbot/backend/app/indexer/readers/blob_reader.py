"""Azure Blob download for the flagged PDF sources (blob name = document_id)."""

from __future__ import annotations

import structlog

from app.core.config import settings

log = structlog.get_logger(__name__)


async def download_document_blob(document_id: str | int) -> bytes | None:
    """Download the PDF blob for *document_id*, or None when unavailable.

    ``azure-storage-blob`` lives in the ``indexer`` extra — imported lazily.
    """
    if not settings.indexer_blob_connection_string or not settings.indexer_blob_container:
        log.warning("indexer.blob_config_missing", document_id=str(document_id))
        return None

    from azure.storage.blob.aio import BlobServiceClient

    async with BlobServiceClient.from_connection_string(
        settings.indexer_blob_connection_string
    ) as service:
        blob = service.get_blob_client(
            container=settings.indexer_blob_container, blob=str(document_id)
        )
        try:
            downloader = await blob.download_blob()
            return await downloader.readall()
        except Exception as exc:
            log.warning(
                "indexer.blob_download_failed",
                document_id=str(document_id),
                error=str(exc),
            )
            return None
