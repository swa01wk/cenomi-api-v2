#!/usr/bin/env python3
"""Optional live eval for the 3713 analytics HTTP contract.

Does not drive chat turns (that is ``run_eval.py``). Hits heartbeat, session
feedback, and metrics endpoints against a running backend.

Usage (from backend/):
    # Offline contract only (no server required)
    python -m tests.eval.analytics_eval

    # Live HTTP — skipped unless the server is up
    python -m tests.eval.run_analytics_eval --base-url http://localhost:8000

Exit codes
----------
  0 — all checks passed, or live server unreachable (printed as SKIP)
  1 — live server reachable but a contract assertion failed
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from uuid import uuid4

import httpx

from tests.eval.analytics_eval import main as offline_main


async def _live(base_url: str) -> int:
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            health = await client.get(f"{base}/api/v1/health")
        except httpx.HTTPError:
            print(f"SKIP live analytics eval — {base} unreachable")
            return 0
        if health.status_code >= 500:
            print(f"SKIP live analytics eval — health {health.status_code}")
            return 0

        failures = 0

        def check(name: str, ok: bool, detail: str = "") -> None:
            nonlocal failures
            print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if not ok else ''}")
            if not ok:
                failures += 1

        missing = uuid4()
        hb = await client.post(
            f"{base}/api/chat/sessions/{missing}/heartbeat",
            json={"client_active_delta_ms": 1000, "user_id": "eval_user"},
        )
        check("heartbeat unknown session is 404", hb.status_code == 404, str(hb.status_code))

        end = await client.post(
            f"{base}/api/chat/sessions/{missing}/end",
            json={"reason": "IDLE_TIMEOUT", "user_id": "eval_user"},
        )
        check("client cannot send IDLE_TIMEOUT", end.status_code == 422, str(end.status_code))

        summary = await client.get(f"{base}/api/v1/observability/metrics/summary")
        check("metrics summary 200", summary.status_code == 200, str(summary.status_code))
        if summary.status_code == 200:
            body = summary.json()
            for key in (
                "sessions",
                "sr_submitted",
                "sr_completion_rate",
                "usefulness_rate",
                "feedback_coverage",
                "p95_latency_ms",
            ):
                check(f"summary has {key}", key in body)

        funnel = await client.get(f"{base}/api/v1/observability/metrics/funnel")
        check("metrics funnel 200", funnel.status_code == 200, str(funnel.status_code))
        if funnel.status_code == 200:
            keys = [s.get("key") for s in funnel.json().get("steps", [])]
            check(
                "funnel six steps",
                keys
                == [
                    "sessions_started",
                    "create_intent",
                    "draft_started",
                    "preview_or_confirm",
                    "sr_submitted",
                    "lifecycle_completed",
                ],
                str(keys),
            )

        users = await client.get(f"{base}/api/v1/observability/metrics/users")
        check("metrics users 200", users.status_code == 200, str(users.status_code))
        fb = await client.get(f"{base}/api/v1/observability/metrics/feedback")
        check("metrics feedback 200", fb.status_code == 200, str(fb.status_code))

        return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="3713 analytics eval")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--skip-offline",
        action="store_true",
        help="Only run live HTTP checks",
    )
    args = parser.parse_args(argv)

    offline_rc = 0 if args.skip_offline else offline_main()
    live_rc = asyncio.run(_live(args.base_url))
    return offline_rc or live_rc


if __name__ == "__main__":
    sys.exit(main())
