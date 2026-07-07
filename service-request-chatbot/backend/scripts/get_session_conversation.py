#!/usr/bin/env python3
"""get_session_conversation.py — Fetch and display a full session conversation
with state transitions from the Observability API.

Given a session ID, this script fetches the complete replay from the backend
and prints a rich, readable transcript showing:

  - Every user turn and bot response
  - Workflow stage before → after each turn
  - Fields newly extracted on each turn
  - Nodes executed (execution path)
  - LLM call count, token usage, and latency
  - A state-transitions summary at the end

Usage
-----
    # Print conversation for a single session
    python scripts/get_session_conversation.py --session-id <uuid>

    # Print multiple sessions
    python scripts/get_session_conversation.py --session-id <uuid1> <uuid2>

    # Read session IDs from the Playwright manifest
    python scripts/get_session_conversation.py \\
        --session-file playwright-report-live/session-ids.json

    # Filter to a specific scenario (by session-ids.json scenario_id)
    python scripts/get_session_conversation.py \\
        --session-file playwright-report-live/session-ids.json \\
        --scenario 1

    # Save output to a file instead of stdout
    python scripts/get_session_conversation.py \\
        --session-id <uuid> --out /tmp/conversation.txt

    # Show only the state-transitions table (no dialogue)
    python scripts/get_session_conversation.py \\
        --session-id <uuid> --transitions-only

    # JSON output for programmatic use
    python scripts/get_session_conversation.py \\
        --session-id <uuid> --json

Exit codes:
    0 — success
    1 — session not found or backend error
    2 — backend unreachable
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx

# ─── ANSI colours ─────────────────────────────────────────────────────────────

_NO_COLOUR = not sys.stdout.isatty() or "--json" in sys.argv


def _c(code: str, text: str) -> str:
    return text if _NO_COLOUR else f"\033[{code}m{text}\033[0m"


def green(s: str)  -> str: return _c("92", s)
def red(s: str)    -> str: return _c("91", s)
def yellow(s: str) -> str: return _c("93", s)
def cyan(s: str)   -> str: return _c("96", s)
def blue(s: str)   -> str: return _c("34", s)
def bold(s: str)   -> str: return _c("1",  s)
def dim(s: str)    -> str: return _c("2",  s)
def magenta(s: str)-> str: return _c("35", s)


# ─── Data model ───────────────────────────────────────────────────────────────

@dataclass
class TurnRecord:
    turn: int
    trace_id: str
    user_input: str
    bot_response: str
    stage_before: Optional[str]
    stage_after: Optional[str]
    intent: Optional[str]
    active_agent: Optional[str]
    sr_id: Optional[str]
    newly_extracted: list[str]
    nodes_executed: list[str]
    llm_calls: int
    total_tokens: int
    total_cost_usd: float
    latency_ms: Optional[int]
    trace_status: str
    error_message: Optional[str]


@dataclass
class ConversationRecord:
    session_id: str
    total_turns: int
    turns: list[TurnRecord] = field(default_factory=list)
    sr_id: Optional[str] = None
    final_stage: Optional[str] = None
    total_latency_ms: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0


# ─── Fetching ─────────────────────────────────────────────────────────────────

def fetch_replay(client: httpx.Client, base_url: str, session_id: str) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/observability/sessions/{session_id}/replay"
    resp = client.get(url, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


# ─── Parsing ──────────────────────────────────────────────────────────────────

def _first_snapshot_value(
    snapshots: list[dict], snap_type: str, key: str,
) -> Any:
    for s in snapshots:
        if s.get("snapshot_type") == snap_type:
            return s.get("state", {}).get(key)
    return None


def _last_snapshot_value(snapshots: list[dict], key: str) -> Any:
    for s in reversed(snapshots):
        val = s.get("state", {}).get(key)
        if val:
            return val
    return None


def _collected_data(snapshots: list[dict]) -> dict:
    for s in reversed(snapshots):
        if s.get("snapshot_type") == "AFTER_NODE":
            cd = s.get("state", {}).get("collected_data")
            if cd:
                return cd
    return {}


def _nodes_ordered(runs: list[dict]) -> list[str]:
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


def parse_replay(replay: dict[str, Any]) -> ConversationRecord:
    session_id = str(replay.get("session_id", ""))
    entries = replay.get("traces", [])

    record = ConversationRecord(
        session_id=session_id,
        total_turns=len(entries),
    )

    prev_cd: dict = {}
    for i, entry in enumerate(entries, start=1):
        trace = entry.get("trace", {})
        runs = entry.get("runs", [])
        snapshots = entry.get("state_snapshots", [])
        llm_calls_raw = entry.get("llm_calls", [])

        stage_before = trace.get("workflow_stage_before") or _first_snapshot_value(snapshots, "BEFORE_NODE", "workflow_stage")
        stage_after  = trace.get("workflow_stage_after")  or _first_snapshot_value(snapshots, "AFTER_NODE",  "workflow_stage")

        current_cd = _collected_data(snapshots)
        newly_extracted = [k for k in current_cd if k not in prev_cd and current_cd[k]]
        prev_cd = current_cd

        sr_id = (
            _last_snapshot_value(snapshots, "sr_id")
            or _last_snapshot_value(snapshots, "backend_refs")
        )
        if isinstance(sr_id, dict):
            sr_id = sr_id.get("sr_id")
        if sr_id:
            sr_id = str(sr_id)
            if not record.sr_id:
                record.sr_id = sr_id

        total_tokens = sum(lc.get("total_tokens") or 0 for lc in llm_calls_raw)
        total_cost = sum(float(lc["cost_usd"]) for lc in llm_calls_raw if lc.get("cost_usd"))
        latency_ms = trace.get("total_latency_ms")

        record.total_latency_ms += latency_ms or 0
        record.total_tokens += total_tokens
        record.total_cost_usd += total_cost

        record.turns.append(TurnRecord(
            turn=i,
            trace_id=str(trace.get("id", "")),
            user_input=(trace.get("input_message") or "").strip(),
            bot_response=(trace.get("output_message") or "").strip(),
            stage_before=stage_before,
            stage_after=stage_after,
            intent=trace.get("intent"),
            active_agent=trace.get("active_agent"),
            sr_id=sr_id,
            newly_extracted=newly_extracted,
            nodes_executed=_nodes_ordered(runs),
            llm_calls=len(llm_calls_raw),
            total_tokens=total_tokens,
            total_cost_usd=total_cost,
            latency_ms=latency_ms,
            trace_status=trace.get("status", "UNKNOWN"),
            error_message=trace.get("error_message"),
        ))

    if record.turns:
        record.final_stage = record.turns[-1].stage_after or record.turns[-1].stage_before

    return record


# ─── Rendering ────────────────────────────────────────────────────────────────

_W = 78


def _divider(char: str = "─") -> str:
    return dim(char * _W)


def _stage_arrow(t: TurnRecord) -> str:
    before = t.stage_before or "—"
    after  = t.stage_after  or "—"
    if before == after or not t.stage_after:
        return cyan(before)
    return f"{dim(before)} {dim('→')} {cyan(bold(after))}"


def render_conversation(record: ConversationRecord, transitions_only: bool = False) -> str:
    lines: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines.append("")
    lines.append(bold("═" * _W))
    lines.append(bold(f"  SESSION CONVERSATION REPLAY"))
    lines.append(f"  Session  : {bold(record.session_id)}")
    lines.append(f"  Turns    : {record.total_turns}")
    lines.append(f"  SR ID    : {record.sr_id or '(not yet submitted)'}")
    lines.append(f"  Stage    : {record.final_stage or '—'}")
    lines.append(f"  Latency  : {record.total_latency_ms:,} ms  "
                  f"Tokens: {record.total_tokens:,}  "
                  f"Cost: ${record.total_cost_usd:.4f}")
    lines.append(bold("═" * _W))
    lines.append("")

    if not transitions_only:
        # ── Turn-by-turn dialogue ──────────────────────────────────────────────
        for t in record.turns:
            status_icon = green("✓") if t.trace_status == "SUCCESS" else red("✗")
            latency_str = dim(f"{t.latency_ms:,}ms") if t.latency_ms else dim("—")
            stage_str = _stage_arrow(t)

            lines.append(_divider())
            lines.append(
                f"  {status_icon} {bold(f'Turn {t.turn}')}"
                f"  stage={stage_str}"
                f"  {latency_str}"
                + (f"  intent={dim(t.intent)}" if t.intent else "")
            )
            lines.append("")

            # User message
            user_wrapped = _wrap(t.user_input, 74, "    ")
            lines.append(f"  {blue('User:')}  {user_wrapped}")

            # Bot response
            bot_preview = t.bot_response[:300] + ("…" if len(t.bot_response) > 300 else "")
            bot_wrapped = _wrap(bot_preview, 74, "    ")
            lines.append(f"  {magenta('Bot:')}   {bot_wrapped}")
            lines.append("")

            # Fields extracted
            if t.newly_extracted:
                lines.append(
                    f"  {dim('Fields extracted:')} "
                    + ", ".join(cyan(f) for f in t.newly_extracted)
                )

            # Nodes
            if t.nodes_executed:
                node_str = " → ".join(dim(n) for n in t.nodes_executed[:8])
                if len(t.nodes_executed) > 8:
                    node_str += dim(f" +{len(t.nodes_executed) - 8}")
                lines.append(f"  {dim('Nodes:')} {node_str}")

            # LLM info
            if t.llm_calls:
                lines.append(
                    f"  {dim('LLM:')} {t.llm_calls} call(s) "
                    f"— {t.total_tokens:,} tokens "
                    f"— ${t.total_cost_usd:.4f}"
                )

            if t.error_message:
                lines.append(f"  {red('Error:')} {t.error_message[:200]}")

            lines.append("")

    # ── State transitions summary table ───────────────────────────────────────
    lines.append(_divider("═"))
    lines.append(bold("  STATE TRANSITIONS SUMMARY"))
    lines.append(_divider())
    lines.append(
        f"  {'T':>3}  "
        f"{'STATUS':>8}  "
        f"{'STAGE BEFORE':>14}  "
        f"{'STAGE AFTER':>14}  "
        f"{'INTENT':>30}  "
        f"{'NEW FIELDS'}"
    )
    lines.append(_divider())

    for t in record.turns:
        before = t.stage_before or "—"
        after  = t.stage_after  or "—"
        transition = f"{before} → {after}" if before != after else before
        intent = (t.intent or "—")[:30]
        fields = ", ".join(t.newly_extracted[:4])
        if len(t.newly_extracted) > 4:
            fields += f" +{len(t.newly_extracted) - 4}"

        status_col = green("SUCCESS") if t.trace_status == "SUCCESS" else red(t.trace_status)
        lines.append(
            f"  {t.turn:>3}  "
            f"{status_col:>8}  "
            f"{before:>14}  "
            f"{after:>14}  "
            f"{intent:>30}  "
            f"{dim(fields)}"
        )

    lines.append(_divider())
    lines.append(
        f"  Total: {record.total_turns} turns  "
        f"Stage: {record.final_stage or '—'}  "
        f"SR: {record.sr_id or '—'}"
    )
    lines.append(_divider("═"))
    lines.append("")

    return "\n".join(lines)


def _wrap(text: str, width: int, indent: str = "") -> str:
    """Wrap long text with indent continuation."""
    if len(text) <= width:
        return text
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = indent + word
        else:
            current = current + (" " if current else "") + word
    if current:
        lines.append(current)
    return ("\n" + indent).join(lines)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch and display a session conversation with state transitions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--session-id", nargs="+", metavar="UUID",
                     help="One or more session UUIDs")
    src.add_argument("--session-file", type=Path, metavar="FILE",
                     help="JSON file with session IDs (array of UUIDs or objects with session_id)")

    parser.add_argument("--scenario", type=int, metavar="N",
                        help="Filter --session-file to this scenario_id only")
    parser.add_argument("--base-url", default="http://localhost:8000",
                        help="Backend base URL (default: http://localhost:8000)")
    parser.add_argument("--out", type=Path, metavar="FILE",
                        help="Write output to FILE instead of stdout")
    parser.add_argument("--transitions-only", action="store_true",
                        help="Show only the state-transitions table (no dialogue)")
    parser.add_argument("--json", action="store_true",
                        help="Output structured JSON instead of formatted text")

    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    # ── Health check ──────────────────────────────────────────────────────────
    print(bold("Checking backend…"), end=" ", flush=True, file=sys.stderr)
    try:
        with httpx.Client(timeout=5) as hc:
            hc.get(f"{base_url}/api/v1/health").raise_for_status()
        print(green("✓"), file=sys.stderr)
    except Exception as exc:
        print(red(f"✗ ({exc})"), file=sys.stderr)
        sys.exit(2)

    # ── Resolve session IDs ───────────────────────────────────────────────────
    session_ids: list[str] = []
    scenario_names: dict[str, str] = {}

    if args.session_id:
        session_ids = args.session_id
    else:
        raw = args.session_file.read_text()
        parsed = json.loads(raw)
        items = parsed if isinstance(parsed, list) else [parsed]
        for item in items:
            if isinstance(item, dict):
                sid = item.get("session_id", "")
                if args.scenario and item.get("scenario_id") != args.scenario:
                    continue
                if sid:
                    session_ids.append(str(sid))
                    scenario_names[str(sid)] = item.get("scenario_name", "")
            elif isinstance(item, str):
                session_ids.append(item)

    if not session_ids:
        print(yellow("No session IDs found."), file=sys.stderr)
        sys.exit(0)

    # ── Fetch + render ────────────────────────────────────────────────────────
    output_parts: list[str] = []
    json_records: list[dict] = []
    exit_code = 0

    with httpx.Client(timeout=30) as client:
        for sid in session_ids:
            scenario_label = scenario_names.get(sid, "")
            label = f"{sid}" + (f" — {scenario_label}" if scenario_label else "")
            print(f"  Fetching {dim(label)}", file=sys.stderr)

            try:
                replay = fetch_replay(client, base_url, sid)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    print(f"  {yellow('⚠')} No traces found for {sid}", file=sys.stderr)
                    continue
                print(f"  {red('✗')} HTTP {exc.response.status_code} for {sid}", file=sys.stderr)
                exit_code = 1
                continue
            except Exception as exc:
                print(f"  {red('✗')} {exc}", file=sys.stderr)
                exit_code = 1
                continue

            record = parse_replay(replay)

            if args.json:
                json_records.append(asdict(record))
            else:
                if scenario_label:
                    output_parts.append(
                        "\n" + bold(f"[ SCENARIO: {scenario_label} ]") + "\n"
                    )
                output_parts.append(
                    render_conversation(record, transitions_only=args.transitions_only)
                )

    # ── Output ────────────────────────────────────────────────────────────────
    if args.json:
        output = json.dumps(json_records if len(json_records) > 1 else json_records[0]
                            if json_records else {}, indent=2, default=str)
    else:
        output = "\n".join(output_parts)

    if args.out:
        args.out.write_text(output)
        print(f"\n  Written to: {args.out}", file=sys.stderr)
    else:
        print(output)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
