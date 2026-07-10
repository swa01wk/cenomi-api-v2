#!/usr/bin/env python3
"""export_session_observability.py — Session state-transition exporter.

Fetches the full observability replay for one or more chat sessions,
then writes a structured export with:
  - Turn-by-turn state transitions  (workflow_stage before → after)
  - Collected-data evolution         (which fields appeared each turn)
  - Node execution trace             (ordered list of nodes run per turn)
  - LLM call summary                 (model, tokens, cost, latency)
  - Tool call records                (SR API calls, lease lookups, etc.)
  - SR lifecycle timeline            (CREATE → FM_REVIEW → RDD_REVIEW → COMPLETE)
  - Per-session eval quality scores  (via eval_session.py logic)

Output formats:
  - JSON  → sessions/<session_id>.json
  - NDJSON manifest → sessions/manifest.ndjson  (one line per session)
  - Markdown summary → sessions/<session_id>.md

Usage:
  # Export specific session IDs
  python scripts/export_session_observability.py \\
    --session-id abc123 def456

  # Auto-discover the N most recent sessions
  python scripts/export_session_observability.py --recent 5

  # Read session IDs from a file (one UUID per line, or a JSON array)
  python scripts/export_session_observability.py \\
    --session-file /path/to/session_ids.json

  # Export and also run eval quality scoring
  python scripts/export_session_observability.py --recent 3 --eval

  # Custom backend URL
  python scripts/export_session_observability.py \\
    --recent 10 --base-url http://staging.example.com

  # Output to a specific directory
  python scripts/export_session_observability.py --recent 5 --out /tmp/obs

Exit codes:
  0 — all sessions exported successfully
  1 — one or more sessions failed to export
  2 — backend unreachable
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

import httpx

# ─── ANSI colours ─────────────────────────────────────────────────────────────

_NO_COLOUR = not sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return text if _NO_COLOUR else f"\033[{code}m{text}\033[0m"


def green(s: str) -> str:   return _c("92", s)
def red(s: str) -> str:     return _c("91", s)
def yellow(s: str) -> str:  return _c("93", s)
def cyan(s: str) -> str:    return _c("96", s)
def bold(s: str) -> str:    return _c("1",  s)
def dim(s: str) -> str:     return _c("2",  s)


# ─── Data models ──────────────────────────────────────────────────────────────

@dataclass
class LLMCallSummary:
    run_name: str
    provider: Optional[str]
    model: Optional[str]
    prompt_name: Optional[str]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    total_tokens: Optional[int]
    latency_ms: Optional[int]
    cost_usd: Optional[float]


@dataclass
class ToolCallSummary:
    tool_name: str
    success: bool
    latency_ms: Optional[int]
    error_message: Optional[str]


@dataclass
class TurnTransition:
    turn: int
    trace_id: str
    user_input: str
    bot_response: str
    active_agent: Optional[str]
    intent: Optional[str]

    # State transitions
    stage_before: Optional[str]
    stage_after: Optional[str]
    sr_id: Optional[str]
    fm_status: Optional[str]
    rdd_status: Optional[str]

    # Field extraction — keys present in collected_data after this turn
    collected_data_snapshot: dict[str, Any]
    # Delta: keys that appeared for the first time this turn
    newly_extracted_fields: list[str]

    # Node execution timeline
    nodes_executed: list[str]    # ordered by started_at

    # LLM + tool telemetry
    llm_calls: list[LLMCallSummary]
    tool_calls: list[ToolCallSummary]

    # Timings
    turn_latency_ms: Optional[int]
    started_at: Optional[str]

    # Status
    trace_status: str
    error_message: Optional[str]


@dataclass
class SRLifecycle:
    """Key lifecycle timestamps extracted from the state transitions."""
    sr_id: Optional[str] = None
    created_at: Optional[str] = None          # turn when sr_id first appears
    fm_review_entered_at: Optional[str] = None
    fm_approved_at: Optional[str] = None
    rdd_review_entered_at: Optional[str] = None
    rdd_report_submitted_at: Optional[str] = None
    completed_at: Optional[str] = None
    total_turns: int = 0
    total_latency_ms: int = 0


@dataclass
class EvalScore:
    """Summary of quality checks from eval_session.py."""
    session_id: str
    trace_count: int
    passes: int = 0
    warnings: int = 0
    failures: int = 0
    infos: int = 0
    checks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SessionExport:
    """Complete export for one chat session."""
    export_metadata: dict[str, Any]
    session_id: str
    total_turns: int
    state_transitions: list[TurnTransition]
    sr_lifecycle: SRLifecycle
    eval_score: Optional[EvalScore]


# ─── API client ───────────────────────────────────────────────────────────────

def _get(client: httpx.Client, url: str) -> dict[str, Any]:
    resp = client.get(url, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def fetch_session_replay(
    client: httpx.Client, base_url: str, session_id: str,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/observability/sessions/{session_id}/replay"
    return _get(client, url)


def fetch_recent_sessions(
    client: httpx.Client, base_url: str, n: int,
) -> list[str]:
    """Return the most recent N distinct session IDs from the traces list."""
    url = f"{base_url.rstrip('/')}/api/observability/traces?page_size=200&page=1"
    data = _get(client, url)
    seen: list[str] = []
    seen_set: set[str] = set()
    for item in data.get("items", []):
        sid = str(item.get("session_id", ""))
        if sid and sid not in seen_set:
            seen.append(sid)
            seen_set.add(sid)
        if len(seen) >= n:
            break
    return seen


# ─── State transition extractor ───────────────────────────────────────────────

def _first_stage_from_snapshots(
    snapshots: list[dict[str, Any]], snap_type: str,
) -> Optional[str]:
    """Return workflow_stage from the first snapshot of the given type."""
    for s in snapshots:
        if s.get("snapshot_type") == snap_type:
            return s.get("state", {}).get("workflow_stage")
    return None


def _collect_data_from_snapshots(
    snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return collected_data from the last AFTER_NODE snapshot."""
    cd: dict[str, Any] = {}
    for s in snapshots:
        if s.get("snapshot_type") == "AFTER_NODE":
            cd = s.get("state", {}).get("collected_data") or cd
    return cd


