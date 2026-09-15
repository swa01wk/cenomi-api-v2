"""The user did the step on the SR form, not in the chat.

Creating an SR, approving it, sending it back for more information or
reassigning it from the main form never reached the chat: its card stayed
actionable, and pressing Confirm & Submit there created the SR a second time.
The form now tells the chat what it did — action
``form_action_done:<kind>[:<sub_category>]`` with the SR id — and this node
answers the way the chat answers its own submission: the success message and
the SR to view. It never calls the platform; the form already did.
"""

from __future__ import annotations

from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.observability.decorators import trace_node

FORM_ACTION_PREFIX = "form_action_done:"

# The labels the chat's own create success uses.
_CREATE_LABELS: dict[str, str] = {
    "HANDOVER": "Handover",
    "FIT_OUT_CONFIRM_FOS": "Fitout Start Confirmation",
    "FIT_OUT_CONFIRM_END": "Fitout End Confirmation",
    "FIT_OUT_CONFIRM_TRADING": "Trading Confirmation",
}

_REVIEW_OUTCOMES: dict[str, str] = {
    "approved": "was approved",
    "more_info": "was sent back for more information",
    "reassigned": "was reassigned",
    "report_submitted": "had its RDD Handover Report submitted",
}


def is_form_action(action: Any) -> bool:
    """True for the action the form sends after doing a step itself."""
    return str(action or "").startswith(FORM_ACTION_PREFIX)


def parse_form_action(action: Any) -> tuple[str, str]:
    """``form_action_done:<kind>[:<sub_category>]`` → ``(kind, sub_category)``."""
    rest = str(action or "").removeprefix(FORM_ACTION_PREFIX)
    kind, _, sub_category = rest.partition(":")
    return kind.strip(), sub_category.strip()


def _created(sub_category: str, sr_id: str) -> tuple[str, str]:
    """Success message and terminal stage for an SR the form created."""
    if sub_category == "HANDED_OVER":
        return (
            f"Your Already Trading SR has been created — reference **{sr_id}**.",
            "HANDED_OVER_COMPLETED",
        )
    label = _CREATE_LABELS.get(sub_category)
    what = f"{label} Service Request" if label else "Service Request"
    return (
        f"Your {what} has been submitted — reference **{sr_id}**.",
        "SR_CREATED",
    )


@trace_node("form_action_sync", "CHAIN")
async def form_action_sync_node(state: ServiceRequestState) -> dict[str, Any]:
    """Report a step the SR form already did, the way the chat reports its own."""
    kind, sub_category = parse_form_action(state.get("action_override"))
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    sr_id = str(backend_refs.get("sr_id") or "").strip()
    sub_category = sub_category or str(state.get("sub_category") or "")
    if not sr_id:
        # Nothing to point at — say nothing rather than claim a submission.
        return {"response_message": "", "status": "WAITING_FOR_USER"}

    if kind == "created":
        message, stage = _created(sub_category, sr_id)
        return {
            "backend_refs": backend_refs,
            "status": "WAITING_FOR_USER",
            # A terminal stage retires the create: a Confirm left on screen can
            # no longer submit it again.
            "workflow_stage": stage,
            "confirmation_status": None,
            "confirmation_required": False,
            "response_message": message,
            "response_ui": {
                "type": "message",
                "message": message,
                "workflow_complete": True,
                "workflow_stage": stage,
                "verbatim": True,
            },
        }

    outcome = _REVIEW_OUTCOMES.get(kind, "was updated")
    message = f"Done — Service Request **{sr_id}** {outcome} from the form."
    return {
        "backend_refs": backend_refs,
        "status": "WAITING_FOR_USER",
        "confirmation_status": None,
        "confirmation_required": False,
        "response_message": message,
        "response_ui": {"type": "message", "message": message, "verbatim": True},
    }
