#!/usr/bin/env python3
"""Evaluation runner for the Handover Service Request chatbot.

Drives all 9 scenarios from docs/e2e-test-guide.md against the live backend
(or any URL you point it at) and prints a colour-coded pass/fail report.

Usage
-----
From the backend/ directory:

    # Run all 9 scenarios
    python -m tests.eval.run_eval

    # Run specific scenarios
    python -m tests.eval.run_eval --scenarios 1,3,6

    # Filter by tag
    python -m tests.eval.run_eval --tags happy-path,core

    # Show detailed turn-by-turn output (bot messages included)
    python -m tests.eval.run_eval --verbose

    # Write structured JSON results
    python -m tests.eval.run_eval --json results/tier_a.json

    # Point at a different server
    python -m tests.eval.run_eval --base-url http://staging.example.com

Exit codes
----------
  0 — all selected scenarios passed
  1 — one or more scenarios failed, or server unreachable
"""

from __future__ import annotations

import argparse
import asyncio
import json as _json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx

from tests.eval.scenarios import (
    SCENARIOS,
    TIER0_SCENARIOS,
    TIER1_SCENARIOS,
    TIER2_SCENARIOS,
    TIER3_SCENARIOS,
    Scenario,
    Turn,
)

_TIER_MAP: dict[int, list[Scenario]] = {
    0: TIER0_SCENARIOS,
    1: TIER1_SCENARIOS,
    2: TIER2_SCENARIOS,
    3: TIER3_SCENARIOS,
}

# ── ANSI colours ─────────────────────────────────────────────────────────────

_NO_COLOUR = not sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return text if _NO_COLOUR else f"\033[{code}m{text}\033[0m"


def green(s: str) -> str:   return _c("92", s)
def red(s: str) -> str:     return _c("91", s)
def yellow(s: str) -> str:  return _c("93", s)
def cyan(s: str) -> str:    return _c("96", s)
def bold(s: str) -> str:    return _c("1",  s)
def dim(s: str) -> str:     return _c("2",  s)


# ── Result data models ────────────────────────────────────────────────────────

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
    sr_reference: str = ""
    latency_ms: int = 0
    http_error: Optional[str] = None


@dataclass
class ScenarioResult:
    scenario: Scenario
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

    @property
    def turns_expected(self) -> int:
        return len(self.scenario.turns)


# ── HTTP helpers ──────────────────────────────────────────────────────────────

_ENDPOINT = "/api/chat/service-request"
_AUTH_ENDPOINT = "/api/auth/login"
# Default eval user credentials — MALL_MANAGER role so RBAC permits CREATE_SR.
_EVAL_USER_EMAIL = "aisha@cenomi.com"
_EVAL_USER_PASSWORD = "test1234"
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


async def _authenticate(
    client: httpx.AsyncClient,
    base_url: str,
    email: str = _EVAL_USER_EMAIL,
    password: str = _EVAL_USER_PASSWORD,
) -> tuple[Optional[str], Optional[str]]:
    """Authenticate against the local chatbot JWT endpoint.

    Returns (access_token, user_id).  Both may be None when auth fails.
    Without a token the supervisor RBAC check blocks action intents for
    anonymous users, causing packed-message CREATE_SR turns to return a
    plain ``message`` denial instead of a ``confirmation_card``.
    """
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
        print(yellow(f"  ⚠ Auth failed for {email}: {exc}"), file=sys.stderr)
        return None, None


async def _post_turn(
    client: httpx.AsyncClient,
    base_url: str,
    spec: Turn,
    session_id: Optional[str],
    token: Optional[str] = None,
    user_id: Optional[str] = None,
) -> tuple[dict[str, Any], int]:
    """POST one chat turn; return (response_body, latency_ms)."""
    payload: dict[str, Any] = {
        "user_id": user_id or "eval_runner",
        "message": spec.message,
        "attachments": [],
    }
    if session_id:
        payload["session_id"] = session_id
    if spec.action:
        payload["action"] = spec.action
    if spec.corrected_fields:
        payload["corrected_fields"] = spec.corrected_fields
    if spec.selected_lease_id:
        payload["selected_lease_id"] = spec.selected_lease_id

    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    t0 = time.monotonic()
    resp = await client.post(
        f"{base_url.rstrip('/')}{_ENDPOINT}",
        json=payload,
        headers=headers,
        timeout=90.0,
    )
    latency_ms = int((time.monotonic() - t0) * 1000)
    resp.raise_for_status()
    return resp.json(), latency_ms


# ── Assertion engine ──────────────────────────────────────────────────────────


