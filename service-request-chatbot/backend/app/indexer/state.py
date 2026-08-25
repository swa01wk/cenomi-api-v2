"""Watermarks and the cross-run advisory lock (chatbot Postgres).

The chatbot DB stores ONLY ``indexer_watermarks`` — Cenomi help/mall/event
rows are never copied here.  The advisory lock uses one key for full and
incremental runs so the two modes can never overlap; it is session-level, so
the acquiring connection must be held open for the entire run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import settings
from app.indexer.config import ADVISORY_LOCK_KEY

log = structlog.get_logger(__name__)

# Sentinel watermark for rows created before a source has ever succeeded.
# ``record_run_start`` / ``save_watermark_failure`` must not invent a real
# watermark — doing so would make the next incremental run skip every change
# that predates the failed first run.  The CLI treats a watermark at (or
# before) the epoch as "no watermark" and runs a full build.
EPOCH = datetime.fromtimestamp(0, tz=UTC)


@dataclass
class Watermark:
    source: str
    last_run_at: datetime
    watermark_at: datetime
    last_status: str
    docs_upserted: int
    docs_deleted: int
    error: str | None


def compute_new_watermark(
    run_started_at: datetime,
    *,
    lag_seconds: int | None = None,
    capped_at: datetime | None = None,
) -> datetime:
    """New watermark = run start − lag, unless the run was row-capped.

    The lag (default 300s) covers clock skew and in-flight transactions whose
    ``updated_at`` predates commit.  When ``INDEXER_MAX_DOCS_PER_RUN`` capped
    the batch, the watermark must be the last processed ``row_updated_at`` if
    that is EARLIER — otherwise the skipped tail would never be picked up.
    """
    lag = settings.indexer_watermark_lag if lag_seconds is None else lag_seconds
    candidate = run_started_at - timedelta(seconds=lag)
    if capped_at is not None and capped_at < candidate:
        return capped_at
    return candidate


async def try_advisory_lock(conn: AsyncConnection) -> bool:
    """Acquire the indexer advisory lock; False when another run holds it."""
    key1, key2 = ADVISORY_LOCK_KEY
    result = await conn.execute(
        text("SELECT pg_try_advisory_lock(:k1, :k2)"), {"k1": key1, "k2": key2}
    )
    acquired = bool(result.scalar())
    if not acquired:
        log.info("indexer.lock_held", key=ADVISORY_LOCK_KEY)
    return acquired


async def release_advisory_lock(conn: AsyncConnection) -> None:
    key1, key2 = ADVISORY_LOCK_KEY
    await conn.execute(
        text("SELECT pg_advisory_unlock(:k1, :k2)"), {"k1": key1, "k2": key2}
    )


async def get_watermark(conn: AsyncConnection, source: str) -> Watermark | None:
    result = await conn.execute(
        text(
            "SELECT source, last_run_at, watermark_at, last_status, "
            "docs_upserted, docs_deleted, error "
            "FROM indexer_watermarks WHERE source = :source"
        ),
        {"source": source},
    )
    row = result.mappings().first()
    if row is None:
        return None
    return Watermark(**dict(row))


async def record_run_start(conn: AsyncConnection, source: str) -> None:
    """Stamp ``last_run_at`` at the start of a real (non-dry) run.

    Leaves ``watermark_at`` untouched, so a stuck or crashed job is visible as
    ``last_run_at`` far ahead of ``watermark_at``.
    """
    now = datetime.now(UTC)
    await conn.execute(
        text(
            "INSERT INTO indexer_watermarks "
            "(source, last_run_at, watermark_at, last_status) "
            "VALUES (:source, :now, :epoch, 'running') "
            "ON CONFLICT (source) DO UPDATE SET "
            "last_run_at = :now, last_status = 'running', updated_at = now()"
        ),
        {"source": source, "now": now, "epoch": EPOCH},
    )
    await conn.commit()


async def save_watermark_success(
    conn: AsyncConnection,
    source: str,
    *,
    watermark_at: datetime,
    docs_upserted: int,
    docs_deleted: int,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO indexer_watermarks "
            "(source, last_run_at, watermark_at, last_status, docs_upserted, docs_deleted, error) "
            "VALUES (:source, now(), :wm, 'success', :up, :del, NULL) "
            "ON CONFLICT (source) DO UPDATE SET "
            "watermark_at = :wm, last_status = 'success', "
            "docs_upserted = :up, docs_deleted = :del, error = NULL, updated_at = now()"
        ),
        {"source": source, "wm": watermark_at, "up": docs_upserted, "del": docs_deleted},
    )
    await conn.commit()


async def save_watermark_failure(conn: AsyncConnection, source: str, error: str) -> None:
    """Record failure WITHOUT advancing the watermark (at-least-once retry)."""
    await conn.execute(
        text(
            "INSERT INTO indexer_watermarks "
            "(source, last_run_at, watermark_at, last_status, error) "
            "VALUES (:source, now(), :epoch, 'failed', :error) "
            "ON CONFLICT (source) DO UPDATE SET "
            "last_status = 'failed', error = :error, updated_at = now()"
        ),
        {"source": source, "error": error[:2000], "epoch": EPOCH},
    )
    await conn.commit()