def _sr_id_from_snapshots(snapshots: list[dict[str, Any]]) -> Optional[str]:
    for s in sorted(snapshots, key=lambda x: x.get("created_at", ""), reverse=True):
        state = s.get("state", {})
        sr_id = (
            state.get("sr_id")
            or (state.get("backend_refs") or {}).get("sr_id")
        )
        if sr_id:
            return str(sr_id)
    return None


def _status_from_snapshots(
    snapshots: list[dict[str, Any]], key: str,
) -> Optional[str]:
    for s in sorted(snapshots, key=lambda x: x.get("created_at", ""), reverse=True):
        val = s.get("state", {}).get(key)
        if val:
            return str(val)
    return None


def _nodes_from_runs(runs: list[dict[str, Any]]) -> list[str]:
    """Return node names in execution order (by started_at)."""
    ordered = sorted(
        [r for r in runs if r.get("node_name")],
        key=lambda r: r.get("started_at", ""),
    )
    seen: list[str] = []
    for r in ordered:
        nn = r["node_name"]
        if not seen or seen[-1] != nn:
            seen.append(nn)
    return seen


def build_turn_transition(
    turn_num: int,
    entry: dict[str, Any],
    prev_cd: dict[str, Any],
) -> TurnTransition:
    trace = entry.get("trace", {})
    runs = entry.get("runs", [])
    snapshots = entry.get("state_snapshots", [])
    llm_calls_raw = entry.get("llm_calls", [])
    tool_calls_raw = entry.get("tool_calls", [])

    # Stage transitions
    stage_before = (
        trace.get("workflow_stage_before")
        or _first_stage_from_snapshots(snapshots, "BEFORE_NODE")
    )
    stage_after = (
        trace.get("workflow_stage_after")
        or _first_stage_from_snapshots(snapshots, "AFTER_NODE")
    )

    # Collected data evolution
    current_cd = _collect_data_from_snapshots(snapshots)
    newly_extracted = [k for k in current_cd if k not in prev_cd and current_cd[k]]

    # LLM summaries
    llm_summaries = [
        LLMCallSummary(
            run_name=next(
                (r.get("run_name", "") for r in runs if r.get("id") == lc.get("run_id")),
                "",
            ),
            provider=lc.get("provider"),
            model=lc.get("model"),
            prompt_name=lc.get("prompt_name"),
            input_tokens=lc.get("input_tokens"),
            output_tokens=lc.get("output_tokens"),
            total_tokens=lc.get("total_tokens"),
            latency_ms=lc.get("latency_ms"),
            cost_usd=float(lc["cost_usd"]) if lc.get("cost_usd") is not None else None,
        )
        for lc in llm_calls_raw
    ]

    # Tool call summaries
    tool_summaries = [
        ToolCallSummary(
            tool_name=tc.get("tool_name", ""),
            success=bool(tc.get("success", False)),
            latency_ms=tc.get("latency_ms"),
            error_message=tc.get("error_message"),
        )
        for tc in tool_calls_raw
    ]

    return TurnTransition(
        turn=turn_num,
        trace_id=str(trace.get("id", "")),
        user_input=(trace.get("input_message") or "")[:500],
        bot_response=(trace.get("output_message") or "")[:500],
        active_agent=trace.get("active_agent"),
        intent=trace.get("intent"),
        stage_before=stage_before,
        stage_after=stage_after,
        sr_id=_sr_id_from_snapshots(snapshots),
        fm_status=_status_from_snapshots(snapshots, "fm_status"),
        rdd_status=_status_from_snapshots(snapshots, "rdd_status"),
        collected_data_snapshot=current_cd,
        newly_extracted_fields=newly_extracted,
        nodes_executed=_nodes_from_runs(runs),
        llm_calls=llm_summaries,
        tool_calls=tool_summaries,
        turn_latency_ms=trace.get("total_latency_ms"),
        started_at=trace.get("started_at"),
        trace_status=trace.get("status", "UNKNOWN"),
        error_message=trace.get("error_message"),
    )


