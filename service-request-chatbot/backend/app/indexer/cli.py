"""Indexer CLI — ``python -m app.indexer.cli``.

Modes (mutually exclusive):
  --full          delete_by_source then re-embed everything (legacy parity)
  --incremental   watermark-driven delta; scoped delete-by-entity then upsert
  --reconcile     delete index orphans whose entity no longer exists in the DB

``--source`` restricts to one source and, alone, implies ``--full``.
``--since`` overrides the stored watermark (incremental only).
``--dry-run`` builds and logs but never writes Search or watermarks.

Exit codes: 0 ok (including lock-held no-op), 1 unhandled error or all
selected sources failed, 2 partial failure.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from typing import Any

import structlog

from app.core.config import settings
from app.core.logging import configure_logging
from app.indexer import state as wm_state
from app.indexer.config import (
    require_cenomi_database_url,
    require_search_config,
)
from app.indexer.readers.pg_reader import CenomiPgReader
from app.indexer.sources import SOURCE_MAP
from app.integrations.factory import get_embedding_provider

log = structlog.get_logger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.indexer.cli",
        description="RAG knowledge indexer for cenomi-help-index (ticket 3547).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--full", action="store_true", help="Full rebuild.")
    mode.add_argument(
        "--incremental", action="store_true", help="Watermark-driven delta run."
    )
    mode.add_argument(
        "--reconcile", action="store_true", help="Delete orphaned index documents."
    )
    parser.add_argument(
        "--source",
        choices=sorted(SOURCE_MAP.keys()),
        help="Restrict to one source. Alone, implies --full.",
    )
    parser.add_argument(
        "--since",
        type=_parse_since,
        default=None,
        help="ISO timestamp overriding the stored watermark (e.g. 2026-08-01T00:00:00Z).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build documents and log counts; write nothing.",
    )
    parser.add_argument(
        "--dump-dir",
        default=None,
        help=(
            "Write the rows fetched from Postgres and the documents built from "
            "them to JSONL files in this directory, for inspection and for "
            "generating ground-truth questions. Vectors are omitted."
        ),
    )
    args = parser.parse_args(argv)

    # --source alone implies --full.
    if not (args.full or args.incremental or args.reconcile):
        args.full = True

    if args.full and args.since:
        log.warning("indexer.since_ignored_with_full")
        args.since = None

    return args


def _parse_since(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _dump_records(dump_dir: str, source_type: str, kind: str, records: list[dict]) -> None:
    """Write *records* to ``{dump_dir}/{source_type}.{kind}.jsonl``.

    Used to capture exactly what came out of Postgres and exactly what was
    indexed, so retrieval answers can be checked against ground truth.
    ``content_vector`` is stripped — 1536 floats per document would dwarf the
    readable content and is not useful for writing questions.
    """
    import json
    import os
    from datetime import date, datetime
    from decimal import Decimal

    def _default(o):
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        if isinstance(o, Decimal):
            return float(o)
        return str(o)

    os.makedirs(dump_dir, exist_ok=True)
    path = os.path.join(dump_dir, f"{source_type}.{kind}.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        for record in records:
            slim = {k: v for k, v in record.items() if k != "content_vector"}
            fh.write(json.dumps(slim, ensure_ascii=False, default=_default) + "\n")
    log.info("indexer.dumped", source=source_type, kind=kind,
             count=len(records), path=path)


def _as_aware_utc(value: datetime | None) -> datetime | None:
    """Cenomi DB timestamp columns are naive (assumed UTC) — see pg_reader._strip_tzinfo."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _cap_rows(rows: list[dict], cap: int) -> tuple[list[dict], datetime | None]:
    """Apply INDEXER_MAX_DOCS_PER_RUN, oldest first.

    Returns (rows_to_process, capped_at) where ``capped_at`` is the
    ``row_updated_at`` of the LAST processed row when the cap bit — the
    watermark is then set there so the skipped tail is picked up next run.
    """
    if len(rows) <= cap:
        return rows, None
    ordered = sorted(
        rows,
        key=lambda r: _as_aware_utc(r.get("row_updated_at")) or datetime.min.replace(tzinfo=UTC),
    )
    kept = ordered[:cap]
    capped_at = _as_aware_utc(kept[-1].get("row_updated_at"))
    log.warning(
        "indexer.rows_capped",
        cap=cap,
        total=len(rows),
        dropped=len(rows) - cap,
        capped_at=str(capped_at),
    )
    return kept, capped_at


