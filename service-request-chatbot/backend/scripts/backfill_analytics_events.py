#!/usr/bin/env python3
"""Backfill SR analytics events from the audit log and trace history.

Why this exists
---------------
Until ticket 3784, ``sr_submitted`` never fired: ``TraceManager`` only emits it
for tool calls typed ``SERVICE_REQUEST_CREATE``, and every submission node
passed ``"API"``.  Reviewer decisions had no event at all.  The dashboard reads
these events, so history has to be reconstructed once:

* ``sr_submitted``  ← audit ``service_request.created``
* ``sr_approved``   ← audit ``sr.fm.approved`` / ``sr.rdd.final_approved``
* ``sr_lifecycle_completed`` ← audit ``sr.rdd.final_approved``, and traces that
  reached ``HANDED_OVER_COMPLETED`` / ``SR_APPROVED``
* ``sr_approved`` / ``sr_rejected`` ← work-permit review traces whose stage moved
  out of a review stage (the PATCH outcome is only on the trace)

Idempotent: every insert is guarded by ``NOT EXISTS`` on
(session_id, event_name, sr_id), the same key live emission dedupes on.

Usage:
    cd backend
    .venv/bin/python scripts/backfill_analytics_events.py          # dry run
    .venv/bin/python scripts/backfill_analytics_events.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings

# Each statement inserts events that are not already present and returns the
# number of rows it would add.  ``sr_id`` lives in different places per source.
_NOT_YET = """
    NOT EXISTS (
        SELECT 1 FROM analytics_events e
        WHERE e.session_id = src.session_id
          AND e.event_name = src.event_name
          AND coalesce(e.properties->>'sr_id', '') = coalesce(src.sr_id, '')
    )
