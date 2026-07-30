#!/usr/bin/env python3
"""run_lifecycle_eval.py — Multi-phase lifecycle evaluation runner.

Drives the FM_REVIEW and RDD_REVIEW lifecycle scenarios (S34–S47) against the
live backend.  Unlike ``run_eval.py`` which only runs single-phase CREATE_SR
scenarios, this runner:

  1. Authenticates as a different seeded user for each phase.
  2. Passes the SR ID between phases (captured from the CREATE_SR submit response).
  3. Uploads a minimal mock document before FM / RDD phases (satisfies the
     document_count validation that blocks save/approve without a document).
  4. Supports the full action vocabulary: approve_fm_review, confirm_fm_approve,
     submit_rdd_report, confirm_rdd_submit, approve_rdd_final,
     confirm_rdd_final_approve.

Usage
-----
From the backend/ directory:

    # Run all lifecycle scenarios (S34–S47)
    python -m tests.eval.run_lifecycle_eval

    # Run specific scenarios
    python -m tests.eval.run_lifecycle_eval --scenarios 34,40,46

    # Filter by tag
    python -m tests.eval.run_lifecycle_eval --tags full-lifecycle

    # Verbose turn-by-turn output
    python -m tests.eval.run_lifecycle_eval --verbose

    # Write results to JSON
    python -m tests.eval.run_lifecycle_eval --json results/lifecycle_eval.json

Exit codes:
  0 — all scenarios passed
  1 — one or more scenarios failed, or server unreachable
"""

from __future__ import annotations

import argparse
import asyncio
import io
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx

from tests.eval.lifecycle_scenarios import (
    LIFECYCLE_SCENARIOS,
    TIER0_LIFECYCLE_SCENARIOS,
    TIER4_SCENARIOS,
    TIER5_SCENARIOS,
    TIER6_SCENARIOS,
    LifecycleScenario,
    PhaseConfig,
)
from tests.eval.scenarios import Turn

_TIER_MAP: dict[int, list[LifecycleScenario]] = {
    0: TIER0_LIFECYCLE_SCENARIOS,
    4: TIER4_SCENARIOS,
    5: TIER5_SCENARIOS,
    6: TIER6_SCENARIOS,
}

# ─── ANSI colours ─────────────────────────────────────────────────────────────

_NO_COLOUR = not sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return text if _NO_COLOUR else f"\033[{code}m{text}\033[0m"


def green(s: str)  -> str: return _c("92", s)
def red(s: str)    -> str: return _c("91", s)
def yellow(s: str) -> str: return _c("93", s)
def cyan(s: str)   -> str: return _c("96", s)
def bold(s: str)   -> str: return _c("1",  s)
def dim(s: str)    -> str: return _c("2",  s)


# ─── Constants ────────────────────────────────────────────────────────────────

_ENDPOINT       = "/api/chat/service-request"
_AUTH_ENDPOINT  = "/api/auth/login"
_UPLOAD_ENDPOINT = "/api/v1/upload"
_HEALTH_ENDPOINT = "/api/v1/health"
_RESET_ENDPOINT  = "/dev/mock/reset"
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_LATENCY_WARN_MS = 15_000


# ─── Result types ─────────────────────────────────────────────────────────────

@dataclass
class TurnResult:
    turn_idx: int
    phase_role: str
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
class PhaseResult:
    phase_idx: int
    role: str
    stage: str
    turn_results: list[TurnResult] = field(default_factory=list)
    session_id: Optional[str] = None

    @property
    def passed(self) -> bool:
        return all(t.passed for t in self.turn_results)


@dataclass
class ScenarioResult:
    scenario: LifecycleScenario
    phase_results: list[PhaseResult] = field(default_factory=list)
    captured_sr_id: Optional[str] = None

    @property
    def passed(self) -> bool:
        return bool(self.phase_results) and all(p.passed for p in self.phase_results)

    @property
    def turns_passed(self) -> int:
        return sum(t.passed for p in self.phase_results for t in p.turn_results)

    @property
    def turns_total(self) -> int:
        return sum(len(p.turn_results) for p in self.phase_results)

    @property
    def turns_expected(self) -> int:
        return sum(len(p.turns) for p in self.scenario.phases)


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