async def _run_source_full(
    source: Any,
    reader: CenomiPgReader,
    provider: Any,
    search_client: Any,
    *,
    dry_run: bool,
    dump_dir: str | None = None,
) -> tuple[int, int]:
    """Full rebuild of one source.  Returns (upserted, deleted)."""
    from app.indexer.index import writer

    rows = await source.fetch_rows(reader, since=None)
    log.info("indexer.source_start", source=source.source_type, mode="full",
             candidate_rows=len(rows))
    if dump_dir:
        _dump_records(dump_dir, source.source_type, "rows", rows)
    docs = await source.build_documents(rows, provider, reader)
    if dump_dir:
        _dump_records(dump_dir, source.source_type, "docs", docs)

    if dry_run:
        log.info("indexer.dry_run_docs", source=source.source_type, docs=len(docs))
        return 0, 0

    deleted = await writer.delete_by_source(search_client, source.source_type)
    upserted = await writer.upsert_documents(
        search_client, docs, source=source.source_type
    )
    return upserted, deleted


async def _run_source_incremental(
    source: Any,
    reader: CenomiPgReader,
    provider: Any,
    search_client: Any,
    *,
    since: datetime,
    dry_run: bool,
    dump_dir: str | None = None,
) -> tuple[int, int, datetime | None]:
    """Incremental run.  Returns (upserted, deleted, capped_at)."""
    from app.indexer.index import writer

    rows = await source.fetch_rows(reader, since=since)
    rows, capped_at = _cap_rows(rows, settings.indexer_max_docs_per_run)
    deleted_ids = await source.fetch_deleted_ids(reader, since=since)
    log.info("indexer.source_start", source=source.source_type, mode="incremental",
             candidate_rows=len(rows), deleted_ids=len(deleted_ids),
             since=str(since))
    if dump_dir:
        _dump_records(dump_dir, source.source_type, "rows", rows)

    docs = await source.build_documents(rows, provider, reader)
    if dump_dir:
        _dump_records(dump_dir, source.source_type, "docs", docs)

    if dry_run:
        log.info("indexer.dry_run_docs", source=source.source_type,
                 docs=len(docs), deletes=len(deleted_ids))
        return 0, 0, capped_at

    deleted = 0
    # Scoped delete-before-upsert per entity present in the new doc set —
    # removes stale chunk tails (e.g. doc shrank from 3 chunks to 2).
    for filter_expression in source.scoped_filters(docs):
        deleted += await writer.delete_by_filter(
            search_client, filter_expression, source=source.source_type
        )
    for entity_id in deleted_ids:
        deleted += await writer.delete_by_filter(
            search_client, source.id_filter(entity_id), source=source.source_type
        )

    upserted = await writer.upsert_documents(
        search_client, docs, source=source.source_type
    )
    return upserted, deleted, capped_at


async def _run_source_reconcile(
    source: Any,
    reader: CenomiPgReader,
    search_client: Any,
    *,
    dry_run: bool,
) -> int:
    """Delete index documents whose entity id no longer exists in the DB."""
    from app.indexer.index import writer

    index_ids = await writer.fetch_index_source_ids(search_client, source.source_type)
    live_ids = set(await source.fetch_live_ids(reader))
    orphans = sorted(index_ids - live_ids)
    log.info("indexer.reconcile", source=source.source_type,
             index_entities=len(index_ids), live_entities=len(live_ids),
             orphans=len(orphans))
    if dry_run or not orphans:
        return 0
    deleted = 0
    for entity_id in orphans:
        deleted += await writer.delete_by_filter(
            search_client, source.id_filter(entity_id), source=source.source_type
        )
    return deleted


