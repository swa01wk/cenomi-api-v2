#!/usr/bin/env python3
"""Sales Data Agent evaluation runner.

Drives SD200–SD231 scenarios against the live AI backend
(default http://localhost:8000) and mock backend (default http://localhost:8001).

CSV-upload turns (``pre_upload_csv=True``) are handled transparently:
  1. Runner POSTs a minimal CSV to ``POST /api/v2/sales-data/upload`` (AI backend).
  2. The returned ``session_id`` is injected as ``corrected_fields.csv_session_id``
     so ``csv_validation_node`` can find the file without relying on the DB session UUID.

Usage
-----
From the backend/ directory:

    python -m tests.eval.run_sales_data_eval --tier sd0
    python -m tests.eval.run_sales_data_eval --tier sd1
    python -m tests.eval.run_sales_data_eval --tier all
    python -m tests.eval.run_sales_data_eval --verbose
    python -m tests.eval.run_sales_data_eval --json results/sd_tier1.json

Exit codes
----------
  0 — all selected scenarios passed
  1 — one or more scenarios failed, or backend unreachable
"""

from __future__ import annotations

import argparse
import asyncio
import json as _json
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from tests.eval.sales_data_scenarios import (
    ALL_SD_SCENARIOS,
    SalesDataScenario,
    SalesDataTurn,
    TIER_SD0,
    TIER_SD1,
    TIER_SD2,
    TIER_SD3,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHAT_ENDPOINT = "/api/chat/service-request"
_UPLOAD_ENDPOINT = "/api/v2/sales-data/upload"
_DOC_UPLOAD_ENDPOINT = "/api/v2/sales-data/supporting-doc"
_AUTH_ENDPOINT = "/api/auth/login"

_DEFAULT_BASE_URL = "http://localhost:8000"
_DEFAULT_MOCK_URL = "http://localhost:8001"

_TIER_MAP: dict[str, list[SalesDataScenario]] = {
    "sd0": TIER_SD0,
    "sd1": TIER_SD1,
    "sd2": TIER_SD2,
    "sd3": TIER_SD3,
    "all": ALL_SD_SCENARIOS,
}

# ANSI colours
_NO_COLOUR = not sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return text if _NO_COLOUR else f"\033[{code}m{text}\033[0m"


def green(s: str) -> str:  return _c("92", s)
def red(s: str) -> str:    return _c("91", s)
def yellow(s: str) -> str: return _c("93", s)
def cyan(s: str) -> str:   return _c("96", s)
def bold(s: str) -> str:   return _c("1",  s)
def dim(s: str) -> str:    return _c("2",  s)


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------

@dataclass
class TurnResult:
    turn_idx: int
    message: str
    note: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    response_message: str = ""
    ui_type: str = ""
    workflow_stage: str = ""
    latency_ms: int = 0
    http_error: Optional[str] = None


@dataclass
class ScenarioResult:
    scenario: SalesDataScenario
    turn_results: list[TurnResult] = field(default_factory=list)
    session_id: Optional[str] = None

    @property
    def passed(self) -> bool:
        return bool(self.turn_results) and all(t.passed for t in self.turn_results)

    @property
    def turns_passed(self) -> int:
        return sum(1 for t in self.turn_results if t.passed)

    @property
    def turns_total(self) -> int:
        return len(self.turn_results)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EVAL_BRAND_PROPERTY: dict[str, tuple[str, str]] = {
    "LC-1001": ("Noon Fashion", "Riyadh Park"),
    "LC-1002": ("Pure Gym", "Riyadh Park"),
    "LC-1003": ("Pure Gym", "Mall of Arabia"),
    "LC-1004": ("Noon Fashion", "Riyadh Park"),
    "LC-1005": ("Noon Fashion", "Riyadh Park"),
}


def _make_csv_bytes(rows: list[dict]) -> bytes:
    """Serialise row dicts as the Cenomi platform CSV template."""
    from app.agents.services.sales_data_csv import render_platform_csv

    if not rows:
        return b""
    filled: list[dict] = []
    for row in rows:
        mapped = dict(row)
        lease = str(mapped.get("lease_code") or mapped.get("Lease code") or "")
        brand, prop = _EVAL_BRAND_PROPERTY.get(lease, ("Noon Fashion", "Riyadh Park"))
        if not mapped.get("brand") and not mapped.get("Brand"):
            mapped["brand"] = brand
        if not mapped.get("property") and not mapped.get("Property"):
            mapped["property"] = prop
        filled.append(mapped)
    return render_platform_csv(filled).encode("utf-8")


async def _authenticate(
    client: httpx.AsyncClient,
    base_url: str,
    email: str,
    password: str,
) -> tuple[Optional[str], Optional[str]]:
    """Login and return (access_token, user_id)."""
    try:
        resp = await client.post(
            f"{base_url.rstrip('/')}{_AUTH_ENDPOINT}",
            json={"username": email, "password": password},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("access_token"), data.get("user_id")
    except Exception as exc:
        print(f"  {yellow('WARN')} Auth failed for {email}: {exc}", file=sys.stderr)
        return None, None


async def _upload_csv(
    client: httpx.AsyncClient,
    base_url: str,
    token: Optional[str],
    rows: list[dict],
    session_id: Optional[str],
) -> Optional[str]:
    """Upload CSV rows and return the cache session_id."""
    csv_bytes = _make_csv_bytes(rows)
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    files = {"file": ("test_eval.csv", csv_bytes, "text/csv")}
    data = {}
    if session_id:
        data["session_id"] = session_id

    try:
        resp = await client.post(
            f"{base_url.rstrip('/')}{_UPLOAD_ENDPOINT}",
            files=files,
            data=data,
            headers=headers,
            timeout=20.0,
        )
        if not resp.is_success:
            print(f"  {yellow('WARN')} CSV upload failed ({resp.status_code}): {resp.text[:200]}")
            return None
        return resp.json().get("session_id")
    except Exception as exc:
        print(f"  {yellow('WARN')} CSV upload error: {exc}")
        return None


async def _upload_doc(
    client: httpx.AsyncClient,
    base_url: str,
    token: Optional[str],
    session_id: Optional[str],
) -> Optional[str]:
    """Upload a tiny PDF and return the cache session_id."""
    pdf_bytes = b"%PDF-1.1\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    files = {"file": ("reupload.pdf", pdf_bytes, "application/pdf")}
    data = {}
    if session_id:
        data["session_id"] = session_id

    try:
        resp = await client.post(
            f"{base_url.rstrip('/')}{_DOC_UPLOAD_ENDPOINT}",
            files=files,
            data=data,
            headers=headers,
            timeout=20.0,
        )
        if not resp.is_success:
            print(f"  {yellow('WARN')} Doc upload failed ({resp.status_code}): {resp.text[:200]}")
            return None
        return resp.json().get("session_id")
    except Exception as exc:
        print(f"  {yellow('WARN')} Doc upload error: {exc}")
        return None


async def _chat_turn(
    client: httpx.AsyncClient,
    base_url: str,
    token: Optional[str],
    user_id: str,
    session_id: Optional[str],
    turn: SalesDataTurn,
    csv_session_id: Optional[str],
    doc_session_id: Optional[str] = None,
) -> tuple[dict[str, Any], int]:
    """Send one chat turn and return (response_dict, latency_ms)."""
    payload: dict[str, Any] = {
        "session_id": session_id,
        "user_id": user_id,
        "message": turn.message,
        "attachments": [],
    }
    if turn.action:
        payload["action"] = turn.action

    # Merge corrected_fields from turn + injected csv/doc session ids
    cf: dict[str, Any] = dict(turn.corrected_fields or {})
    if csv_session_id:
        cf["csv_session_id"] = csv_session_id
    if doc_session_id:
        cf["doc_session_id"] = doc_session_id
    if cf:
        payload["corrected_fields"] = cf

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    t0 = time.monotonic()
    try:
        resp = await client.post(
            f"{base_url.rstrip('/')}{_CHAT_ENDPOINT}",
            json=payload,
            headers=headers,
            timeout=60.0,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        resp.raise_for_status()
        return resp.json(), latency_ms
    except httpx.HTTPStatusError as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {"_error": str(exc), "_body": exc.response.text[:500]}, latency_ms
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {"_error": str(exc)}, latency_ms


def _assert_turn(turn: SalesDataTurn, data: dict, latency_ms: int) -> TurnResult:
    """Score one chat turn response against the turn's assertions."""
    tr = TurnResult(
        turn_idx=0,
        message=turn.message,
        note=turn.note,
        passed=True,
        latency_ms=latency_ms,
    )

    # HTTP error
    if "_error" in data:
        tr.failures.append(f"HTTP error: {data['_error']} — {data.get('_body','')[:200]}")
        tr.passed = False
        return tr

    ui: dict = data.get("ui") or {}
    state: dict = data.get("state") or {}
    message: str = data.get("message") or ui.get("message") or ""

    tr.response_message = message
    tr.ui_type = ui.get("type", "")
    tr.workflow_stage = state.get("workflow_stage") or ""

    # ── Hard: ui_type ────────────────────────────────────────────────────────
    if turn.expect_ui_type and ui.get("type") != turn.expect_ui_type:
        tr.failures.append(
            f"ui.type: expected '{turn.expect_ui_type}' got '{ui.get('type')}'"
        )
        tr.passed = False

    # ── Hard: workflow_stage ─────────────────────────────────────────────────
    if turn.expect_workflow_stage:
        expected = turn.expect_workflow_stage
        allowed = expected if isinstance(expected, list) else [expected]
        if state.get("workflow_stage") not in allowed:
            tr.failures.append(
                f"workflow_stage: expected {allowed} got '{state.get('workflow_stage')}'"
            )
            tr.passed = False

    # ── Soft: keyword must appear ─────────────────────────────────────────────
    if turn.expect_keywords:
        msg_lower = message.lower()
        if not any(kw.lower() in msg_lower for kw in turn.expect_keywords):
            tr.warnings.append(
                f"None of {turn.expect_keywords} found in response: "
                f'"{message[:120]}"'
            )

    # ── Soft: keyword must NOT appear ────────────────────────────────────────
    if turn.expect_no_keywords:
        msg_lower = message.lower()
        found = [kw for kw in turn.expect_no_keywords if kw.lower() in msg_lower]
        if found:
            tr.warnings.append(f"Forbidden keywords found in response: {found}")

    # ── Soft: latency ────────────────────────────────────────────────────────
    if latency_ms > 20_000:
        tr.warnings.append(f"High latency: {latency_ms} ms")

    return tr


async def _run_scenario(
    scenario: SalesDataScenario,
    base_url: str,
    verbose: bool,
    client: httpx.AsyncClient,
) -> ScenarioResult:
    result = ScenarioResult(scenario=scenario)

    print(f"\n▶ S{scenario.id} {cyan(scenario.name)}")
    print(f"  {dim(scenario.goal)}")

    # Isolate scenarios — restore PENDING seed rows in the mock backend
    try:
        await client.post(f"{_DEFAULT_MOCK_URL}/dev/mock/reset", timeout=5.0)
    except Exception:
        pass

    # Authenticate
    token, user_id_raw = await _authenticate(
        client, base_url, scenario.user_email, scenario.user_password
    )
    user_id = user_id_raw or scenario.user_email

    session_id: Optional[str] = None

    for idx, turn in enumerate(scenario.turns, start=1):
        print(f"  T{idx:<2}  {dim(turn.note or turn.message[:60])}", end="", flush=True)

        # Pre-upload CSV if required
        csv_session_id: Optional[str] = None
        if turn.pre_upload_csv:
            rows = turn.csv_rows or [
                {
                    "lease_code": "LC-1001",
                    "sales_period_start": "2026-08-01",
                    "sales_period_end": "2026-08-31",
                    "sales_amt": "85000",
                }
            ]
            csv_session_id = await _upload_csv(client, base_url, token, rows, session_id)
            if not csv_session_id:
                print(f"  → {red('FAIL')} (CSV upload failed)")
                tr = TurnResult(
                    turn_idx=idx,
                    message=turn.message,
                    note=turn.note,
                    passed=False,
                    failures=["CSV pre-upload failed — check /api/v2/sales-data/upload"],
                )
                result.turn_results.append(tr)
                break

        doc_session_id: Optional[str] = None
        if turn.pre_upload_doc:
            doc_session_id = await _upload_doc(client, base_url, token, session_id)
            if not doc_session_id:
                print(f"  → {red('FAIL')} (PDF upload failed)")
                tr = TurnResult(
                    turn_idx=idx,
                    message=turn.message,
                    note=turn.note,
                    passed=False,
                    failures=["PDF pre-upload failed — check /api/v2/sales-data/supporting-doc"],
                )
                result.turn_results.append(tr)
                break

        data, latency_ms = await _chat_turn(
            client, base_url, token, user_id, session_id, turn, csv_session_id, doc_session_id
        )

        # Update session_id from first successful response
        if "session_id" in data and not session_id:
            session_id = data["session_id"]
            result.session_id = session_id

        tr = _assert_turn(turn, data, latency_ms)
        tr.turn_idx = idx
        result.turn_results.append(tr)

        # Print result
        status_str = green("PASS") if tr.passed else red("FAIL")
        print(f"  → {status_str}  ({latency_ms} ms)")

        if tr.failures:
            for f in tr.failures:
                print(f"      {red('✗')} {f}")
        if tr.warnings:
            for w in tr.warnings:
                print(f"      {yellow('⚠')} {w}")
        if verbose:
            print(f"      ↳ ui={tr.ui_type!r}  stage={tr.workflow_stage!r}")
            print(f"      ↳ {dim(tr.response_message[:120])}")

        if not tr.passed:
            print(f"  {red('Stopping scenario — hard assertion failed.')}")
            break

    overall = green("PASSED") if result.passed else red("FAILED")
    print(f"  Result: {overall}  ({result.turns_passed}/{result.turns_total} turns)")
    return result


async def run_eval(
    base_url: str,
    tier: str,
    verbose: bool,
    json_out: Optional[str],
) -> int:
    scenarios = _TIER_MAP.get(tier)
    if scenarios is None:
        print(f"Unknown tier '{tier}'. Valid: {', '.join(_TIER_MAP)}", file=sys.stderr)
        return 1

    enabled = [s for s in scenarios if s.enabled]

    print(bold(f"\n{'═' * 68}"))
    print(bold(f"  SALES DATA AGENT EVAL — Tier {tier.upper()}"))
    print(bold(f"  {len(enabled)} scenario(s)  |  base_url={base_url}"))
    print(bold(f"{'═' * 68}"))

    # Health check
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{base_url.rstrip('/')}/api/v1/health", timeout=10.0)
            r.raise_for_status()
    except Exception as exc:
        print(red(f"\nBackend unreachable: {exc}"), file=sys.stderr)
        return 1

    results: list[ScenarioResult] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for scenario in enabled:
            sr = await _run_scenario(scenario, base_url, verbose, client)
            results.append(sr)

    # Summary
    passed_count = sum(1 for r in results if r.passed)
    total = len(results)
    turns_passed = sum(r.turns_passed for r in results)
    turns_total = sum(r.turns_total for r in results)

    print(f"\n{'─' * 68}")
    print(bold(f"  SUMMARY  Tier {tier.upper()}"))
    print(f"  Scenarios : {passed_count}/{total} passed")
    print(f"  Turns     : {turns_passed}/{turns_total} passed")

    failed = [r for r in results if not r.passed]
    if failed:
        print(f"\n  {red('FAILED SCENARIOS:')}")
        for r in failed:
            print(f"    S{r.scenario.id} — {r.scenario.name}")
            for tr in r.turn_results:
                if not tr.passed:
                    for f in tr.failures:
                        print(f"      T{tr.turn_idx}: {f}")
    else:
        print(f"  {green('All scenarios passed!')} ✓")
    print(f"{'─' * 68}\n")

    # JSON output
    if json_out:
        out = []
        for r in results:
            out.append({
                "scenario_id": r.scenario.id,
                "scenario_name": r.scenario.name,
                "tier": r.scenario.tier,
                "passed": r.passed,
                "session_id": r.session_id,
                "turns_passed": r.turns_passed,
                "turns_total": r.turns_total,
                "turns": [
                    {
                        "turn": tr.turn_idx,
                        "message": tr.message,
                        "passed": tr.passed,
                        "ui_type": tr.ui_type,
                        "workflow_stage": tr.workflow_stage,
                        "latency_ms": tr.latency_ms,
                        "failures": tr.failures,
                        "warnings": tr.warnings,
                        "response_message": tr.response_message[:200],
                    }
                    for tr in r.turn_results
                ],
            })
        with open(json_out, "w") as fh:
            _json.dump(out, fh, indent=2)
        print(f"JSON written → {json_out}")

    return 0 if all(r.passed for r in results) else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sales Data Agent evaluation runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base-url", default=_DEFAULT_BASE_URL)
    parser.add_argument(
        "--tier",
        default="all",
        choices=list(_TIER_MAP.keys()),
        help="Tier to run: sd0, sd1, sd2, sd3, all",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--json", dest="json_out", metavar="FILE")
    args = parser.parse_args()

    exit_code = asyncio.run(
        run_eval(args.base_url, args.tier, args.verbose, args.json_out)
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