def build_sr_lifecycle(transitions: list[TurnTransition]) -> SRLifecycle:
    lc = SRLifecycle(total_turns=len(transitions))
    prev_stage: Optional[str] = None

    for t in transitions:
        lc.total_latency_ms += t.turn_latency_ms or 0

        if t.sr_id and not lc.sr_id:
            lc.sr_id = t.sr_id
            lc.created_at = t.started_at

        stage = t.stage_after or t.stage_before
        if stage and stage != prev_stage:
            if stage == "FM_REVIEW" and not lc.fm_review_entered_at:
                lc.fm_review_entered_at = t.started_at
            elif stage == "RDD_REVIEW" and not lc.rdd_review_entered_at:
                lc.rdd_review_entered_at = t.started_at
            elif stage == "SR_COMPLETED" and not lc.completed_at:
                lc.completed_at = t.started_at
            prev_stage = stage

        if t.fm_status == "APPROVED" and not lc.fm_approved_at:
            lc.fm_approved_at = t.started_at
        if t.rdd_status == "REPORT_SUBMITTED" and not lc.rdd_report_submitted_at:
            lc.rdd_report_submitted_at = t.started_at

    return lc


# ─── Eval quality scorer (reuses eval_session.py logic) ──────────────────────

def _eval_session(
    client: httpx.Client, base_url: str, session_id: str,
) -> Optional[EvalScore]:
    """Call eval_session logic directly without subprocess."""
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _backend = _Path(__file__).parent.parent
        if str(_backend) not in _sys.path:
            _sys.path.insert(0, str(_backend))
        from tests.eval.eval_session import (
            evaluate_session,
            fetch_session_replay as _fetch_replay,
            to_json_report,
        )
        replay = _fetch_replay(session_id, base_url)
        se = evaluate_session(replay)
        report = to_json_report(se)
        summary = se.summary
        return EvalScore(
            session_id=session_id,
            trace_count=se.trace_count,
            passes=summary.get("PASS", 0),
            warnings=summary.get("WARN", 0),
            failures=summary.get("FAIL", 0),
            infos=summary.get("INFO", 0),
            checks=report.get("turns", []),
        )
    except Exception as exc:
        print(yellow(f"  Eval scoring unavailable: {exc}"))
        return None


