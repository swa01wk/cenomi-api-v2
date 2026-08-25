"""Indexer status & re-index endpoints (ticket 3547 follow-up).

The indexer itself runs as a separate Container App Job image — the API
process deliberately does NOT install the ``indexer`` dependency extra and
therefore cannot embed or write to Azure Search in-process.  What the API
*can* do is read and reset the ``indexer_watermarks`` table in the chatbot
database, which is exactly what these endpoints expose:

GET  /indexer/status
    One row per source: watermark cursor, last run time, last status,
    last run's upsert/delete counts, and last error.

POST /indexer/reindex
    Request a rebuild by resetting the watermark cursor to the epoch for one
    source (or all).  The NEXT scheduled job run (cron */15 min) sees the
    epoch sentinel, treats the source as never-succeeded, and performs a full
    delete-and-rebuild of it.  This endpoint does NOT run the indexer — it
    schedules the work for the job, so the API responds instantly and the
    heavy lifting stays in the job image.

Both endpoints require an authenticated ADMIN (or global admin) caller when
RBAC is enforced.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.config import settings
from app.core.security import get_auth_context
from app.db.session import DbSession
from app.types.chat import AuthContext

router = APIRouter()

# Matches app.indexer.state.EPOCH — the "never succeeded / rebuild me"
# sentinel the CLI checks before choosing incremental vs full.
_EPOCH = datetime.fromtimestamp(0, tz=UTC)

# Valid source_type values (mirrors app.indexer.config.SOURCE_ALIASES values;
# hard-coded here so the API never imports the indexer package).
_SOURCE_TYPES: tuple[str, ...] = (
    "help_content",
    "mall_info",
    "key_contact",
    "event",
    "user_guide",
    "download_center",
)


def _require_admin(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
    """Indexer operations are admin-only when RBAC is enforced."""
    if settings.rbac_enforce:
        if not (auth.is_global_admin or "ADMIN" in auth.roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Indexer operations require the ADMIN role.",
            )
    return auth


class SourceStatus(BaseModel):
    source: str
    last_run_at: datetime
    watermark_at: datetime
    last_status: str
    docs_upserted: int
    docs_deleted: int
    error: str | None
    updated_at: datetime
    reindex_pending: bool = Field(
        description="True when the watermark is reset to the epoch — the next "
        "scheduled job run will fully rebuild this source."
    )


class IndexerStatusResponse(BaseModel):
    sources: list[SourceStatus]
    note: str = (
        "Rows appear after the first indexer run. The scheduled job runs "
        "--incremental every 15 minutes; a watermark at the epoch means a "
        "full rebuild is pending."
    )


class ReindexRequest(BaseModel):
    source: Literal[
        "help_content",
        "mall_info",
        "key_contact",
        "event",
        "user_guide",
        "download_center",
        "all",
    ] = Field(default="all", description="Source to rebuild, or 'all'.")


class ReindexResponse(BaseModel):
    reset_sources: list[str]
    message: str


@router.get(
    "/indexer/status",
    response_model=IndexerStatusResponse,
    summary="RAG indexer status — per-source watermarks and last-run results",
)
async def get_indexer_status(
    db: DbSession,
    _auth: AuthContext = Depends(_require_admin),
) -> IndexerStatusResponse:
    result = await db.execute(
        text(
            "SELECT source, last_run_at, watermark_at, last_status, "
            "docs_upserted, docs_deleted, error, updated_at "
            "FROM indexer_watermarks ORDER BY source"
        )
    )
    sources = [
        SourceStatus(
            **dict(row),
            reindex_pending=row["watermark_at"] <= _EPOCH,
        )
        for row in result.mappings().all()
    ]
    return IndexerStatusResponse(sources=sources)


@router.post(
    "/indexer/reindex",
    response_model=ReindexResponse,
    summary="Request a full rebuild of one source (or all) on the next job run",
)
async def request_reindex(
    body: ReindexRequest,
    db: DbSession,
    _auth: AuthContext = Depends(_require_admin),
) -> ReindexResponse:
    targets = list(_SOURCE_TYPES) if body.source == "all" else [body.source]

    # Reset existing rows to the epoch sentinel; insert sentinel rows for
    # sources that have never run so their first run is an explicit full build.
    for source in targets:
        await db.execute(
            text(
                "INSERT INTO indexer_watermarks "
                "(source, last_run_at, watermark_at, last_status) "
                "VALUES (:source, :epoch, :epoch, 'reindex_requested') "
                "ON CONFLICT (source) DO UPDATE SET "
                "watermark_at = :epoch, last_status = 'reindex_requested', "
                "error = NULL, updated_at = now()"
            ),
            {"source": source, "epoch": _EPOCH},
        )
    await db.commit()

    return ReindexResponse(
        reset_sources=targets,
        message=(
            "Watermarks reset. The next scheduled indexer run (every 15 "
            "minutes) will fully rebuild the listed sources. To rebuild "
            "immediately, start the Container App Job manually: "
            "az containerapp job start --name <indexer_job_name> ..."
        ),
    )