async def async_main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = parse_args(argv)
    run_started_at = datetime.now(UTC)
    dry_run = args.dry_run or settings.indexer_dry_run
    mode = "reconcile" if args.reconcile else ("incremental" if args.incremental else "full")

    selected = [args.source] if args.source else None
    from app.indexer.sources import SOURCE_MAP as source_map

    sources = []
    for alias, source in source_map.items():
        if selected is not None and alias not in selected:
            continue
        if not source.enabled:
            if selected is not None:
                log.info("indexer.source_disabled", source=source.source_type)
            continue
        sources.append(source)

    log.info("indexer.run_start", mode=mode, dry_run=dry_run,
             sources=[s.source_type for s in sources])
    if not sources:
        log.info("indexer.run_done", status="success", sources=0)
        return 0

    # ── Chatbot DB: advisory lock + watermarks (one held connection) ──────
    from sqlalchemy.ext.asyncio import create_async_engine

    chatbot_engine = create_async_engine(settings.database_url, pool_size=1)
    lock_conn = await chatbot_engine.connect()

    reader: CenomiPgReader | None = None
    search_client: Any = None
    failures: list[str] = []
    succeeded: list[str] = []

    try:
        if not await wm_state.try_advisory_lock(lock_conn):
            return 0  # another run in progress — clean no-op

        reader = CenomiPgReader(require_cenomi_database_url())

        provider = None
        if mode != "reconcile":
            provider = get_embedding_provider()
            if provider is None:
                raise RuntimeError(
                    "No embedding provider available — check EMBEDDING_PROVIDER "
                    "and its credentials."
                )

        needs_search = (not dry_run) or mode == "reconcile"
        if needs_search:
            endpoint, api_key, index_name = require_search_config()
            from app.indexer.index.schema import ensure_index_exists
            from app.indexer.index.writer import make_search_client

            if not dry_run:
                await ensure_index_exists(endpoint, api_key, index_name)
            search_client = make_search_client(endpoint, api_key, index_name)

        for source in sources:
            try:
                if mode == "reconcile":
                    deleted = await _run_source_reconcile(
                        source, reader, search_client, dry_run=dry_run
                    )
                    log.info("indexer.source_done", source=source.source_type,
                             mode=mode, deleted=deleted)
                    succeeded.append(source.source_type)
                    continue

                # Resolve effective mode per source.
                effective_since: datetime | None = None
                if mode == "incremental":
                    if args.since is not None:
                        effective_since = args.since
                    else:
                        wm = await wm_state.get_watermark(lock_conn, source.source_type)
                        if wm is not None and wm.watermark_at > wm_state.EPOCH:
                            effective_since = wm.watermark_at
                        # else: never-succeeded source → full build below.
                    if (
                        source.source_type == "mall_info"
                        and settings.indexer_mall_info_full_on_incremental
                    ):
                        effective_since = None

                run_full = mode == "full" or effective_since is None

                if not dry_run:
                    await wm_state.record_run_start(lock_conn, source.source_type)

                if run_full:
                    upserted, deleted = await _run_source_full(
                        source, reader, provider, search_client, dry_run=dry_run,
                        dump_dir=args.dump_dir,
                    )
                    capped_at = None
                else:
                    upserted, deleted, capped_at = await _run_source_incremental(
                        source, reader, provider, search_client,
                        since=effective_since, dry_run=dry_run,
                        dump_dir=args.dump_dir,
                    )

                if not dry_run:
                    new_wm = wm_state.compute_new_watermark(
                        run_started_at, capped_at=capped_at
                    )
                    await wm_state.save_watermark_success(
                        lock_conn, source.source_type,
                        watermark_at=new_wm,
                        docs_upserted=upserted,
                        docs_deleted=deleted,
                    )
                log.info("indexer.source_done", source=source.source_type,
                         mode="full" if run_full else "incremental",
                         upserted=upserted, deleted=deleted)
                succeeded.append(source.source_type)

            except Exception as exc:
                failures.append(source.source_type)
                log.exception("indexer.error", source=source.source_type, error=str(exc))
                if not dry_run:
                    try:
                        await wm_state.save_watermark_failure(
                            lock_conn, source.source_type, str(exc)
                        )
                    except Exception:
                        log.exception("indexer.watermark_save_failed",
                                      source=source.source_type)

        status = (
            "success" if not failures
            else ("failed" if not succeeded else "partial")
        )
        log.info("indexer.run_done", status=status,
                 succeeded=succeeded, failed=failures,
                 elapsed_s=int((datetime.now(UTC) - run_started_at).total_seconds()))
        if not failures:
            return 0
        return 1 if not succeeded else 2

    except Exception as exc:
        log.exception("indexer.error", error=str(exc))
        return 1
    finally:
        try:
            await wm_state.release_advisory_lock(lock_conn)
        except Exception:
            pass
        if search_client is not None:
            try:
                await search_client.close()
            except Exception:
                pass
        if reader is not None:
            await reader.dispose()
        await lock_conn.close()
        await chatbot_engine.dispose()


def main() -> None:
    sys.exit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