# ─── Markdown formatter ───────────────────────────────────────────────────────

def _fmt_md(export: SessionExport) -> str:
    lines: list[str] = []
    meta = export.export_metadata
    lc = export.sr_lifecycle

    lines += [
        f"# Session Observability Export",
        f"",
        f"**Session ID:** `{export.session_id}`  ",
        f"**Exported:** {meta.get('exported_at', '')}  ",
        f"**Backend:** {meta.get('backend_url', '')}  ",
        f"**Total turns:** {export.total_turns}  ",
        f"**Total latency:** {lc.total_latency_ms:,} ms  ",
        f"",
        f"---",
        f"",
        f"## SR Lifecycle Timeline",
        f"",
        f"| Event | Timestamp |",
        f"|---|---|",
    ]

    for label, ts in [
        ("SR Created", lc.created_at),
        ("FM Review Entered", lc.fm_review_entered_at),
        ("FM Approved", lc.fm_approved_at),
        ("RDD Review Entered", lc.rdd_review_entered_at),
        ("RDD Report Submitted", lc.rdd_report_submitted_at),
        ("SR Completed", lc.completed_at),
    ]:
        if ts:
            lines.append(f"| {label} | `{ts}` |")

    if lc.sr_id:
        lines += [f"", f"**SR ID:** `{lc.sr_id}`"]

    lines += [f"", f"---", f"", f"## State Transitions — Turn by Turn", f""]

    for t in export.state_transitions:
        stage_arrow = (
            f"`{t.stage_before}` → `{t.stage_after}`"
            if t.stage_before != t.stage_after and t.stage_after
            else f"`{t.stage_after or t.stage_before or 'unknown'}`"
        )
        status_icon = "✓" if t.trace_status == "SUCCESS" else "✗"

        lines += [
            f"### Turn {t.turn} {status_icon}",
            f"",
            f"**Stage:** {stage_arrow}  ",
            f"**Intent:** `{t.intent or 'n/a'}`  ",
            f"**Latency:** {t.turn_latency_ms or 0:,} ms  ",
            f"",
            f"> **User:** {t.user_input[:200]}",
            f">",
            f"> **Bot:** {t.bot_response[:300]}",
            f"",
        ]

        if t.nodes_executed:
            lines.append(f"**Nodes:** {' → '.join(t.nodes_executed)}")

        if t.newly_extracted_fields:
            lines.append(f"**New fields:** `{'`, `'.join(t.newly_extracted_fields)}`")

        if t.llm_calls:
            total_tokens = sum(lc.total_tokens or 0 for lc in t.llm_calls)
            total_cost = sum(lc.cost_usd or 0 for lc in t.llm_calls)
            lines.append(
                f"**LLM:** {len(t.llm_calls)} call(s) — "
                f"{total_tokens:,} tokens — "
                f"${total_cost:.4f}"
            )

        if t.tool_calls:
            tool_names = ", ".join(
                f"`{tc.tool_name}`" + ("" if tc.success else " ✗")
                for tc in t.tool_calls
            )
            lines.append(f"**Tools:** {tool_names}")

        if t.error_message:
            lines += [f"", f"⚠️ **Error:** {t.error_message}"]

        lines.append("")

    # Eval scores
    if export.eval_score:
        ev = export.eval_score
        lines += [
            f"---",
            f"",
            f"## Eval Quality Scores",
            f"",
            f"| Metric | Count |",
            f"|---|---|",
            f"| PASS | {ev.passes} |",
            f"| INFO | {ev.infos} |",
            f"| WARN | {ev.warnings} |",
            f"| FAIL | {ev.failures} |",
            f"",
        ]

    return "\n".join(lines)