def _assert_turn(
    spec: Turn,
    body: dict[str, Any],
) -> tuple[bool, list[str], list[str], str]:
    """Evaluate one turn's assertions.

    Returns (passed, hard_failures, soft_warnings, sr_reference).
    Hard failures set passed=False and stop the scenario.
    Soft warnings are reported but do not fail the scenario.
    """
    failures: list[str] = []
    warnings: list[str] = []
    sr_reference = ""

    ui_type: str = (body.get("ui") or {}).get("type", "") or ""
    state: dict = body.get("state") or {}
    workflow_stage: str = state.get("workflow_stage", "") or ""
    msg: str = body.get("message", "") or ""

    # ── Hard: UI type ─────────────────────────────────────────────────────────
    # "message" and "text_question" are both valid conversational-prompt types;
    # treat them as equivalent when asserting expect_ui_type="message".
    _CONVERSATIONAL = {"message", "text_question"}
    if spec.expect_ui_type is not None:
        expected = spec.expect_ui_type
        if expected == "message":
            if ui_type not in _CONVERSATIONAL:
                failures.append(
                    f"ui.type: expected a conversational type (message/text_question), "
                    f"got '{ui_type or '(empty)'}'"
                )
        elif ui_type != expected:
            failures.append(
                f"ui.type: expected '{expected}', got '{ui_type or '(empty)'}'"
            )

    # ── Hard: workflow stage ──────────────────────────────────────────────────
    if spec.expect_workflow_stage is not None:
        if workflow_stage != spec.expect_workflow_stage:
            failures.append(
                f"workflow_stage: expected '{spec.expect_workflow_stage}', "
                f"got '{workflow_stage or '(empty)'}'"
            )
        else:
            # Extract SR reference UUID from success message
            uuids = _UUID_RE.findall(msg)
            if uuids:
                sr_reference = uuids[0]

    # ── Hard: no-submit guard ────────────────────────────────────────────────
    if spec.expect_no_submit:
        ready = state.get("ready_to_submit", False)
        if ready:
            failures.append("ready_to_submit: expected False but got True")

    # ── Soft: keyword check ───────────────────────────────────────────────────
    if spec.expect_keywords:
        msg_lower = msg.lower()
        if not any(kw.lower() in msg_lower for kw in spec.expect_keywords):
            hint = f"Got: {msg[:160]!r}" if msg else "No message in response"
            warnings.append(
                f"Keywords {spec.expect_keywords!r} not found in message. {hint}"
            )

    # ── Soft: forbidden keywords (eligibility regressions) ────────────────────
    if spec.expect_no_keywords:
        ui_msg = str((body.get("ui") or {}).get("message") or "")
        haystack = f"{msg}\n{ui_msg}".lower()
        for kw in spec.expect_no_keywords:
            if kw.lower() in haystack:
                warnings.append(
                    f"Forbidden keyword {kw!r} found in message/ui.message"
                )

    # ── Soft: active_agent null check (post-reset) ────────────────────────────
    if spec.expect_no_active_agent:
        active_agent = body.get("active_agent")
        if active_agent:
            warnings.append(
                f"active_agent: expected null/empty after reset, got '{active_agent}'"
            )

    # ── Soft: ui.fields value check (confirmation_card) ──────────────────────
    if spec.expect_field_value:
        card_fields = (body.get("ui") or {}).get("fields", []) or []
        for label, expected_val in spec.expect_field_value.items():
            found = next(
                (f for f in card_fields if str(f.get("label", "")).lower() == label.lower()),
                None,
            )
            if not found:
                warnings.append(
                    f"ui.fields: field '{label}' not found in confirmation card"
                )
            else:
                actual_val = str(found.get("value", ""))
                if actual_val != str(expected_val):
                    warnings.append(
                        f"ui.fields[{label!r}]: expected '{expected_val}', got '{actual_val}'"
                    )

    # ── Soft: top-level ui key/value checks (e.g. existing_handover.srId) ──────
    if spec.expect_ui_contains:
        ui = body.get("ui") or {}
        for key, expected_val in spec.expect_ui_contains.items():
            actual_val = ui.get(key)
            if actual_val is None:
                warnings.append(f"ui.{key}: missing (expected '{expected_val}')")
            elif str(actual_val) != str(expected_val):
                warnings.append(
                    f"ui.{key}: expected '{expected_val}', got '{actual_val}'"
                )

    passed = len(failures) == 0
    return passed, failures, warnings, sr_reference


# ── Scenario runner ───────────────────────────────────────────────────────────


