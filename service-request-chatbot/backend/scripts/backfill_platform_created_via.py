#!/usr/bin/env python3
"""Mark service requests the AI assistant already created as ``AI_ASSISTANT``.

Platform migration 1789482400 added ``service_request.created_via`` with every
existing row set to ``USER``.  SRs the assistant created before the AI backend
started sending ``created_via`` are known only to the AI backend: its
``sr_submitted`` events (and, as a fallback, the ``service_request.created``
audit rows).  This copies that knowledge to the platform once per environment.

Reads the SR ids from ``DATABASE_URL`` (AI backend) and updates
``CENOMI_DATABASE_URL`` (platform): ``service_request`` and both archive
tables, only rows still ``USER``.  Idempotent; dry run by default.

Usage:
    cd backend
    .venv/bin/python scripts/backfill_platform_created_via.py          # dry run
    .venv/bin/python scripts/backfill_platform_created_via.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings

AI_CREATED_IDS_SQL = """
    SELECT DISTINCT properties->>'sr_id' AS sr_id
    FROM analytics_events
    WHERE event_name = 'sr_submitted' AND properties->>'sr_id' IS NOT NULL
    UNION
    SELECT DISTINCT after_state->>'sr_id'
    FROM service_request_chat_audit_logs
    WHERE action = 'service_request.created' AND after_state->>'sr_id' IS NOT NULL
"""

PLATFORM_TABLES = (
    "cenomi.service_request",
    "cenomi.service_request_archive_recent",
    "cenomi.service_request_archive_cold",
)


async def main(apply: bool) -> None:
    if not settings.cenomi_database_url:
        sys.exit("CENOMI_DATABASE_URL is not set")

    ai_engine = create_async_engine(settings.database_url)
    async with ai_engine.connect() as conn:
        raw = [r[0] for r in await conn.execute(text(AI_CREATED_IDS_SQL))]
    await ai_engine.dispose()
    ids = sorted({int(v) for v in raw if str(v).strip().isdigit()})
    print(f"SRs the assistant created (AI backend): {len(ids)}")
    if not ids:
        return

    platform = create_async_engine(settings.cenomi_database_url)
    async with platform.connect() as conn:
        trans = await conn.begin()
        for table in PLATFORM_TABLES:
            found = (
                await conn.execute(
                    text(
                        f"SELECT count(*) FROM {table} WHERE service_request_id IN :ids"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"ids": ids},
                )
            ).scalar_one()
            updated = await conn.execute(
                text(
                    f"UPDATE {table} SET created_via = 'AI_ASSISTANT' "
                    "WHERE service_request_id IN :ids AND created_via = 'USER'"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": ids},
            )
            print(f"{table}: {found} found, {updated.rowcount} marked AI_ASSISTANT")
        if apply:
            await trans.commit()
            print("applied")
        else:
            await trans.rollback()
            print("dry run — rolled back (pass --apply to write)")
    await platform.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="commit the updates")
    asyncio.run(main(parser.parse_args().apply))