async def _authenticate(
    client: httpx.AsyncClient, base_url: str, email: str, password: str,
) -> tuple[Optional[str], Optional[str]]:
    """Authenticate and return (access_token, user_id)."""
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
    turn: Turn,
    session_id: Optional[str],
    token: Optional[str],
    user_id: str,
    sr_id: Optional[str] = None,
) -> tuple[dict[str, Any], int]:
    """POST one chat turn; return (response_body, latency_ms)."""
    payload: dict[str, Any] = {
        "user_id": user_id,
        "message": turn.message,
        "attachments": [],
    }
    if session_id:
        payload["session_id"] = session_id
    if turn.action:
        payload["action"] = turn.action
    if turn.corrected_fields:
        payload["corrected_fields"] = turn.corrected_fields
    if turn.selected_lease_id:
        payload["selected_lease_id"] = turn.selected_lease_id
    if sr_id:
        payload["sr_id"] = sr_id

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


async def _upload_mock_document(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
    sr_id: str,
    document_type: str,
    token: Optional[str],
) -> bool:
    """Upload a minimal PDF to satisfy document_count validation.

    Returns True on success, False on failure (non-fatal — phase continues).
    """
    _MINIMAL_PDF = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj "
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n"
        b"0000000058 00000 n\n0000000115 00000 n\n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
    )
    try:
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        resp = await client.post(
            f"{base_url.rstrip('/')}{_UPLOAD_ENDPOINT}",
            data={
                "document_type": document_type,
                "session_id": session_id,
                "sr_id": sr_id,
            },
            files={"file": ("mock_document.pdf", io.BytesIO(_MINIMAL_PDF), "application/pdf")},
            headers=headers,
            timeout=30.0,
        )
        if resp.status_code == 200:
            print(dim(f"      ↑ Uploaded mock {document_type}"))
            return True
        else:
            print(yellow(f"      ⚠ Document upload returned {resp.status_code}: {resp.text[:200]}"))
            return False
    except Exception as exc:
        print(yellow(f"      ⚠ Document upload failed: {exc}"))
        return False


async def _reset_mock_state(client: httpx.AsyncClient, base_url: str) -> None:
    """Clear the in-memory mock SR state between scenarios."""
    try:
        await client.post(f"{base_url.rstrip('/')}{_RESET_ENDPOINT}", timeout=5.0)
    except Exception:
        pass  # In production mode this returns 404 — ignore


# ─── Assertion engine ─────────────────────────────────────────────────────────

_CONVERSATIONAL = frozenset({"message", "text_question"})