# ─── Main exporter ─────────────────────────────────────────────────────────────

def export_session(
    client: httpx.Client,
    base_url: str,
    session_id: str,
    out_dir: Path,
    run_eval: bool = False,
) -> Optional[SessionExport]:
    print(f"  {cyan('▶')} Exporting session {bold(session_id)}")

    try:
        replay = fetch_session_replay(client, base_url, session_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            print(f"    {yellow('⚠')} No traces found for session {session_id} — skipping")
            return None
        raise

    traces = replay.get("traces", [])
    if not traces:
        print(f"    {yellow('⚠')} Empty trace list — skipping")
        return None

    # Build transitions
    transitions: list[TurnTransition] = []
    prev_cd: dict[str, Any] = {}
    for i, entry in enumerate(traces, start=1):
        t = build_turn_transition(i, entry, prev_cd)
        transitions.append(t)
        prev_cd = t.collected_data_snapshot

    sr_lifecycle = build_sr_lifecycle(transitions)
    eval_score = _eval_session(client, base_url, session_id) if run_eval else None

    export = SessionExport(
        export_metadata={
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "backend_url": base_url,
            "script_version": "1.0.0",
        },
        session_id=session_id,
        total_turns=len(transitions),
        state_transitions=transitions,
        sr_lifecycle=sr_lifecycle,
        eval_score=eval_score,
    )

    # Write JSON
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{session_id}.json"
    with open(json_path, "w") as f:
        json.dump(asdict(export), f, indent=2, default=str)

    # Write Markdown
    md_path = out_dir / f"{session_id}.md"
    with open(md_path, "w") as f:
        f.write(_fmt_md(export))

    # Print summary
    lc = sr_lifecycle
    stage_str = ""
    if lc.completed_at:
        stage_str = "SR_COMPLETED ✓"
    elif lc.rdd_report_submitted_at:
        stage_str = "RDD_REVIEW (report submitted)"
    elif lc.rdd_review_entered_at:
        stage_str = "RDD_REVIEW"
    elif lc.fm_approved_at:
        stage_str = "FM approved"
    elif lc.fm_review_entered_at:
        stage_str = "FM_REVIEW"
    elif lc.created_at:
        stage_str = "CREATE_SR"

    eval_str = ""
    if eval_score:
        ev = eval_score
        col = green if ev.failures == 0 else red
        eval_str = f"  eval: {col(f'PASS={ev.passes} WARN={ev.warnings} FAIL={ev.failures}')}"

    print(
        f"    {green('✓')} {len(transitions)} turns  "
        f"stage={bold(stage_str)}  "
        f"{lc.total_latency_ms:,}ms total"
        f"{eval_str}"
    )
    print(f"    JSON → {json_path}")
    print(f"    MD   → {md_path}")

    return export


def write_manifest(
    exports: list[SessionExport], out_dir: Path,
) -> None:
    """Append each export's metadata to manifest.ndjson."""
    manifest_path = out_dir / "manifest.ndjson"
    with open(manifest_path, "a") as f:
        for exp in exports:
            lc = exp.sr_lifecycle
            record = {
                "session_id": exp.session_id,
                "exported_at": exp.export_metadata["exported_at"],
                "total_turns": exp.total_turns,
                "sr_id": lc.sr_id,
                "final_stage": (
                    "SR_COMPLETED" if lc.completed_at
                    else "RDD_REVIEW" if lc.rdd_review_entered_at
                    else "FM_REVIEW" if lc.fm_review_entered_at
                    else "CREATE_SR" if lc.created_at
                    else "UNKNOWN"
                ),
                "total_latency_ms": lc.total_latency_ms,
                "eval_passes": exp.eval_score.passes if exp.eval_score else None,
                "eval_failures": exp.eval_score.failures if exp.eval_score else None,
            }
            f.write(json.dumps(record, default=str) + "\n")
    print(f"\n  Manifest → {manifest_path}")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export session state-transitions and observability data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--session-id", nargs="+", metavar="UUID",
        help="One or more session IDs to export",
    )
    group.add_argument(
        "--recent", type=int, metavar="N",
        help="Auto-discover the N most recent sessions from the trace list",
    )
    group.add_argument(
        "--session-file", type=Path, metavar="FILE",
        help="Path to a file with session IDs (one UUID per line, or a JSON array/object)",
    )
    parser.add_argument(
        "--base-url", default="http://localhost:8000", metavar="URL",
        help="Backend base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--out", type=Path, default=Path("sessions"), metavar="DIR",
        help="Output directory (default: ./sessions)",
    )
    parser.add_argument(
        "--eval", action="store_true",
        help="Run eval_session quality scoring for each session",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")

    # ── Health check ──────────────────────────────────────────────────────────
    print(bold("Checking backend…"), end=" ", flush=True)
    try:
        with httpx.Client(timeout=5) as hc:
            r = hc.get(f"{base_url}/api/v1/health")
            r.raise_for_status()
        print(green("✓ OK"))
    except Exception as exc:
        print(red(f"✗ unreachable ({exc})"))
        print(f"  Start the backend: {cyan('uvicorn app.main:app --reload')}")
        sys.exit(2)

    # ── Resolve session IDs ───────────────────────────────────────────────────
    session_ids: list[str] = []

    if args.session_id:
        session_ids = [str(UUID(s)) for s in args.session_id]  # validate UUIDs

    elif args.recent:
        print(f"Discovering {args.recent} most recent sessions…", end=" ", flush=True)
        with httpx.Client(timeout=30) as hc:
            session_ids = fetch_recent_sessions(hc, base_url, args.recent)
        print(green(f"✓ found {len(session_ids)}"))

    elif args.session_file:
        raw = args.session_file.read_text()
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                # Support both plain UUID strings and dicts with a "session_id" key
                session_ids = [
                    str(x["session_id"] if isinstance(x, dict) else x)
                    for x in parsed
                ]
            elif isinstance(parsed, dict):
                # Playwright results.json or custom {session_ids: [...]}
                session_ids = [str(x) for x in (
                    parsed.get("session_ids")
                    or parsed.get("sessionIds")
                    or list(parsed.values())
                )]
        except json.JSONDecodeError:
            # Plain text: one UUID per line
            session_ids = [ln.strip() for ln in raw.splitlines() if ln.strip()]

    if not session_ids:
        print(yellow("No session IDs found — nothing to export."))
        sys.exit(0)

    print(f"\n{bold(f'Exporting {len(session_ids)} session(s) → {args.out}/')}\n")

    # ── Export ────────────────────────────────────────────────────────────────
    t0 = time.monotonic()
    exports: list[SessionExport] = []
    failed = 0

    with httpx.Client(timeout=60) as client:
        for sid in session_ids:
            try:
                exp = export_session(
                    client, base_url, sid, args.out, run_eval=args.eval,
                )
                if exp:
                    exports.append(exp)
            except Exception as exc:
                print(f"  {red('✗')} {sid}: {exc}")
                failed += 1

    if exports:
        write_manifest(exports, args.out)

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed = time.monotonic() - t0
    print()
    print(bold("─" * 60))
    ok_col = green if failed == 0 else yellow
    print(
        f"  {ok_col(f'{len(exports)}/{len(session_ids)} sessions exported')}  "
        f"{dim(f'{elapsed:.1f}s')}"
    )
    if failed:
        print(f"  {red(f'{failed} failed')}")
    print(bold("─" * 60))

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
