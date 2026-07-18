#!/usr/bin/env python3
"""Session trace evaluator for the Handover Service Request chatbot.

Fetches the full replay for a given session_id from the live backend and
scores every turn against a set of quality criteria.

Usage
-----
From the backend/ directory:

    python -m tests.eval.eval_session --session-id f2776b79-d1c2-4b90-a8f6-d25aa2adc3a0

    # Pretty-print only (no JSON file)
    python -m tests.eval.eval_session --session-id <uuid> --no-json

    # Point at a different backend
    python -m tests.eval.eval_session --session-id <uuid> --base-url http://staging:8000

Criteria evaluated per turn
----------------------------
Original criteria (1–7):
1. STATUS              — trace status must be SUCCESS
2. LATENCY             — warn when total_latency_ms > 5 000 ms
3. INTENT              — flag UNKNOWN intent after Turn 1 on an active agent
4. STAGE               — workflow_stage must not regress (only forward or stay same)
5. EXTRACTION          — flag when no fields were extracted on a field-collection turn
6. CONFIRMATION        — warn when all fields are present but confirmation_card not issued
7. SUBMISSION          — verify SR_CREATED stage reached on the final successful turn

Extended criteria from agent_ui_fixes.md (8–16):
8.  CANCEL_FLOW        — cancel action without confirmation prompt before completion → FAIL
9.  NOTES_VS_DESCRIPTION — notes field populated when description was the active field → WARN
10. ACTIVE_FIELD_BINDING — field extracted does not match active_field on that turn → WARN
11. COMMENTS_PROMPTED  — all other fields collected but comments never explicitly asked → WARN
12. RDD_FIELD_ORDER    — actual_handover_date extracted before guideLineLink + rdd_upload_doc_type → WARN
13. STALE_STATE        — collected_data from previous SR leaks into a new SR intent turn → FAIL
14. STAGE_COMPLETE_EMITTED — FM/RDD submission does not emit stage_complete → WARN
15. CANCEL_STATE_CLEARED — after cancel, collected_data is non-empty → FAIL
16. CONFIRMATION_STATUS_LEAK — confirmation_status=CONFIRMED persists into a new-SR turn → FAIL
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "http://localhost:8000"
HIGH_LATENCY_MS = 5_000

STAGE_ORDER = {"CREATE_SR": 0, "FM_REVIEW": 1, "RDD_REVIEW": 2, "SR_CREATED": 3}

# Fields the user must supply (not backend-derived).  Used to detect turns
# where the bot should have extracted at least one field.
USER_SUPPLIED_FIELDS = frozenset(
    {
        "title",
        "description",
        "startDate",
        "endDate",
        "inspectionTime",
        "inspection_done_by",
        "comments",
        "notes",
        "guideLineLink",
        "rdd_upload_doc_type",
        "document_status",
        "actual_handover_date",
        "fitout_start_date",
        "fitout_end_date",
    }
)

# RDD date fields that must NOT appear before guideLineLink AND rdd_upload_doc_type
# are collected (used by the RDD_FIELD_ORDER check).
_RDD_DATE_FIELDS = frozenset({"actual_handover_date", "fitout_start_date", "fitout_end_date"})

# RDD pre-upload required fields — must both be present before dates are asked.
_RDD_PRE_UPLOAD_FIELDS = frozenset({"guideLineLink", "rdd_upload_doc_type"})

# Fields that must all be collected before comments can be considered "never prompted".
_COMMENTS_PREREQUISITE_FIELDS = frozenset(
    {"description", "startDate", "endDate", "inspectionTime", "inspection_done_by", "notes"}
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    PASS = "PASS"
    INFO = "INFO"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class CheckResult:
    name: str
    severity: Severity
    message: str


@dataclass
class TurnEval:
    turn_number: int
    trace_id: str
    input_message: str
    output_message: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def worst_severity(self) -> Severity:
        order = [Severity.FAIL, Severity.WARN, Severity.INFO, Severity.PASS]
        for sev in order:
            if any(c.severity == sev for c in self.checks):
                return sev
        return Severity.PASS

    def add(self, name: str, severity: Severity, message: str) -> None:
        self.checks.append(CheckResult(name, severity, message))


@dataclass
class SessionEval:
    session_id: str
    trace_count: int
    turns: list[TurnEval] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {s.value: 0 for s in Severity}
        for t in self.turns:
            counts[t.worst_severity.value] += 1
        return counts


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def fetch_session_replay(session_id: str, base_url: str) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/observability/sessions/{session_id}/replay"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url)
    if resp.status_code == 404:
        print(f"[ERROR] Session {session_id} not found at {url}", file=sys.stderr)
        sys.exit(1)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Per-turn evaluation logic
# ---------------------------------------------------------------------------


def _has_field_collection_turns(turn_data: dict[str, Any]) -> bool:
    """True if this turn ran field_extraction (captured via runs)."""
    return any(r["run_name"] == "field_extraction" for r in turn_data.get("runs", []))


def _extracted_user_fields(turn_data: dict[str, Any]) -> list[str]:
    """Return names of user-supplied fields extracted on this turn (from LLM call output)."""
    result: list[str] = []
    for llm in turn_data.get("llm_calls", []):
        out = llm.get("structured_output") or {}
        for fname in (out.get("fields") or {}).keys():
            if fname in USER_SUPPLIED_FIELDS:
                result.append(fname)
    return result


def _confirmation_card_issued(turn_data: dict[str, Any]) -> bool:
    """True when any run or tool call output contains a confirmation_card hint.

    The session replay does not store the full HTTP response body; this
    heuristic checks run outputs for any key mentioning 'confirmation'.
    """
    for run in turn_data.get("runs", []):
        out = json.dumps(run.get("output") or {})
        if "confirmation" in out.lower():
            return True
    return False


# ---------------------------------------------------------------------------
# Extended check helpers (Issues 1–7 from agent_ui_fixes.md)
# ---------------------------------------------------------------------------


def _get_collected_data_after(turn_data: dict[str, Any]) -> dict[str, Any]:
    """Return collected_data from the last AFTER_NODE state snapshot on this turn."""
    for snap in reversed(turn_data.get("state_snapshots", [])):
        if snap.get("snapshot_type") == "AFTER_NODE":
            cd = (snap.get("state") or {}).get("collected_data")
            if isinstance(cd, dict):
                return cd
    return {}


def _get_collected_data_before(turn_data: dict[str, Any]) -> dict[str, Any]:
    """Return collected_data from the first BEFORE_NODE state snapshot on this turn."""
    for snap in turn_data.get("state_snapshots", []):
        if snap.get("snapshot_type") == "BEFORE_NODE":
            cd = (snap.get("state") or {}).get("collected_data")
            if isinstance(cd, dict):
                return cd
    return {}


def _get_active_field(turn_data: dict[str, Any]) -> str | None:
    """Return the active_field set at the END of this turn (after missing_field_node)."""
    for snap in reversed(turn_data.get("state_snapshots", [])):
        state = snap.get("state") or {}
        if "active_field" in state:
            return state["active_field"]
    return None


def _get_response_ui(turn_data: dict[str, Any]) -> dict[str, Any]:
    """Return response_ui from the last available state snapshot."""
    for snap in reversed(turn_data.get("state_snapshots", [])):
        ui = (snap.get("state") or {}).get("response_ui")
        if isinstance(ui, dict):
            return ui
    return {}


def _cancel_action_on_turn(turn_data: dict[str, Any]) -> bool:
    """True when action_override contained a cancel_sr action on this turn."""
    trace = turn_data.get("trace", {})
    action = str(trace.get("action_override") or "").lower()
    return action in ("cancel_sr",)


def _confirmation_prompt_in_output(turn_data: dict[str, Any]) -> bool:
    """True when the output message contains a cancel-confirmation prompt."""
    trace = turn_data.get("trace", {})
    output = (trace.get("output_message") or "").lower()
    return any(kw in output for kw in ("are you sure", "sure you want", "cannot be undone", "confirm cancel"))


def _extracted_fields_this_turn(turn_data: dict[str, Any]) -> set[str]:
    """Return set of field names extracted by the LLM on this turn."""
    fields: set[str] = set()
    for llm in turn_data.get("llm_calls", []):
        out = llm.get("structured_output") or {}
        for fname in (out.get("fields") or {}).keys():
            fields.add(fname)
    return fields


def _workflow_stage_after(turn_data: dict[str, Any]) -> str | None:
    return turn_data.get("trace", {}).get("workflow_stage_after")


def _workflow_stage_before(turn_data: dict[str, Any]) -> str | None:
    return turn_data.get("trace", {}).get("workflow_stage_before")


def _confirmation_status_after(turn_data: dict[str, Any]) -> str | None:
    """Return confirmation_status from the last after-node state snapshot."""
    for snap in reversed(turn_data.get("state_snapshots", [])):
        if snap.get("snapshot_type") == "AFTER_NODE":
            return (snap.get("state") or {}).get("confirmation_status")
    return None


def _all_user_fields_present(trace: dict[str, Any], turn_data: dict[str, Any]) -> bool:
    """Check state snapshots for presence of all required user fields.

    Looks for AFTER_NODE snapshots containing collected_data.
    """
    for snap in turn_data.get("state_snapshots", []):
        if snap.get("snapshot_type") != "AFTER_NODE":
            continue
        state = snap.get("state") or {}
        cd = state.get("collected_data") or {}
        if all(cd.get(f) for f in ("description", "startDate", "endDate", "inspection_done_by")):
            return True
    return False


def evaluate_turn(
    turn_number: int,
    turn_data: dict[str, Any],
    prev_stage: str | None,
) -> TurnEval:
    trace = turn_data["trace"]
    te = TurnEval(
        turn_number=turn_number,
        trace_id=trace["id"],
        input_message=trace.get("input_message") or "",
        output_message=trace.get("output_message") or "",
    )

    # ── 1. STATUS ────────────────────────────────────────────────────────────
    status = trace.get("status", "UNKNOWN")
    if status == "SUCCESS":
        te.add("STATUS", Severity.PASS, "Trace completed successfully")
    else:
        te.add("STATUS", Severity.FAIL, f"Trace status: {status} — {trace.get('error_message', '')}")

    # ── 2. LATENCY ───────────────────────────────────────────────────────────
    latency = trace.get("total_latency_ms")
    if latency is None:
        te.add("LATENCY", Severity.INFO, "Latency not recorded")
    elif latency > HIGH_LATENCY_MS:
        te.add("LATENCY", Severity.WARN, f"High latency: {latency} ms (threshold {HIGH_LATENCY_MS} ms)")
    else:
        te.add("LATENCY", Severity.PASS, f"Latency OK: {latency} ms")

    # ── 3. INTENT ────────────────────────────────────────────────────────────
    intent = trace.get("intent")
    active_agent = trace.get("active_agent")
    if turn_number == 1:
        te.add("INTENT", Severity.INFO, f"Turn 1 intent: {intent} (classification warm-up expected)")
    elif intent == "UNKNOWN" and not active_agent:
        te.add("INTENT", Severity.WARN, f"Intent UNKNOWN and no active agent on turn {turn_number}")
    elif intent:
        te.add("INTENT", Severity.PASS, f"Intent: {intent}")
    else:
        te.add("INTENT", Severity.INFO, "No intent recorded (agent already active)")

    # ── 4. STAGE PROGRESSION ─────────────────────────────────────────────────
    stage_after = trace.get("workflow_stage_after")
    stage_before = trace.get("workflow_stage_before")
    if stage_after and prev_stage and stage_after != prev_stage:
        prev_order = STAGE_ORDER.get(prev_stage, -1)
        after_order = STAGE_ORDER.get(stage_after, -1)
        if after_order < prev_order:
            te.add("STAGE", Severity.WARN, f"Stage regression: {prev_stage} → {stage_after}")
        else:
            te.add("STAGE", Severity.PASS, f"Stage advanced: {prev_stage} → {stage_after}")
    elif stage_after:
        te.add("STAGE", Severity.PASS, f"Stage: {stage_after}")
    else:
        te.add("STAGE", Severity.INFO, f"Stage unchanged: {stage_before}")

    # ── 5. FIELD EXTRACTION ──────────────────────────────────────────────────
    ran_extraction = _has_field_collection_turns(turn_data)
    if ran_extraction:
        extracted = _extracted_user_fields(turn_data)
        if extracted:
            te.add("EXTRACTION", Severity.PASS, f"Extracted fields: {', '.join(extracted)}")
        else:
            # The LLM may legitimately extract nothing (e.g. a declination)
            te.add("EXTRACTION", Severity.INFO, "Field extraction ran but no user-supplied fields extracted")
    else:
        te.add("EXTRACTION", Severity.INFO, "Field extraction node not traced on this turn")

    # ── 6. CONFIRMATION CARD ─────────────────────────────────────────────────
    fields_complete = _all_user_fields_present(trace, turn_data)
    card_issued = _confirmation_card_issued(turn_data)
    if fields_complete and not card_issued:
        te.add(
            "CONFIRMATION",
            Severity.WARN,
            "All required fields appear present but no confirmation card signal detected in run outputs",
        )
    elif card_issued:
        te.add("CONFIRMATION", Severity.PASS, "Confirmation card issued")
    else:
        te.add("CONFIRMATION", Severity.INFO, "Fields not yet complete — no card expected")

    # ── 7. SUBMISSION ────────────────────────────────────────────────────────
    tool_names = [tc["tool_name"] for tc in (turn_data.get("tool_calls") or [])]
    if "service_request_api.create_service_request" in tool_names:
        # Check the tool call succeeded
        for tc in turn_data["tool_calls"]:
            if tc["tool_name"] == "service_request_api.create_service_request":
                if tc.get("success"):
                    ref_id = (tc.get("response_payload") or {}).get("id", "unknown")
                    te.add("SUBMISSION", Severity.PASS, f"SR submitted successfully — reference: {ref_id}")
                else:
                    te.add("SUBMISSION", Severity.FAIL, f"SR submission failed: {tc.get('error_message')}")

    # ── 8. CANCEL_FLOW ───────────────────────────────────────────────────────
    # If cancel_sr action was sent but the output does NOT contain a confirmation
    # prompt, the cancel flow is broken — agent acted without user confirmation.
    if _cancel_action_on_turn(turn_data):
        if _confirmation_prompt_in_output(turn_data):
            te.add(
                "CANCEL_FLOW",
                Severity.PASS,
                "cancel_sr action triggered a confirmation prompt as expected",
            )
        else:
            output_snippet = (trace.get("output_message") or "")[:120]
            te.add(
                "CANCEL_FLOW",
                Severity.FAIL,
                f"cancel_sr action did not produce a confirmation prompt — "
                f"output: \"{output_snippet}\"",
            )

    # ── 9. NOTES_VS_DESCRIPTION ──────────────────────────────────────────────
    # If active_field was 'description' but LLM extracted 'notes' (not 'description'),
    # the description answer was wrongly routed to notes.
    active_field_before = _get_active_field(turn_data)
    extracted_this_turn = _extracted_fields_this_turn(turn_data)
    if (
        active_field_before == "description"
        and "notes" in extracted_this_turn
        and "description" not in extracted_this_turn
    ):
        te.add(
            "NOTES_VS_DESCRIPTION",
            Severity.WARN,
            "active_field=description but LLM extracted 'notes' instead of 'description' — "
            "description answer routed to wrong field",
        )
    elif active_field_before == "description" and "description" in extracted_this_turn:
        te.add(
            "NOTES_VS_DESCRIPTION",
            Severity.PASS,
            "Description field correctly extracted on description turn",
        )

    # ── 10. ACTIVE_FIELD_BINDING ─────────────────────────────────────────────
    # If active_field is set and a single different user field was extracted,
    # the extraction did not bind to the active field.
    if active_field_before and active_field_before in USER_SUPPLIED_FIELDS and extracted_this_turn:
        user_extracted = extracted_this_turn & USER_SUPPLIED_FIELDS
        if user_extracted and active_field_before not in user_extracted and len(user_extracted) == 1:
            wrong_field = next(iter(user_extracted))
            te.add(
                "ACTIVE_FIELD_BINDING",
                Severity.WARN,
                f"active_field={active_field_before} but only '{wrong_field}' was extracted — "
                "possible field binding mismatch (may be caused by notes/description confusion)",
            )
        elif active_field_before in (extracted_this_turn & USER_SUPPLIED_FIELDS):
            te.add(
                "ACTIVE_FIELD_BINDING",
                Severity.PASS,
                f"active_field={active_field_before} correctly extracted",
            )

    # ── 11. COMMENTS_PROMPTED ────────────────────────────────────────────────
    # If SR is submitted (SR_CREATED) but comments was never collected,
    # warn that comments was not explicitly prompted.
    stage_after = trace.get("workflow_stage_after")
    if stage_after == "SR_CREATED":
        cd_before_submit = _get_collected_data_before(turn_data)
        prereqs_met = all(
            cd_before_submit.get(f) not in (None, "")
            for f in _COMMENTS_PREREQUISITE_FIELDS
        )
        comments_never_asked = cd_before_submit.get("comments") is None
        if prereqs_met and comments_never_asked:
            te.add(
                "COMMENTS_PROMPTED",
                Severity.WARN,
                "SR submitted but comments field was never collected (remained None); "
                "comments must be explicitly prompted per agent_ui_fixes.md",
            )

    # ── 12. RDD_FIELD_ORDER ───────────────────────────────────────────────────
    # In RDD_REVIEW: date field extracted before guideLineLink or rdd_upload_doc_type.
    stage_before = trace.get("workflow_stage_before")
    if stage_before == "RDD_REVIEW" and (extracted_this_turn & _RDD_DATE_FIELDS):
        cd_before_rdd = _get_collected_data_before(turn_data)
        guideline_present = bool(cd_before_rdd.get("guideLineLink"))
        doc_type_present = bool(cd_before_rdd.get("rdd_upload_doc_type"))
        if not guideline_present or not doc_type_present:
            te.add(
                "RDD_FIELD_ORDER",
                Severity.WARN,
                f"RDD date field(s) {extracted_this_turn & _RDD_DATE_FIELDS} extracted before "
                f"guideLineLink={'present' if guideline_present else 'MISSING'} / "
                f"rdd_upload_doc_type={'present' if doc_type_present else 'MISSING'} — "
                "wrong collection order (expected: guidelines → doc type → dates)",
            )
        else:
            te.add(
                "RDD_FIELD_ORDER",
                Severity.PASS,
                "RDD date field extracted after guidelines and doc type — correct order",
            )

    # ── 13. STALE_STATE ───────────────────────────────────────────────────────
    # New CREATE_SR intent turn with non-empty collected_data = stale state leak.
    intent = trace.get("intent")
    active_agent_after = trace.get("active_agent")
    if intent == "CREATE_SR" and active_agent_after is None:
        cd_after_intent = _get_collected_data_after(turn_data)
        stale_keys = [
            k for k in ("lease_code", "description", "startDate")
            if cd_after_intent.get(k) not in (None, "")
        ]
        if stale_keys:
            te.add(
                "STALE_STATE",
                Severity.FAIL,
                f"New CREATE_SR intent but collected_data still has stale keys: {stale_keys} — "
                "state not cleared between SRs",
            )

    # ── 14. STAGE_COMPLETE_EMITTED ────────────────────────────────────────────
    # After FM approve or RDD submit/final_approve, check stage_complete emitted.
    _stage_complete_tools = {
        "service_request_api.fm.patch_approve",
        "service_request_api.rdd.submit_report",
    }
    for tc in (turn_data.get("tool_calls") or []):
        tool = tc.get("tool_name", "")
        if tool in _stage_complete_tools and tc.get("success"):
            response_ui = _get_response_ui(turn_data)
            if not response_ui.get("stage_complete") and not response_ui.get("workflow_complete"):
                te.add(
                    "STAGE_COMPLETE_EMITTED",
                    Severity.WARN,
                    f"FM/RDD tool '{tool}' succeeded but response_ui missing "
                    "stage_complete / workflow_complete — Update button may remain visible on UI",
                )
            else:
                te.add(
                    "STAGE_COMPLETE_EMITTED",
                    Severity.PASS,
                    f"stage_complete / workflow_complete correctly emitted after '{tool}'",
                )

    # ── 15. CANCEL_STATE_CLEARED ─────────────────────────────────────────────
    # After confirm_cancel_sr, collected_data must be empty.
    action_this_turn = str(trace.get("action_override") or "").lower()
    if action_this_turn == "confirm_cancel_sr":
        cd_after_cancel = _get_collected_data_after(turn_data)
        non_empty = {
            k: v for k, v in cd_after_cancel.items()
            if v not in (None, "", [], {}) and not k.startswith("_")
        }
        if non_empty:
            te.add(
                "CANCEL_STATE_CLEARED",
                Severity.FAIL,
                f"After confirm_cancel_sr, collected_data is non-empty — "
                f"stale fields: {list(non_empty.keys())}",
            )
        else:
            te.add(
                "CANCEL_STATE_CLEARED",
                Severity.PASS,
                "collected_data correctly cleared after SR cancellation",
            )

    # ── 16. CONFIRMATION_STATUS_LEAK ─────────────────────────────────────────
    # New CREATE_SR intent turn with confirmation_status=CONFIRMED = stale leak.
    if intent == "CREATE_SR" and active_agent_after is None:
        conf_status = _confirmation_status_after(turn_data)
        if conf_status == "CONFIRMED":
            te.add(
                "CONFIRMATION_STATUS_LEAK",
                Severity.FAIL,
                "confirmation_status=CONFIRMED persists into a new CREATE_SR intent turn — "
                "stale confirmation from prior SR leaked into new session state",
            )

    return te


# ---------------------------------------------------------------------------
# Session-level evaluation
# ---------------------------------------------------------------------------


def evaluate_session(replay: dict[str, Any]) -> SessionEval:
    session_id = replay["session_id"]
    traces = replay["traces"]
    se = SessionEval(session_id=session_id, trace_count=len(traces))

    prev_stage: str | None = None
    for i, turn_data in enumerate(traces, start=1):
        te = evaluate_turn(i, turn_data, prev_stage)
        se.turns.append(te)
        # Advance tracked stage
        new_stage = turn_data["trace"].get("workflow_stage_after")
        if new_stage:
            prev_stage = new_stage

    return se


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_SEV_COLOUR = {
    Severity.PASS: "\033[32m",  # green
    Severity.INFO: "\033[36m",  # cyan
    Severity.WARN: "\033[33m",  # yellow
    Severity.FAIL: "\033[31m",  # red
}
_RESET = "\033[0m"


def _colour(sev: Severity, text: str, use_colour: bool) -> str:
    if not use_colour:
        return text
    return f"{_SEV_COLOUR[sev]}{text}{_RESET}"


def print_report(se: SessionEval, use_colour: bool = True) -> None:
    print()
    print("=" * 72)
    print(f"  SESSION EVALUATION REPORT")
    print(f"  Session : {se.session_id}")
    print(f"  Turns   : {se.trace_count}")
    print("=" * 72)

    for te in se.turns:
        worst = te.worst_severity
        header = _colour(worst, f"[{worst.value:4s}]", use_colour)
        print(f"\n{header}  Turn {te.turn_number:>2}  ←  \"{te.input_message[:60]}\"")
        print(f"         ↳  \"{te.output_message[:80]}\"")
        for chk in te.checks:
            tag = _colour(chk.severity, f"  {chk.severity.value:4s}", use_colour)
            print(f"{tag}  [{chk.name:12s}]  {chk.message}")

    summary = se.summary
    print()
    print("─" * 72)
    print("  SUMMARY")
    for sev in Severity:
        count = summary.get(sev.value, 0)
        if count:
            label = _colour(sev, f"{sev.value}: {count}", use_colour)
            print(f"    {label}")
    total_pass = summary.get("PASS", 0)
    total = se.trace_count
    print(f"    Turns passing all checks: {total_pass}/{total}")
    print("─" * 72)
    print()


def to_json_report(se: SessionEval) -> dict[str, Any]:
    return {
        "session_id": se.session_id,
        "trace_count": se.trace_count,
        "summary": se.summary,
        "turns": [
            {
                "turn": te.turn_number,
                "trace_id": te.trace_id,
                "input": te.input_message,
                "output": te.output_message,
                "worst_severity": te.worst_severity.value,
                "checks": [
                    {"name": c.name, "severity": c.severity.value, "message": c.message}
                    for c in te.checks
                ],
            }
            for te in se.turns
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate agent traces for a chatbot session."
    )
    parser.add_argument(
        "--session-id",
        required=True,
        help="UUID of the chat session to evaluate",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Backend base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--output-json",
        metavar="FILE",
        help="Write JSON report to FILE",
    )
    parser.add_argument(
        "--no-colour",
        action="store_true",
        help="Disable ANSI colour output",
    )
    args = parser.parse_args()

    print(f"Fetching session replay for {args.session_id} …")
    replay = fetch_session_replay(args.session_id, args.base_url)

    se = evaluate_session(replay)
    print_report(se, use_colour=not args.no_colour)

    if args.output_json:
        report = to_json_report(se)
        with open(args.output_json, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"JSON report written to: {args.output_json}")


if __name__ == "__main__":
    main()