async def run_scenario(
    scenario: Scenario,
    base_url: str,
    verbose: bool = False,
) -> ScenarioResult:
    result = ScenarioResult(scenario=scenario)
    session_id: Optional[str] = None

    async with httpx.AsyncClient() as client:
        # Authenticate as MALL_MANAGER so the supervisor RBAC check permits
        # CREATE_RDD_SERVICE_REQUEST.  Without a token, anonymous callers only
        # receive read-only intents (ASK_HELP / CHECK_STATUS / PREVIEW) and
        # every packed CREATE_SR message returns a plain denial ``message``.
        token, user_id = await _authenticate(client, base_url)

        for idx, spec in enumerate(scenario.turns, start=1):
            # ── Send turn ─────────────────────────────────────────────────────
            try:
                body, latency_ms = await _post_turn(
                    client, base_url, spec, session_id, token=token, user_id=user_id
                )
            except httpx.HTTPStatusError as exc:
                result.turn_results.append(TurnResult(
                    turn_idx=idx,
                    message=spec.message,
                    note=spec.note,
                    passed=False,
                    http_error=f"HTTP {exc.response.status_code}: {exc.response.text[:300]}",
                ))
                break
            except Exception as exc:
                result.turn_results.append(TurnResult(
                    turn_idx=idx,
                    message=spec.message,
                    note=spec.note,
                    passed=False,
                    http_error=f"Request error: {type(exc).__name__}: {exc}",
                ))
                break

            # Carry session forward
            session_id = body.get("session_id") or session_id

            # ── Assert ────────────────────────────────────────────────────────
            passed, failures, warnings, sr_ref = _assert_turn(spec, body)

            ui_type: str = (body.get("ui") or {}).get("type", "") or ""
            stage: str = (body.get("state") or {}).get("workflow_stage", "") or ""
            msg: str = body.get("message", "") or ""

            tr = TurnResult(
                turn_idx=idx,
                message=spec.message,
                note=spec.note,
                passed=passed,
                failures=failures,
                warnings=warnings,
                response_message=msg,
                ui_type=ui_type,
                workflow_stage=stage,
                sr_reference=sr_ref,
                latency_ms=latency_ms,
            )
            result.turn_results.append(tr)

            if verbose:
                _print_turn_verbose(tr, spec)

            # Stop scenario on hard failure (soft warnings continue)
            if not passed:
                break

    result.session_id = session_id
    return result


_LATENCY_WARN_MS = 15_000  # warn when a single turn exceeds 15 seconds


def _print_turn_verbose(tr: TurnResult, spec: Turn) -> None:
    icon = green("✓") if tr.passed else red("✗")
    latency_str = (
        yellow(f"{tr.latency_ms}ms ⚠ slow")
        if tr.latency_ms > _LATENCY_WARN_MS
        else dim(f"{tr.latency_ms}ms")
    )
    meta = latency_str
    if tr.sr_reference:
        meta += f"  {dim('ref=' + tr.sr_reference)}"
    print(f"      {icon} Turn {tr.turn_idx}: {dim(spec.note)}  {meta}")

    if tr.http_error:
        print(f"          {red('ERROR')} {tr.http_error}")
    for f in tr.failures:
        print(f"          {red('FAIL')} {f}")
    for w in tr.warnings:
        print(f"          {yellow('WARN')} {w}")

    # Show bot reply (truncated)
    if tr.response_message:
        snippet = tr.response_message[:200].replace("\n", " ")
        extras = []
        if tr.ui_type:
            extras.append(f"ui={tr.ui_type!r}")
        if tr.workflow_stage:
            extras.append(f"stage={tr.workflow_stage!r}")
        extra_str = "  " + dim("  ".join(extras)) if extras else ""
        print(f"          {dim('→')} {dim(snippet)}{extra_str}")


# ── Server health check ───────────────────────────────────────────────────────


async def _server_ok(base_url: str) -> bool:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{base_url.rstrip('/')}/api/v1/health", timeout=5.0
            )
            return r.status_code == 200
    except Exception:
        return False


# ── Terminal report ───────────────────────────────────────────────────────────

_W = 72
_DIV = "─" * _W
_HDR = "═" * _W


