"""Canonical enums for the AI-agent success-metrics pipeline (ticket 3713).

Import these from APIs, repositories, and tests — do not scatter string literals.
"""

from __future__ import annotations

CREATE_SR_INTENTS: frozenset[str] = frozenset(
    {
        "CREATE_RDD_SERVICE_REQUEST",
        "CREATE_HANDED_OVER_SR",
        "CREATE_FITOUT_CONFIRM_FOS",
        "CREATE_FITOUT_CONFIRM_END",
        "CREATE_FITOUT_CONFIRM_TRADING",
        "CREATE_WORK_PERMIT_SR",
    }
)

TERMINAL_WORKFLOW_STAGES: frozenset[str] = frozenset(
    {
        "SR_COMPLETED",
        "HANDED_OVER_COMPLETED",
    }
)

EVENT_SESSION_STARTED = "session_started"
EVENT_SESSION_ENDED = "session_ended"
EVENT_SR_SUBMITTED = "sr_submitted"
EVENT_SR_LIFECYCLE_COMPLETED = "sr_lifecycle_completed"
EVENT_FIELD_CORRECTED = "field_corrected"
EVENT_FLOW_CANCELLED = "flow_cancelled"

ALLOWED_EVENT_NAMES: frozenset[str] = frozenset(
    {
        EVENT_SESSION_STARTED,
        EVENT_SESSION_ENDED,
        EVENT_SR_SUBMITTED,
        EVENT_SR_LIFECYCLE_COMPLETED,
        EVENT_FIELD_CORRECTED,
        EVENT_FLOW_CANCELLED,
    }
)

END_REASON_IDLE = "IDLE_TIMEOUT"
END_REASON_CLIENT = "CLIENT_ENDED"
ALLOWED_END_REASONS: frozenset[str] = frozenset({END_REASON_IDLE, END_REASON_CLIENT})

FEEDBACK_TURN_THUMBS = "TURN_THUMBS"
FEEDBACK_FLOW_USEFUL = "FLOW_USEFUL"
FEEDBACK_CSAT = "CSAT"
FEEDBACK_ADMIN_REVIEW = "ADMIN_REVIEW"
FEEDBACK_AUTOMATED_EVAL = "AUTOMATED_EVAL"

ALLOWED_FEEDBACK_TYPES: frozenset[str] = frozenset(
    {
        FEEDBACK_TURN_THUMBS,
        FEEDBACK_FLOW_USEFUL,
        FEEDBACK_CSAT,
        FEEDBACK_ADMIN_REVIEW,
        FEEDBACK_AUTOMATED_EVAL,
        "rating",  # legacy admin panel value; mapped to ADMIN_REVIEW on write
    }
)

LABEL_THUMBS_UP = "THUMBS_UP"
LABEL_THUMBS_DOWN = "THUMBS_DOWN"
LABEL_USEFUL = "USEFUL"
LABEL_NOT_USEFUL = "NOT_USEFUL"

NOT_USEFUL_REASONS: frozenset[str] = frozenset(
    {
        "WRONG_LEASE",
        "WRONG_FIELDS",
        "TOO_MANY_STEPS",
        "CONFUSING",
        "OTHER",
    }
)

FLOW_CANCEL_ACTIONS: frozenset[str] = frozenset(
    {
        "cancel",
        "cancel_create_sr",
        "cancel_sr",
        "confirm_cancel_sr",
        "cancel_milestone",
    }
)

TOOL_TYPE_SR_CREATE = "SERVICE_REQUEST_CREATE"


def clamp_active_delta_ms(delta_ms: int, max_ms: int) -> int:
    """Clamp a heartbeat active-time delta to ``[0, max_ms]``."""
    if delta_ms < 0:
        return 0
    if max_ms < 0:
        return 0
    return min(delta_ms, max_ms)


def ratio(numerator: int, denominator: int) -> float:
    """Safe division; ``0`` when the denominator is empty."""
    if denominator <= 0:
        return 0.0
    return numerator / denominator
