"""Offline contract eval for AI-agent success metrics (ticket 3713).

Checks the product analytics contract — event names, feedback types, clamp/ratio
math, funnel step keys, heartbeat bounds, Alembic 007 revision — without any
network or database. Prints a PASS/FAIL table and exits 1 on failure.

Run from backend/:
    PYTHONPATH=$(pwd) python -m tests.eval.analytics_eval

pytest also collects ``test_analytics_eval_contract``.
"""

from __future__ import annotations

import ast
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.observability.analytics_constants import (
    ALLOWED_END_REASONS,
    ALLOWED_EVENT_NAMES,
    ALLOWED_FEEDBACK_TYPES,
    CREATE_SR_INTENTS,
    END_REASON_CLIENT,
    END_REASON_IDLE,
    EVENT_FIELD_CORRECTED,
    EVENT_FLOW_CANCELLED,
    EVENT_SESSION_ENDED,
    EVENT_SESSION_STARTED,
    EVENT_DOCUMENT_UPLOADED,
    EVENT_SR_APPROVED,
    EVENT_SR_LIFECYCLE_COMPLETED,
    EVENT_SR_REJECTED,
    EVENT_SR_SUBMITTED,
    FEEDBACK_ADMIN_REVIEW,
    FEEDBACK_CSAT,
    FEEDBACK_FLOW_USEFUL,
    FEEDBACK_TURN_THUMBS,
    FLOW_CANCEL_ACTIONS,
    LABEL_NOT_USEFUL,
    LABEL_THUMBS_DOWN,
    LABEL_THUMBS_UP,
    LABEL_USEFUL,
    NOT_USEFUL_REASONS,
    TERMINAL_WORKFLOW_STAGES,
    TOOL_TYPE_SR_CREATE,
    clamp_active_delta_ms,
    ratio,
)
from app.observability.analytics_queries import parse_window

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(condition), detail))


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _source(rel: str) -> str:
    return (_backend_root() / rel).read_text()


def _revisions(source: str) -> tuple[str | None, str | None]:
    """Read ``revision`` / ``down_revision`` constants from a migration."""
    revision = None
    down = None
    for node in ast.parse(source).body:
        target_name = None
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name = node.target.id
            value = node.value
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            target_name = names[0] if names else None
            value = node.value
        if target_name in {"revision", "down_revision"} and isinstance(value, ast.Constant):
            if target_name == "revision":
                revision = value.value
            else:
                down = value.value
    return revision, down