def _print_report(results: list[ScenarioResult], elapsed: float) -> None:
    print(f"\n{bold(_HDR)}")
    print(bold("  CHATBOT EVALUATION REPORT — Handover Service Request"))
    print(f"{bold(_HDR)}\n")

    for res in results:
        sc = res.scenario
        scen_status = green("PASSED") if res.passed else red("FAILED")
        print(f"  {bold(f'Scenario {sc.id}')}  {sc.name}  [{scen_status}]")
        print(f"  {dim(sc.goal)}")

        for tr in res.turn_results:
            icon = green("✓") if tr.passed else red("✗")

            extras: list[str] = []
            if tr.ui_type:
                extras.append(f"ui={tr.ui_type}")
            if tr.workflow_stage:
                extras.append(f"stage={tr.workflow_stage}")
            if tr.sr_reference:
                extras.append(f"ref={tr.sr_reference}")
            extra_str = f"  {dim('(' + '  '.join(extras) + ')')}" if extras else ""

            if tr.http_error:
                print(f"    {icon} Turn {tr.turn_idx}: {dim(tr.note)}")
                print(f"        {red('ERROR')} {tr.http_error}")
            else:
                latency_tag = (
                    yellow(f"({tr.latency_ms}ms ⚠ slow)")
                    if tr.latency_ms > _LATENCY_WARN_MS
                    else dim(f"({tr.latency_ms}ms)")
                )
                print(
                    f"    {icon} Turn {tr.turn_idx}: "
                    f"{dim(tr.note)}  "
                    f"{latency_tag}{extra_str}"
                )
                for f in tr.failures:
                    print(f"        {red('FAIL')} {f}")
                for w in tr.warnings:
                    print(f"        {yellow('WARN')} {w}")

        turns_label = f"{res.turns_passed}/{res.turns_expected} turns passed"
        if res.turns_total < res.turns_expected:
            turns_label += f"  {yellow(f'(stopped at turn {res.turns_total} due to failure)')}"
        print(f"  {dim(turns_label)}\n")

    # ── Summary ───────────────────────────────────────────────────────────────
    passed_scenarios = sum(1 for r in results if r.passed)
    total_scenarios = len(results)
    total_turns_passed = sum(r.turns_passed for r in results)
    total_turns_expected = sum(r.turns_expected for r in results)

    all_ok = passed_scenarios == total_scenarios
    summary_colour = green if all_ok else red

    print(bold(_HDR))
    print(
        f"  {bold('SUMMARY')}  "
        f"{summary_colour(f'{passed_scenarios}/{total_scenarios} scenarios passed')}  "
        f"({total_turns_passed}/{total_turns_expected} turns)  "
        f"{dim(f'{elapsed:.1f}s total')}"
    )

    if not all_ok:
        print()
        for res in results:
            if not res.passed:
                first_fail = next(
                    (t for t in res.turn_results if not t.passed), None
                )
                if first_fail:
                    desc = (
                        first_fail.http_error
                        or (first_fail.failures[0] if first_fail.failures else "unknown")
                    )
                    print(
                        f"  {red('✗')} Scenario {res.scenario.id} ({res.scenario.name}) "
                        f"— Turn {first_fail.turn_idx}: {desc}"
                    )

    # ── Slow-turn summary ─────────────────────────────────────────────────────
    slow_turns = [
        (res.scenario.id, res.scenario.name, tr)
        for res in results
        for tr in res.turn_results
        if tr.latency_ms > _LATENCY_WARN_MS
    ]
    if slow_turns:
        print()
        print(yellow(f"  ⚠ SLOW TURNS (>{_LATENCY_WARN_MS // 1000}s threshold):"))
        for sc_id, sc_name, tr in slow_turns:
            print(
                f"    S{sc_id} Turn {tr.turn_idx} — {dim(tr.note)}: "
                f"{yellow(f'{tr.latency_ms}ms')}"
            )

    print(bold(_HDR) + "\n")


# ── JSON serialiser ───────────────────────────────────────────────────────────


def _to_json(results: list[ScenarioResult]) -> list[dict[str, Any]]:
    out = []
    for res in results:
        sc = res.scenario
        turns_out = []
        for tr in res.turn_results:
            turns_out.append({
                "turn": tr.turn_idx,
                "message": tr.message[:120],
                "note": tr.note,
                "passed": tr.passed,
                "failures": tr.failures,
                "warnings": tr.warnings,
                "ui_type": tr.ui_type,
                "workflow_stage": tr.workflow_stage,
                "sr_reference": tr.sr_reference,
                "latency_ms": tr.latency_ms,
                "http_error": tr.http_error,
            })
        out.append({
            "scenario_id": sc.id,
            "scenario_name": sc.name,
            "scenario_tags": sc.tags,
            "goal": sc.goal,
            "passed": res.passed,
            "session_id": res.session_id,
            "turns_passed": res.turns_passed,
            "turns_total": res.turns_total,
            "turns_expected": res.turns_expected,
            "turns": turns_out,
        })
    return out