def _assert_turn(turn: Turn, body: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []

    ui  = (body.get("ui") or {})
    st  = body.get("state") or {}
    msg = body.get("message") or ""
    ui_type        = ui.get("type", "") or ""
    workflow_stage = st.get("workflow_stage", "") or ""

    # Hard: UI type
    if turn.expect_ui_type is not None:
        exp = turn.expect_ui_type
        if exp == "message":
            if ui_type not in _CONVERSATIONAL:
                failures.append(f"ui.type: expected conversational, got '{ui_type}'")
        elif ui_type != exp:
            failures.append(f"ui.type: expected '{exp}', got '{ui_type}'")

    # Hard: workflow stage
    if turn.expect_workflow_stage is not None:
        if workflow_stage != turn.expect_workflow_stage:
            failures.append(
                f"workflow_stage: expected '{turn.expect_workflow_stage}', got '{workflow_stage}'"
            )

    # Hard: no-submit guard
    if turn.expect_no_submit and st.get("ready_to_submit"):
        failures.append("ready_to_submit: expected False but got True")

    # Soft: keyword check
    if turn.expect_keywords:
        msg_lower = msg.lower()
        if not any(kw.lower() in msg_lower for kw in turn.expect_keywords):
            warnings.append(
                f"Keywords {turn.expect_keywords!r} not found. Got: \"{msg[:160]}\""
            )

    # Soft: forbidden keywords
    if turn.expect_no_keywords:
        ui_msg = str(ui.get("message") or "")
        haystack = f"{msg}\n{ui_msg}".lower()
        for kw in turn.expect_no_keywords:
            if kw.lower() in haystack:
                warnings.append(
                    f"Forbidden keyword {kw!r} found in message/ui.message"
                )

    # Soft: top-level ui key checks
    if turn.expect_ui_contains:
        for key, expected_val in turn.expect_ui_contains.items():
            actual_val = ui.get(key)
            if actual_val is None:
                warnings.append(f"ui.{key}: missing (expected '{expected_val}')")
            elif str(actual_val) != str(expected_val):
                warnings.append(
                    f"ui.{key}: expected '{expected_val}', got '{actual_val}'"
                )

    return len(failures) == 0, failures, warnings


# ─── Phase runner ─────────────────────────────────────────────────────────────

async def run_phase(
    phase: PhaseConfig,
    phase_idx: int,
    base_url: str,
    captured_sr_id: Optional[str],
    verbose: bool = False,
) -> tuple[PhaseResult, Optional[str], Optional[str]]:
    """Run one phase and return (PhaseResult, new_sr_id, session_id)."""
    result = PhaseResult(phase_idx=phase_idx, role=phase.role, stage=phase.stage)

    # Authenticate
    token, user_id = await _authenticate(
        httpx.AsyncClient(), base_url, phase.user_email, phase.user_password,
    )
    if not user_id:
        user_id = f"eval_{phase.role.lower()}"

    session_id: Optional[str] = None
    sr_id = captured_sr_id  # may be None for phase 1
    # Track sr_id captured *within this phase* separately from the inherited
    # captured_sr_id.  For phases that start a new SR (open_existing_sr=False)
    # we must NOT pass the inherited sr_id to the first turn or to subsequent
    # turns until this phase itself creates an SR — otherwise a stale id from a
    # completed earlier phase forces the server into FM/RDD routing instead of
    # letting the user raise a fresh request.
    phase_own_sr_id: Optional[str] = None

    async with httpx.AsyncClient() as client:
        # Upload mock document if required (need session_id + sr_id, so do after first turn)
        doc_uploaded = False

        for idx, turn in enumerate(phase.turns, start=1):
            # Decide what sr_id to pass
            turn_sr_id: Optional[str] = None
            if phase.open_existing_sr and idx == 1:
                # First turn of FM/RDD phase — pass the captured SR ID to open it
                turn_sr_id = sr_id
            elif phase_own_sr_id:
                # This phase has already received its own SR ID — use it for
                # subsequent turns so the session stays anchored to the new SR.
                turn_sr_id = phase_own_sr_id
            elif phase.open_existing_sr and sr_id:
                # Subsequent turns of an open-existing phase — keep the same SR.
                turn_sr_id = sr_id
            elif sr_id and idx == 1 and turn.action:
                # T1 with an explicit action (e.g. cancel_sr, confirm_cancel_sr)
                # operates on the existing SR and needs the inherited sr_id.
                # T1 WITHOUT an action is a new-SR turn and must NOT receive the
                # inherited sr_id, or the server will route into FM/RDD instead of
                # letting the user raise a fresh request.
                turn_sr_id = sr_id

            # Upload document(s) after the first turn (session_id is now known).
            # document_types enables multi-file uploads (doc_upload_fix / L173).
            if phase.upload_mock_document and not doc_uploaded and session_id and sr_id:
                types_to_upload = list(phase.document_types) or [phase.document_type]
                for doc_type in types_to_upload:
                    await _upload_mock_document(
                        client, base_url, session_id, sr_id,
                        doc_type, token,
                    )
                doc_uploaded = True

            # Post the turn
            try:
                body, latency_ms = await _post_turn(
                    client, base_url, turn, session_id, token, user_id, sr_id=turn_sr_id,
                )
            except httpx.HTTPStatusError as exc:
                result.turn_results.append(TurnResult(
                    turn_idx=idx, phase_role=phase.role,
                    message=turn.message, note=turn.note,
                    passed=False,
                    http_error=f"HTTP {exc.response.status_code}: {exc.response.text[:300]}",
                ))
                return result, sr_id, session_id  # stop phase on HTTP error
            except Exception as exc:
                result.turn_results.append(TurnResult(
                    turn_idx=idx, phase_role=phase.role,
                    message=turn.message, note=turn.note,
                    passed=False,
                    http_error=f"Error: {type(exc).__name__}: {exc}",
                ))
                return result, sr_id, session_id

            # Update session + sr_id from response
            session_id = body.get("session_id") or session_id
            state = body.get("state") or {}
            new_sr = state.get("sr_id") or body.get("sr_id")
            if new_sr:
                sr_id = str(new_sr)
                phase_own_sr_id = str(new_sr)
            elif not sr_id:
                # Try to extract UUID from the message (CREATE_SR submit response)
                uuids = _UUID_RE.findall(body.get("message", ""))
                if uuids:
                    sr_id = uuids[0]
                    phase_own_sr_id = uuids[0]

            # Assert
            passed, failures, warnings = _assert_turn(turn, body)
            ui_type        = (body.get("ui") or {}).get("type", "") or ""
            workflow_stage = (body.get("state") or {}).get("workflow_stage", "") or ""
            msg            = body.get("message", "") or ""

            tr = TurnResult(
                turn_idx=idx, phase_role=phase.role,
                message=turn.message, note=turn.note,
                passed=passed, failures=failures, warnings=warnings,
                response_message=msg, ui_type=ui_type,
                workflow_stage=workflow_stage, latency_ms=latency_ms,
            )
            result.turn_results.append(tr)

            if verbose:
                _print_turn_verbose(tr, phase.role)

            if not passed:
                break  # stop phase on hard failure

    result.session_id = session_id
    return result, sr_id, session_id


# ─── Scenario runner ──────────────────────────────────────────────────────────

async def run_scenario(
    scenario: LifecycleScenario,
    base_url: str,
    verbose: bool = False,
    sr_id_override: Optional[str] = None,
    allowed_stages: Optional[set[str]] = None,
) -> ScenarioResult:
    result = ScenarioResult(scenario=scenario)
    # Start with injected sr_id (skips CREATE_SR setup if provided)
    captured_sr_id: Optional[str] = sr_id_override

    # Reset mock SR state between scenarios (no-op in production mode)
    async with httpx.AsyncClient() as hc:
        await _reset_mock_state(hc, base_url)

    for phase_idx, phase in enumerate(scenario.phases, start=1):
        # --phase filter: skip phases not in allowed_stages
        if allowed_stages is not None and phase.stage not in allowed_stages:
            if verbose:
                print(f"    {dim(f'Phase {phase_idx}: {phase.role} ({phase.stage}) — skipped by --phase filter')}")
            # If this was a CREATE_SR setup phase being skipped, keep the sr_id_override
            # so subsequent FM/RDD phases still get a valid SR ID
            continue

        if verbose:
            print(f"    {dim(f'Phase {phase_idx}: {phase.role} ({phase.stage})')}")

        phase_result, captured_sr_id, _ = await run_phase(
            phase, phase_idx, base_url, captured_sr_id, verbose=verbose,
        )
        result.phase_results.append(phase_result)
        result.captured_sr_id = captured_sr_id
        # session_id is already stored on phase_result by run_phase

        if not phase_result.passed:
            break  # stop scenario on phase failure

    return result


# ─── Terminal output ──────────────────────────────────────────────────────────

def _print_turn_verbose(tr: TurnResult, role: str) -> None:
    icon = green("✓") if tr.passed else red("✗")
    lat  = yellow(f"{tr.latency_ms}ms ⚠") if tr.latency_ms > _LATENCY_WARN_MS else dim(f"{tr.latency_ms}ms")
    stage_str = dim(f"stage={tr.workflow_stage}") if tr.workflow_stage else ""
    print(f"      {icon} T{tr.turn_idx} [{dim(role)}]  {dim(tr.note)}  {lat}  {stage_str}")
    if tr.http_error:
        print(f"          {red('ERROR')} {tr.http_error}")
    for f in tr.failures:
        print(f"          {red('FAIL')}  {f}")
    for w in tr.warnings:
        print(f"          {yellow('WARN')}  {w}")
    if tr.response_message:
        snippet = tr.response_message[:160].replace("\n", " ")
        print(f"          {dim('→')} {dim(snippet)}")


_W = 72
_HDR = "═" * _W
_DIV = "─" * _W


def _print_report(results: list[ScenarioResult], elapsed: float) -> None:
    print(f"\n{bold(_HDR)}")
    print(bold("  LIFECYCLE EVALUATION REPORT"))
    print(f"{bold(_HDR)}\n")

    for res in results:
        sc    = res.scenario
        icon  = green("PASSED") if res.passed else red("FAILED")
        sr_id = res.captured_sr_id or "—"
        print(f"  {bold(f'S{sc.id}')}  {sc.name}  [{icon}]  SR={dim(sr_id[:8] + '...' if sr_id != '—' else '—')}")
        print(f"  {dim(sc.goal)}")

        for pr in res.phase_results:
            p_icon = green("✓") if pr.passed else red("✗")
            print(f"    {p_icon} Phase {pr.phase_idx}: {pr.role} ({pr.stage})")
            for tr in pr.turn_results:
                t_icon = green("✓") if tr.passed else red("✗")
                lat = yellow(f"({tr.latency_ms}ms ⚠)") if tr.latency_ms > _LATENCY_WARN_MS else dim(f"({tr.latency_ms}ms)")
                print(f"        {t_icon} T{tr.turn_idx}: {dim(tr.note)}  {lat}")
                if tr.http_error:
                    print(f"            {red('ERROR')} {tr.http_error}")
                for f in tr.failures:
                    print(f"            {red('FAIL')}  {f}")
                for w in tr.warnings:
                    print(f"            {yellow('WARN')}  {w}")

        turns_str = f"{res.turns_passed}/{res.turns_expected} turns"
        print(f"  {dim(turns_str)}\n")

    # Summary
    passed_count = sum(1 for r in results if r.passed)
    total_count  = len(results)
    all_ok = passed_count == total_count

    total_tp = sum(r.turns_passed  for r in results)
    total_te = sum(r.turns_expected for r in results)

    col = green if all_ok else red
    print(bold(_HDR))
    print(
        f"  {bold('SUMMARY')}  "
        f"{col(f'{passed_count}/{total_count} scenarios passed')}  "
        f"({total_tp}/{total_te} turns)  "
        f"{dim(f'{elapsed:.1f}s total')}"
    )

    if not all_ok:
        print()
        for res in results:
            if not res.passed:
                for pr in res.phase_results:
                    first_fail = next((t for t in pr.turn_results if not t.passed), None)
                    if first_fail:
                        desc = first_fail.http_error or (
                            first_fail.failures[0] if first_fail.failures else "unknown"
                        )
                        print(
                            f"  {red('✗')} S{res.scenario.id} ({res.scenario.name}) "
                            f"— Phase {pr.phase_idx} T{first_fail.turn_idx}: {desc}"
                        )

    slow = [
        (res.scenario.id, res.scenario.name, pr.role, tr)
        for res in results
        for pr in res.phase_results
        for tr in pr.turn_results
        if tr.latency_ms > _LATENCY_WARN_MS
    ]
    if slow:
        print()
        print(yellow(f"  ⚠ SLOW TURNS (>{_LATENCY_WARN_MS // 1000}s):"))
        for sid, sname, role, tr in slow:
            print(
                f"    S{sid} T{tr.turn_idx} [{role}] — {dim(tr.note)}: "
                f"{yellow(f'{tr.latency_ms}ms')}"
            )

    print(bold(_HDR) + "\n")


# ─── JSON output ──────────────────────────────────────────────────────────────

def _to_json(results: list[ScenarioResult]) -> list[dict[str, Any]]:
    import json
    out = []
    for res in results:
        sc = res.scenario
        phases = []
        for pr in res.phase_results:
            turns_out = []
            for tr in pr.turn_results:
                turns_out.append({
                    "turn": tr.turn_idx,
                    "role": tr.phase_role,
                    "message": tr.message[:120],
                    "note": tr.note,
                    "passed": tr.passed,
                    "failures": tr.failures,
                    "warnings": tr.warnings,
                    "ui_type": tr.ui_type,
                    "workflow_stage": tr.workflow_stage,
                    "latency_ms": tr.latency_ms,
                    "http_error": tr.http_error,
                })
            phases.append({
                "phase_idx": pr.phase_idx,
                "role": pr.role,
                "stage": pr.stage,
                "passed": pr.passed,
                "session_id": pr.session_id,
                "turns": turns_out,
            })
        out.append({
            "scenario_id": sc.id,
            "scenario_name": sc.name,
            "scenario_tags": sc.tags,
            "passed": res.passed,
            "captured_sr_id": res.captured_sr_id,
            "turns_passed": res.turns_passed,
            "turns_expected": res.turns_expected,
            "session_ids": [pr.session_id for pr in res.phase_results if pr.session_id],
            "phases": phases,
        })
    return out


# ─── Health check ─────────────────────────────────────────────────────────────

async def _server_ok(base_url: str) -> bool:
    try:
        async with httpx.AsyncClient() as hc:
            r = await hc.get(f"{base_url.rstrip('/')}{_HEALTH_ENDPOINT}", timeout=5.0)
            return r.status_code == 200
    except Exception:
        return False


# ─── Main ─────────────────────────────────────────────────────────────────────

async def _main(
    base_url: str,
    scenario_ids: Optional[list[int]],
    tags: Optional[list[str]],
    verbose: bool,
    json_out: Optional[Path],
    tier: Optional[int] = None,
    sr_id_override: Optional[str] = None,
    phase_filter: Optional[str] = None,
) -> int:
    print(bold("Checking server…"), end=" ", flush=True)
    if not await _server_ok(base_url):
        print(red("✗ unreachable"))
        print(f"  Start: {cyan('uvicorn app.main:app --reload')}")
        return 1
    print(green("✓ OK"))

    # Select scenario pool
    if tier is not None:
        if tier not in _TIER_MAP:
            print(yellow(f"Unknown lifecycle tier {tier}. Valid: 0, 4, 5, 6"))
            return 1
        scenarios = [s for s in _TIER_MAP[tier] if s.enabled]
        print(bold(f"Lifecycle tier {tier}: {len(scenarios)} scenario(s)"))
    else:
        scenarios = [s for s in LIFECYCLE_SCENARIOS if s.enabled]
    if scenario_ids:
        scenarios = [s for s in scenarios if s.id in scenario_ids]
    if tags:
        scenarios = [s for s in scenarios if any(t in s.tags for t in tags)]

    # --sr-id: inject a pre-existing SR ID so the CREATE_SR setup phase is skipped
    # and the runner starts directly at the FM or RDD phase.
    if sr_id_override:
        print(yellow(f"  ⚡ --sr-id override: skipping CREATE_SR setup; using SR={sr_id_override[:8]}…"))

    # --phase: restrict which phase stages run
    _PHASE_STAGES: dict[str, set[str]] = {
        "fm":   {"FM_REVIEW"},
        "rdd":  {"RDD_REVIEW"},
        "full": {"CREATE_SR", "FM_REVIEW", "RDD_REVIEW"},
    }
    allowed_stages: Optional[set[str]] = None
    if phase_filter:
        if phase_filter not in _PHASE_STAGES:
            print(yellow(f"Unknown --phase value '{phase_filter}'. Valid: fm, rdd, full"))
            return 1
        allowed_stages = _PHASE_STAGES[phase_filter]
        print(bold(f"Phase filter: running only stages {allowed_stages}"))

    if not scenarios:
        print(yellow("No matching scenarios."))
        return 1

    total_turns = sum(
        len(p.turns) for s in scenarios for p in s.phases
    )
    total_phases = sum(len(s.phases) for s in scenarios)
    print(bold(
        f"Running {len(scenarios)} lifecycle scenario(s), "
        f"{total_phases} phases, {total_turns} turns against {base_url}\n"
    ))

    t0 = time.monotonic()
    results: list[ScenarioResult] = []

    for scenario in scenarios:
        phases_str = " → ".join(p.stage for p in scenario.phases)
        print(f"{cyan('▶')} {bold(f'S{scenario.id}')} {scenario.name}  {dim(f'[{phases_str}]')}")
        if verbose:
            print(f"  {dim(scenario.goal)}")

        result = await run_scenario(
            scenario, base_url, verbose=verbose,
            sr_id_override=sr_id_override,
            allowed_stages=allowed_stages,
        )
        results.append(result)

        sr_display = f"SR={dim(result.captured_sr_id[:8] + '…')}" if result.captured_sr_id else ""
        if result.passed:
            print(f"  {green('PASSED')} ({result.turns_passed}/{result.turns_expected} turns) {sr_display}\n")
        else:
            first_fail_phase = next((p for p in result.phase_results if not p.passed), None)
            if first_fail_phase:
                ft = next((t for t in first_fail_phase.turn_results if not t.passed), None)
                desc = ft.http_error or (ft.failures[0] if ft.failures else "?") if ft else "?"
                print(
                    f"  {red('FAILED')} (stopped at Phase {first_fail_phase.phase_idx} "
                    f"T{ft.turn_idx if ft else '?'}: {desc})\n"
                )
            else:
                print(f"  {red('FAILED')}\n")

    elapsed = time.monotonic() - t0
    _print_report(results, elapsed)

    if json_out:
        import json as _json
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(_json.dumps(_to_json(results), indent=2))
        print(f"  JSON report → {json_out}")

    failed = sum(1 for r in results if not r.passed)
    return 0 if failed == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lifecycle eval runner — FM_REVIEW, RDD_REVIEW, full lifecycle.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", metavar="URL")
    parser.add_argument(
        "--scenarios", default=None, metavar="IDS",
        help="Comma-separated scenario IDs, e.g. 134,140,146 (note: IDs are 134–172 after rename)",
    )
    parser.add_argument("--tags", default=None, metavar="TAGS",
                        help="Comma-separated tags, e.g. full-lifecycle,rbac")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--json", dest="json_out", default=None, type=Path, metavar="FILE")
    parser.add_argument(
        "--tier", dest="tier", default=None, type=int,
        choices=[0, 4, 5, 6], metavar="N",
        help=(
            "Run only lifecycle tier N: "
            "0=smoke FM, 4=FM_REVIEW, 5=RDD_REVIEW, 6=full lifecycle+battle. "
            "Overrides --scenarios."
        ),
    )
    parser.add_argument(
        "--sr-id", dest="sr_id", default=None, metavar="UUID",
        help=(
            "Inject a pre-existing SR UUID to skip the CREATE_SR setup phase "
            "and test FM/RDD review in isolation. "
            "e.g. --sr-id 12345678-1234-1234-1234-123456789abc"
        ),
    )
    parser.add_argument(
        "--phase", dest="phase", default=None, choices=["fm", "rdd", "full"],
        help=(
            "Run only specific phase stages: "
            "fm=FM_REVIEW only, rdd=RDD_REVIEW only, full=all phases (default)."
        ),
    )
    args = parser.parse_args()

    ids: Optional[list[int]] = None
    if args.scenarios:
        try:
            ids = [int(x.strip()) for x in args.scenarios.split(",")]
        except ValueError:
            parser.error("--scenarios must be comma-separated integers")

    tags: Optional[list[str]] = None
    if args.tags:
        tags = [t.strip() for t in args.tags.split(",")]

    sys.exit(asyncio.run(_main(
        args.base_url, ids, tags, args.verbose, args.json_out,
        tier=args.tier,
        sr_id_override=args.sr_id,
        phase_filter=args.phase,
    )))


if __name__ == "__main__":
    main()
