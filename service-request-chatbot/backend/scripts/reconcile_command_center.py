#!/usr/bin/env python3
"""Reconcile the AI Command Center API against raw SQL.

Calls the four ``/api/v1/dashboard/ai-command-center/*`` sections for one
window, recomputes each headline KPI with independent SQL written from the
definitions in ``docs/ai-command-center-full-plan.md`` §3 (not by reusing the
repository), and prints PASS / FAIL per figure.  Run it against staging before
sign-off, and after any change to the analytics queries.

Usage:
    cd backend
    .venv/bin/python scripts/reconcile_command_center.py \\
        --base-url http://localhost:8100 --days 30 [--agent rdd_handover_agent]

Reads DATABASE_URL and MSP_SERVICE_TOKEN from settings / .env.  Exit code is
the number of mismatches, so it can gate a pipeline.

The API caches each section for 60 s; run this against a quiet window (or
right after a restart) so both sides see the same rows.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings

QUESTION = "lower(trim(coalesce(t.input_message, ''))) NOT IN ('', 'none')"
CREATE_INTENTS = (
    "('CREATE_RDD_SERVICE_REQUEST','CREATE_HANDED_OVER_SR','CREATE_FITOUT_CONFIRM_FOS',"
    "'CREATE_FITOUT_CONFIRM_END','CREATE_FITOUT_CONFIRM_TRADING','CREATE_WORK_PERMIT_SR')"
)

# Each check: (label, api path in the section payload, SQL returning one value).
# ``:from`` / ``:to`` bind the window; ``{agent_t}`` / ``{agent_e}`` expand to an
# agent filter on traces / on events (empty when no agent is given).
CHECKS: list[tuple[str, str, str, str]] = [
    (
        "K1 active users",
        "overview",
        "kpis.active_users.value",
        (
            f"SELECT count(DISTINCT t.user_id) FROM agent_traces t "
            f"WHERE t.created_at BETWEEN :from AND :to AND {QUESTION} {{agent_t}}"
        ),
    ),
    (
        "K4 AI interactions",
        "overview",
        "kpis.interactions.value",
        (
            f"SELECT count(*) FROM agent_traces t "
            f"WHERE t.created_at BETWEEN :from AND :to AND {QUESTION} {{agent_t}}"
        ),
    ),
    (
        "K8 answer rate — answered",
        "overview",
        "kpis.answer_rate.numerator",
        (
            f"SELECT count(*) FROM agent_traces t WHERE t.created_at BETWEEN :from AND :to "
            f"AND {QUESTION} AND t.answer_status = 'ANSWERED' {{agent_t}}"
        ),
    ),
    (
        "K8 answer rate — classified",
        "overview",
        "kpis.answer_rate.denominator",
        (
            f"SELECT count(*) FROM agent_traces t WHERE t.created_at BETWEEN :from AND :to "
            f"AND {QUESTION} AND t.answer_status IS NOT NULL {{agent_t}}"
        ),
    ),
    (
        "K9 escalation — not answered",
        "overview",
        "kpis.escalation_rate.numerator",
        (
            f"SELECT count(*) FROM agent_traces t WHERE t.created_at BETWEEN :from AND :to "
            f"AND {QUESTION} AND t.answer_status IN ('UNANSWERED','FAILED') {{agent_t}}"
        ),
    ),
    (
        "K5 sessions",
        "usage",
        "kpis.sessions",
        (
            "SELECT count(DISTINCT t.session_id) FROM agent_traces t "
            "WHERE t.created_at BETWEEN :from AND :to {agent_t}"
        ),
    ),
    (
        "K10 containment — contained sessions",
        "usage",
        "kpis.containment_rate.numerator",
        (
            f"SELECT count(*) FROM (SELECT t.session_id FROM agent_traces t "
            f"WHERE t.created_at BETWEEN :from AND :to AND {QUESTION} {{agent_t}} "
            f"GROUP BY t.session_id "
            f"HAVING count(*) FILTER (WHERE t.answer_status IN ('UNANSWERED','FAILED')) = 0) s"
        ),
    ),
    (
        "K19 SRs created via AI",
        "productivity",
        "kpis.srs_created",
        (
            "SELECT count(DISTINCT coalesce(e.properties->>'sr_id', e.id::text)) "
            "FROM analytics_events e WHERE e.event_name = 'sr_submitted' "
            "AND e.occurred_at BETWEEN :from AND :to {agent_e}"
        ),
    ),
    (
        "K21 SR completion — completed",
        "productivity",
        "kpis.sr_completion_rate.numerator",
        (
            "SELECT count(DISTINCT e.properties->>'sr_id') FROM analytics_events e "
            "WHERE e.event_name = 'sr_submitted' AND e.occurred_at BETWEEN :from AND :to "
            "AND e.properties->>'sr_id' IN (SELECT c.properties->>'sr_id' FROM analytics_events c "
            "WHERE c.event_name = 'sr_lifecycle_completed' AND c.properties->>'sr_id' IS NOT NULL) "
            "{agent_e}"
        ),
    ),
    (
        "K20 SR submission — create-intent sessions",
        "productivity",
        "kpis.sr_submission_rate.denominator",
        (
            f"SELECT count(DISTINCT t.session_id) FROM agent_traces t "
            f"WHERE t.created_at BETWEEN :from AND :to AND t.intent IN {CREATE_INTENTS} {{agent_t}}"
        ),
    ),
    (
        "K26 approvals",
        "productivity",
        "kpis.approvals",
        (
            "SELECT count(*) FROM analytics_events e WHERE e.event_name = 'sr_approved' "
            "AND e.occurred_at BETWEEN :from AND :to {agent_e}"
        ),
    ),
    (
        "K16 tool calls",
        "overview",
        "kpis.tool_reliability.denominator",
        (
            "SELECT count(*) FROM agent_tool_calls c WHERE c.created_at BETWEEN :from AND :to "
            "{agent_c}"
        ),
    ),
    (
        "K16 tool calls succeeded",
        "overview",
        "kpis.tool_reliability.numerator",
        (
            "SELECT count(*) FROM agent_tool_calls c WHERE c.created_at BETWEEN :from AND :to "
            "AND c.success IS NOT FALSE {agent_c}"
        ),
    ),
    (
        "K13 feedback participation — rated sessions",
        "performance",
        "kpis.feedback_participation.numerator",
        (
            f"SELECT count(*) FROM (SELECT DISTINCT t.session_id FROM agent_traces t "
            f"WHERE t.created_at BETWEEN :from AND :to AND {QUESTION} {{agent_t}}) s "
            f"WHERE s.session_id IN (SELECT f.session_id FROM agent_feedback f "
            f"WHERE f.feedback_type = 'USER_RATING' AND f.session_id IS NOT NULL)"
        ),
    ),
    (
        "Thumbs up",
        "performance",
        "kpis.thumbs_up_rate.numerator",
        (
            "SELECT count(*) FROM agent_feedback f JOIN agent_traces t ON t.id = f.trace_id "
            "WHERE f.feedback_type = 'TURN_THUMBS' AND f.label = 'THUMBS_UP' "
            "AND t.created_at BETWEEN :from AND :to {agent_t}"
        ),
    ),
    (
        "Thumbs votes",
        "performance",
        "kpis.thumbs_up_rate.denominator",
        (
            "SELECT count(*) FROM agent_feedback f JOIN agent_traces t ON t.id = f.trace_id "
            "WHERE f.feedback_type = 'TURN_THUMBS' AND f.label IN ('THUMBS_UP','THUMBS_DOWN') "
            "AND t.created_at BETWEEN :from AND :to {agent_t}"
        ),
    ),
    (
        "K15 P95 latency (ms)",
        "overview",
        "kpis.p95_latency_ms.value",
        (
            "SELECT round(percentile_cont(0.95) WITHIN GROUP (ORDER BY t.total_latency_ms)) "
            "FROM agent_traces t WHERE t.created_at BETWEEN :from AND :to "
            "AND t.total_latency_ms IS NOT NULL {agent_t}"
        ),
    ),
]


def _dig(payload: dict[str, Any], path: str) -> Any:
    node: Any = payload
    for part in path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def _agent_sql(agent: str | None) -> dict[str, str]:
    if not agent:
        return {"agent_t": "", "agent_e": "", "agent_c": ""}
    return {
        "agent_t": "AND t.active_agent = :agent",
        "agent_e": "AND e.session_id IN (SELECT a.session_id FROM agent_traces a "
        "WHERE a.active_agent = :agent)",
        "agent_c": "AND c.trace_id IN (SELECT a.id FROM agent_traces a "
        "WHERE a.active_agent = :agent)",
    }


async def main(base_url: str, days: int, agent: str | None) -> int:
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(days=days)
    params = {"from": start.isoformat(), "to": end.isoformat()}
    if agent:
        params["agent"] = agent
    headers = {"x-internal-api-token": settings.msp_service_token or ""}

    sections: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(base_url=base_url, timeout=60) as client:
        for section in {c[1] for c in CHECKS}:
            response = await client.get(
                f"/api/v1/dashboard/ai-command-center/{section}", params=params, headers=headers
            )
            response.raise_for_status()
            sections[section] = response.json()["data"]

    engine = create_async_engine(settings.database_url)
    binds: dict[str, Any] = {"from": start, "to": end}
    if agent:
        binds["agent"] = agent
    agent_sql = _agent_sql(agent)

    failures = 0
    width = max(len(c[0]) for c in CHECKS) + 2
    print(f"\nWindow {start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} UTC  agent={agent or 'all'}\n")
    print(f"{'KPI'.ljust(width)}{'API':>12}{'SQL':>12}  RESULT")
    print("-" * (width + 32))
    async with engine.connect() as conn:
        for label, section, path, sql in CHECKS:
            api_value = _dig(sections[section], path)
            sql_value = (await conn.execute(text(sql.format(**agent_sql)), binds)).scalar()
            api_n = None if api_value is None else float(api_value)
            sql_n = None if sql_value is None else float(sql_value)
            ok = api_n == sql_n or (
                api_n is not None and sql_n is not None and abs(api_n - sql_n) <= 1
                and "latency" in label
            )
            failures += 0 if ok else 1
            print(
                f"{label.ljust(width)}{api_value!s:>12}{sql_value!s:>12}  "
                f"{'PASS' if ok else 'FAIL'}"
            )
    await engine.dispose()
    print(f"\n{len(CHECKS) - failures}/{len(CHECKS)} reconciled")
    return failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", default="http://localhost:8100")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--agent", default=None)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.base_url, args.days, args.agent)))