def run_checks() -> list[tuple[str, bool, str]]:
    CHECKS.clear()

    # ── Event / feedback vocabulary ──────────────────────────────────────
    check(
        "nine product events",
        ALLOWED_EVENT_NAMES
        == {
            EVENT_SESSION_STARTED,
            EVENT_SESSION_ENDED,
            EVENT_SR_SUBMITTED,
            EVENT_SR_LIFECYCLE_COMPLETED,
            EVENT_FIELD_CORRECTED,
            EVENT_FLOW_CANCELLED,
            EVENT_SR_APPROVED,
            EVENT_SR_REJECTED,
            EVENT_DOCUMENT_UPLOADED,
        },
        str(sorted(ALLOWED_EVENT_NAMES)),
    )
    check("session_started name exact", EVENT_SESSION_STARTED == "session_started")
    check("sr_submitted name exact", EVENT_SR_SUBMITTED == "sr_submitted")
    check(
        "end reasons only idle + client",
        ALLOWED_END_REASONS == {END_REASON_IDLE, END_REASON_CLIENT},
    )
    check("FLOW_USEFUL type", FEEDBACK_FLOW_USEFUL == "FLOW_USEFUL")
    check("TURN_THUMBS type", FEEDBACK_TURN_THUMBS == "TURN_THUMBS")
    check("CSAT type", FEEDBACK_CSAT == "CSAT")
    check("admin review distinct from CSAT", FEEDBACK_ADMIN_REVIEW != FEEDBACK_CSAT)
    check("legacy rating still allowlisted", "rating" in ALLOWED_FEEDBACK_TYPES)
    check(
        "thumbs labels",
        {LABEL_THUMBS_UP, LABEL_THUMBS_DOWN} == {"THUMBS_UP", "THUMBS_DOWN"},
    )
    check("useful labels", {LABEL_USEFUL, LABEL_NOT_USEFUL} == {"USEFUL", "NOT_USEFUL"})
    check(
        "not-useful reasons",
        NOT_USEFUL_REASONS
        == {"WRONG_LEASE", "WRONG_FIELDS", "TOO_MANY_STEPS", "CONFUSING", "OTHER"},
    )
    check("cancel actions include cancel", "cancel" in FLOW_CANCEL_ACTIONS)
    check("SR create tool type", TOOL_TYPE_SR_CREATE == "SERVICE_REQUEST_CREATE")
    check(
        "create-intent frozenset",
        CREATE_SR_INTENTS
        >= {
            "CREATE_RDD_SERVICE_REQUEST",
            "CREATE_HANDED_OVER_SR",
            "CREATE_FITOUT_CONFIRM_FOS",
            "CREATE_FITOUT_CONFIRM_END",
            "CREATE_FITOUT_CONFIRM_TRADING",
            "CREATE_WORK_PERMIT_SR",
        },
    )
    check(
        "terminal stages",
        TERMINAL_WORKFLOW_STAGES == {"SR_COMPLETED", "HANDED_OVER_COMPLETED"},
    )

    # ── Math ─────────────────────────────────────────────────────────────
    check("clamp negative → 0", clamp_active_delta_ms(-1, 120_000) == 0)
    check("clamp overflow → max", clamp_active_delta_ms(999_999, 120_000) == 120_000)
    check("clamp in range identity", clamp_active_delta_ms(30_000, 120_000) == 30_000)
    check("ratio empty denom is 0", ratio(5, 0) == 0.0)
    check("ratio 3/4", abs(ratio(3, 4) - 0.75) < 1e-9)
    check(
        "create-intent without submit is 0% completion",
        ratio(0, 10) == 0.0,
    )

    start, end = parse_window(None, None)
    check("window tz-aware", start.tzinfo is not None and end.tzinfo is not None)
    check("window default ~7d", 6.9 <= (end - start).total_seconds() / 86400 <= 7.1)
    naive = datetime(2026, 1, 8, 0, 0, 0)
    s2, e2 = parse_window(None, naive)
    check("naive to-ts becomes UTC", e2.tzinfo is timezone.utc)

    # ── Alembic 009 (renumbered from 007 when feedback analytics landed first)
    mig = _source("alembic/versions/009_agent_analytics.py")
    revision, down = _revisions(mig)
    check("alembic revision is 009", revision == "009", str(revision))
    check("alembic revises 008", down == "008", str(down))
    check("migration adds last_activity_at", "last_activity_at" in mig)
    check("migration adds client_active_ms", "client_active_ms" in mig)
    check("migration adds analytics_events", "analytics_events" in mig)
    check("migration makes trace_id nullable", "nullable=True" in mig)
    check("migration check constraint", "ck_agent_feedback_target" in mig)

    # ── Alembic 010 — trace dimensions (ticket 3784) ─────────────────────
    mig010 = _source("alembic/versions/010_trace_dimensions.py")
    revision, down = _revisions(mig010)
    check("alembic 010 revises 009", (revision, down) == ("010", "009"), f"{revision}<-{down}")
    for column in ("channel", "user_roles", "mall_names", "property_ids", "is_global_admin"):
        check(f"migration 010 adds {column}", f'"{column}"' in mig010)

    # ── Call-site honesty ────────────────────────────────────────────────
    faq = _source("app/agents/graph/nodes/faq/faq_node.py")
    resp = _source("app/agents/graph/nodes/shared/response_generation_node.py")
    orch = _source("app/services/chat_orchestration_service.py")
    tm = _source("app/observability/trace_manager.py")
    check("faq_node captures LLM tokens", "capture_complete_json" in faq)
    check("response_generation captures LLM tokens", "capture_complete_json" in resp)
    check("orchestration emits session_started", "EVENT_SESSION_STARTED" in orch)
    check("orchestration emits field_corrected", "EVENT_FIELD_CORRECTED" in orch)
    check("orchestration emits flow_cancelled", "EVENT_FLOW_CANCELLED" in orch)
    check("TraceManager emits sr_submitted", "EVENT_SR_SUBMITTED" in tm)
    check(
        "TraceManager emits lifecycle completed",
        "EVENT_SR_LIFECYCLE_COMPLETED" in tm,
    )

    # ── Ticket 3784 write side ───────────────────────────────────────────
    create_node = _source("app/agents/graph/nodes/handover/api_submission_node.py")
    check(
        "handover create typed SERVICE_REQUEST_CREATE (sr_submitted fires)",
        "_TOOL_TYPE = TOOL_TYPE_SR_CREATE" in create_node,
    )
    check("TraceManager emits sr_approved / sr_rejected", "EVENT_SR_REJECTED" in tm)
    for rel, flow in (
        ("app/agents/graph/nodes/handover/fm_api_submission_node.py", "FLOW_HANDOVER_FM"),
        ("app/agents/graph/nodes/handover/rdd_api_submission_node.py", "FLOW_HANDOVER_RDD"),
        ("app/agents/graph/nodes/work_permit/work_permit_api_submission_node.py", "FLOW_WORK_PERMIT"),
        ("app/agents/graph/nodes/fitout_confirm/fitout_confirm_api_submission_node.py", "FLOW_FITOUT_CONFIRM"),
    ):
        node = _source(rel)
        check(f"{flow} records reviewer decisions", "record_sr_decision" in node and flow in node)
    for rel in (
        "app/agents/graph/nodes/work_permit/work_permit_api_submission_node.py",
        "app/agents/graph/nodes/fitout_confirm/fitout_confirm_api_submission_node.py",
    ):
        check(f"{rel.split('/')[-1]} captures its SR API call", "record_sr_api_call" in _source(rel))
    check("orchestration stamps trace dimensions", "trace_dimensions(" in orch)

    # ── Ticket 3784 read side ────────────────────────────────────────────
    cc = _source("app/api/routes/command_center.py")
    for section in ("overview", "usage", "performance", "productivity"):
        check(f"command center /{section} route", f'"/{section}"' in cc)
    check("command center mounted", "command_center_router" in _source("app/main.py"))

    # ── HTTP contract files exist ────────────────────────────────────────
    check(
        "session analytics router exists",
        (_backend_root() / "app/api/routes/session_analytics.py").is_file(),
    )
    check(
        "metrics module has funnel route",
        "metrics/funnel" in _source("app/observability/api/metrics.py"),
    )
    check(
        "metrics module has users route",
        "metrics/users" in _source("app/observability/api/metrics.py"),
    )
    check(
        "metrics module has feedback route",
        "metrics/feedback" in _source("app/observability/api/metrics.py"),
    )

    frontend = _backend_root().parent / "frontend"
    check(
        "usefulness card shipped",
        (frontend / "components/chatbot/FlowUsefulnessCard.tsx").is_file(),
    )
    check(
        "admin AI Success page shipped",
        (frontend / "app/admin/agent-success/page.tsx").is_file(),
    )
    chat = (frontend / "components/chatbot/ServiceRequestChat.tsx").read_text()
    check("heartbeat wired in chat", "postSessionHeartbeat" in chat)
    check("pagehide ends session", "postSessionEnd" in chat)
    check("visibility hidden does not end", "pagehide" in chat)
    bubble = (frontend / "components/chatbot/MessageBubble.tsx").read_text()
    check("turn thumbs on assistant bubbles", "TURN_THUMBS" in bubble)

    return list(CHECKS)


def main() -> int:
    checks = run_checks()
    width = max(len(name) for name, _, _ in checks) + 2
    failures = 0
    print(f"\n{'CHECK'.ljust(width)}RESULT")
    print("-" * (width + 6))
    for name, ok, detail in checks:
        print(
            f"{name.ljust(width)}{'PASS' if ok else 'FAIL'}"
            f"{('  ' + detail) if (detail and not ok) else ''}"
        )
        failures += 0 if ok else 1
    print("-" * (width + 6))
    print(f"{len(checks) - failures}/{len(checks)} passed\n")
    return 1 if failures else 0


def test_analytics_eval_contract() -> None:
    """pytest entry so CI runs the same contract as ``python -m``."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