# ── Main entry point ──────────────────────────────────────────────────────────


async def _main(
    base_url: str,
    scenario_ids: Optional[list[int]],
    tags: Optional[list[str]],
    verbose: bool,
    json_out: Optional[Path] = None,
    tier: Optional[int] = None,
) -> int:
    # Health check
    print(bold("Checking server..."), end=" ", flush=True)
    if not await _server_ok(base_url):
        print(red("✗ unreachable"))
        print(f"  Start the backend: {cyan('uvicorn app.main:app --reload')}")
        print(f"  Expected URL: {cyan(base_url)}\n")
        return 1
    print(green("✓ OK"))

    # Filter scenarios — tier takes precedence, then explicit IDs / tags
    if tier is not None:
        if tier not in _TIER_MAP:
            print(yellow(f"Unknown tier {tier}. Valid: 0, 1, 2, 3"))
            return 1
        scenarios = [s for s in _TIER_MAP[tier] if s.enabled]
        print(bold(f"Tier {tier}: {len(scenarios)} scenario(s) selected"))
    else:
        scenarios = [s for s in SCENARIOS if s.enabled]
    if scenario_ids:
        scenarios = [s for s in scenarios if s.id in scenario_ids]
    if tags:
        scenarios = [s for s in scenarios if any(t in s.tags for t in tags)]

    if not scenarios:
        print(yellow("No scenarios match the given filters."))
        return 1

    total_turns = sum(len(s.turns) for s in scenarios)
    print(
        bold(
            f"Running {len(scenarios)} scenario(s), "
            f"{total_turns} turns against {base_url}...\n"
        )
    )

    t0 = time.monotonic()
    results: list[ScenarioResult] = []

    for scenario in scenarios:
        label = f"Scenario {scenario.id}: {scenario.name}"
        print(f"{cyan('▶')} {bold(label)}")

        if verbose:
            print(f"  {dim(scenario.goal)}")

        result = await run_scenario(scenario, base_url, verbose=verbose)
        results.append(result)

        if result.passed:
            print(f"  {green('PASSED')} ({result.turns_passed}/{result.turns_expected} turns)\n")
        else:
            failed_at = next(
                (t for t in result.turn_results if not t.passed), None
            )
            stop_note = (
                f" — stopped at turn {failed_at.turn_idx}"
                if failed_at
                else ""
            )
            print(
                f"  {red('FAILED')} "
                f"({result.turns_passed}/{result.turns_expected} turns{stop_note})\n"
            )

    elapsed = time.monotonic() - t0
    _print_report(results, elapsed)

    failed = sum(1 for r in results if not r.passed)

    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(_json.dumps(_to_json(results), indent=2))
        print(f"  JSON report → {json_out}")

    return 0 if failed == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Chatbot evaluation suite — drives e2e scenarios against the live server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        metavar="URL",
        help="Backend base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--scenarios",
        default=None,
        metavar="IDS",
        help="Comma-separated scenario IDs to run, e.g. --scenarios 1,2,6",
    )
    parser.add_argument(
        "--tags",
        default=None,
        metavar="TAGS",
        help="Comma-separated tags to filter scenarios, e.g. --tags happy-path,core",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed turn-by-turn output during execution",
    )
    parser.add_argument(
        "--json",
        dest="json_out",
        default=None,
        type=Path,
        metavar="FILE",
        help="Write structured JSON results to FILE, e.g. results/tier_1.json",
    )
    parser.add_argument(
        "--tier",
        dest="tier",
        default=None,
        type=int,
        choices=[0, 1, 2, 3],
        metavar="N",
        help=(
            "Run only scenarios in tier N: "
            "0=smoke, 1=core CREATE_SR, 2=edge cases, 3=battle tests. "
            "Overrides --scenarios; can still combine with --tags."
        ),
    )
    args = parser.parse_args()

    scenario_ids: Optional[list[int]] = None
    if args.scenarios:
        try:
            scenario_ids = [int(x.strip()) for x in args.scenarios.split(",")]
        except ValueError:
            parser.error("--scenarios must be comma-separated integers, e.g. 1,2,3")

    tags: Optional[list[str]] = None
    if args.tags:
        tags = [t.strip() for t in args.tags.split(",")]

    sys.exit(asyncio.run(_main(args.base_url, scenario_ids, tags, args.verbose, args.json_out, args.tier)))


if __name__ == "__main__":
    main()