"""

STATEMENTS: dict[str, str] = {
    "sr_submitted from audit": f"""
        WITH src AS (
            SELECT a.session_id,
                   coalesce(a.actor_user_id, s.user_id) AS user_id,
                   'sr_submitted' AS event_name,
                   a.after_state->>'sr_id' AS sr_id,
                   a.created_at AS occurred_at,
                   jsonb_build_object('sr_id', a.after_state->>'sr_id',
                                      'backfilled', true) AS properties
            FROM service_request_chat_audit_logs a
            JOIN chat_sessions s ON s.id = a.session_id
            WHERE a.action = 'service_request.created'
        )
        INSERT INTO analytics_events
            (id, occurred_at, user_id, session_id, event_name, properties)
        SELECT gen_random_uuid(), occurred_at, user_id, session_id, event_name, properties
        FROM src WHERE user_id IS NOT NULL AND {_NOT_YET}
    """,
    "sr_approved from audit (FM / RDD)": f"""
        WITH src AS (
            SELECT a.session_id,
                   coalesce(a.actor_user_id, s.user_id) AS user_id,
                   'sr_approved' AS event_name,
                   a.after_state->>'sr_id' AS sr_id,
                   a.created_at AS occurred_at,
                   jsonb_build_object(
                       'sr_id', a.after_state->>'sr_id',
                       'flow', CASE a.action WHEN 'sr.fm.approved' THEN 'handover_fm'
                                             ELSE 'handover_rdd' END,
                       'stage', CASE a.action WHEN 'sr.fm.approved' THEN 'FM_REVIEW'
                                              ELSE 'RDD_REVIEW' END,
                       'backfilled', true) AS properties
            FROM service_request_chat_audit_logs a
            JOIN chat_sessions s ON s.id = a.session_id
            WHERE a.action IN ('sr.fm.approved', 'sr.rdd.final_approved')
        )
        INSERT INTO analytics_events
            (id, occurred_at, user_id, session_id, event_name, properties)
        SELECT gen_random_uuid(), occurred_at, user_id, session_id, event_name, properties
        FROM src WHERE user_id IS NOT NULL AND {_NOT_YET}
    """,
    "sr_lifecycle_completed from audit (RDD final approve)": f"""
        WITH src AS (
            SELECT DISTINCT ON (a.after_state->>'sr_id')
                   a.session_id,
                   coalesce(a.actor_user_id, s.user_id) AS user_id,
                   'sr_lifecycle_completed' AS event_name,
                   a.after_state->>'sr_id' AS sr_id,
                   a.created_at AS occurred_at,
                   jsonb_build_object('sr_id', a.after_state->>'sr_id',
                                      'workflow_stage', 'SR_COMPLETED',
                                      'backfilled', true) AS properties
            FROM service_request_chat_audit_logs a
            JOIN chat_sessions s ON s.id = a.session_id
            WHERE a.action = 'sr.rdd.final_approved'
              AND a.after_state->>'sr_id' IS NOT NULL
            ORDER BY a.after_state->>'sr_id', a.created_at
        )
        INSERT INTO analytics_events
            (id, occurred_at, user_id, session_id, event_name, properties)
        SELECT gen_random_uuid(), occurred_at, user_id, session_id, event_name, properties
        FROM src WHERE user_id IS NOT NULL AND {_NOT_YET}
    """,
    "sr_lifecycle_completed from traces (handed-over / fit-out approved)": f"""
        WITH terminal AS (
            SELECT DISTINCT ON (t.session_id)
                   t.session_id, t.user_id, t.workflow_stage_after, t.completed_at
            FROM agent_traces t
            WHERE t.status = 'SUCCESS'
              AND t.workflow_stage_after IN ('HANDED_OVER_COMPLETED', 'SR_APPROVED')
              AND t.completed_at IS NOT NULL
            ORDER BY t.session_id, t.completed_at
        ), src AS (
            SELECT tm.session_id,
                   tm.user_id,
                   'sr_lifecycle_completed' AS event_name,
                   (SELECT a.after_state->>'sr_id'
                      FROM service_request_chat_audit_logs a
                     WHERE a.session_id = tm.session_id
                       AND a.action = 'service_request.created'
                     ORDER BY a.created_at DESC LIMIT 1) AS sr_id,
                   tm.completed_at AS occurred_at,
                   tm.workflow_stage_after AS stage
            FROM terminal tm
        )
        INSERT INTO analytics_events
            (id, occurred_at, user_id, session_id, event_name, properties)
        SELECT gen_random_uuid(), occurred_at, user_id, session_id, event_name,
               jsonb_strip_nulls(jsonb_build_object('sr_id', sr_id,
                                                    'workflow_stage', stage,
                                                    'backfilled', true))
        FROM src WHERE {_NOT_YET}
    """,
    "work-permit decisions from traces": f"""
        WITH src AS (
            SELECT t.session_id,
                   t.user_id,
                   CASE WHEN t.workflow_stage_after = 'WP_REJECTED'
                        THEN 'sr_rejected' ELSE 'sr_approved' END AS event_name,
                   NULL::text AS sr_id,
                   t.completed_at AS occurred_at,
                   jsonb_build_object('flow', 'work_permit',
                                      'stage', t.workflow_stage_before,
                                      'backfilled', true) AS properties
            FROM agent_traces t
            WHERE t.status = 'SUCCESS'
              AND t.workflow_stage_before IN
                  ('WP_DD_REVIEW', 'WP_FM_REVIEW', 'WP_MM_REVIEW', 'WP_MM_OPS_REVIEW')
              AND t.workflow_stage_after IS DISTINCT FROM t.workflow_stage_before
              AND t.workflow_stage_after IN
                  ('WP_DD_REVIEW', 'WP_FM_REVIEW', 'WP_MM_REVIEW', 'WP_MM_OPS_REVIEW',
                   'WP_APPROVED', 'WP_REJECTED')
              AND t.completed_at IS NOT NULL
        )
        INSERT INTO analytics_events
            (id, occurred_at, user_id, session_id, event_name, properties)
        SELECT gen_random_uuid(), occurred_at, user_id, session_id, event_name, properties
        FROM src WHERE {_NOT_YET}
    """,
}


async def main(apply: bool) -> None:
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as conn:
        trans = await conn.begin()
        for label, sql in STATEMENTS.items():
            result = await conn.execute(text(sql))
            print(f"{label}: {result.rowcount} row(s)")
        if apply:
            await trans.commit()
            print("applied")
        else:
            await trans.rollback()
            print("dry run — rolled back (pass --apply to write)")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="commit the inserts")
    asyncio.run(main(parser.parse_args().apply))
