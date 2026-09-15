"""The guided-tour choice: does the assistant drive the form, or the user?

Asked once per draft, before the first field question of a collection stage,
and remembered in ``collected_data`` so it survives every turn of that draft.

Both answers behave identically in chat — the assistant asks the same
questions in the same order, and the user answers them. The choice only
governs the *form*:

* ``guided``  — the form is inert, the field in play is ringed and scrolled
  to, and everything else is dimmed.
* ``open``    — the form stays editable. The user may answer in chat or by
  typing into the form; a form edit reaches the draft through the existing
  silent form→chat sync, and the assistant simply moves on to the next
  question without remarking on it.

Workflow-agnostic on purpose: the gate lives in shared nodes and is enabled
per workflow by ``WorkflowConfig.guided_tour_gate``, so a new service request
inherits it by registering a config rather than by wiring anything here.
"""

from __future__ import annotations

from typing import Any

# collected_data key. No leading underscore: this is a real answer the user
# gave, persisted with the draft like ``mm_optional_upload_status``.
GUIDED_TOUR_KEY = "guided_tour"

GUIDED = "guided"
OPEN = "open"
# Written when the question goes out, so an answer is only ever read as an
# answer to *this* question. Without it a bare "no" at the confirmation card
# would be taken as "no, leave the form open" and swallow a cancel.
ASKING = "asking"

# Chip actions. Direct-set, so a tap resolves the choice without an
# extraction round-trip; every agent's entry route sends them to merge_state.
ACTION_GUIDED = "select_guided_tour_guided"
ACTION_OPEN = "select_guided_tour_open"

GUIDED_TOUR_ACTIONS: dict[str, str] = {
    ACTION_GUIDED: GUIDED,
    ACTION_OPEN: OPEN,
}

_QUESTION = (
    "Before we start — would you like me to guide you through the form?\n\n"
    "**Guide me** — I'll ask for each value and highlight the field on the "
    "form as we go.\n"
    "**Leave the form open** — I'll still ask, and you can answer here or "
    "type straight into the form."
)


def is_enabled() -> bool:
    """True when the choice should be offered at all."""
    from app.core.config import get_settings

    return bool(get_settings().guided_tour_choice_enabled)


def answered(collected: dict[str, Any]) -> bool:
    """True once the user has made the choice for this draft."""
    return str(collected.get(GUIDED_TOUR_KEY) or "").strip() in (GUIDED, OPEN)


def is_pending(collected: dict[str, Any]) -> bool:
    """True while the question is on screen and unanswered."""
    return str(collected.get(GUIDED_TOUR_KEY) or "").strip() == ASKING


def mode(collected: dict[str, Any]) -> str:
    """The mode in force. Guided is the default, and the behaviour that ships
    today, so an unanswered or unknown value never unlocks the form."""
    value = str(collected.get(GUIDED_TOUR_KEY) or "").strip()
    return OPEN if value == OPEN else GUIDED


def is_open_form(collected: dict[str, Any]) -> bool:
    """True when the user asked to keep the form editable."""
    return mode(collected) == OPEN


def build_question_response(collected: dict[str, Any]) -> dict[str, Any]:
    """State patch that asks the choice, with its two chips."""
    return {
        "collected_data": {**collected, GUIDED_TOUR_KEY: ASKING},
        "response_message": _QUESTION,
        "response_ui": {
            "type": "text_question",
            "field": GUIDED_TOUR_KEY,
            "message": _QUESTION,
            "verbatim": True,
        },
        "response_decorations": {
            "suggestions": {
                "kind": "labeled_options",
                "options": [
                    {
                        "label": "Guide me",
                        "value": GUIDED,
                        "action": ACTION_GUIDED,
                    },
                    {
                        "label": "Leave the form open",
                        "value": OPEN,
                        "action": ACTION_OPEN,
                    },
                ],
            }
        },
        "status": "WAITING_FOR_USER",
        # Not a collectable field: nothing should try to bind a stray answer
        # to it, and the form must not try to highlight a control named
        # "guided_tour" that does not exist.
        "active_field": None,
    }


# Typed answers, so a user who ignores the chips is not stuck in a loop.
# Deliberately deterministic: the choice is not a collectable field, so the
# extractor would drop it, and asking an LLM to read "yes" is not worth a call.
_GUIDED_WORDS = ("guide", "guided", "tour", "walk", "yes", "please do", "sure")
_OPEN_WORDS = (
    "open",
    "form",
    "myself",
    "self",
    "no",
    "skip",
    "manual",
    "manually",
    "own",
)


def parse_answer(message: str | None) -> str | None:
    """Read a typed answer to the choice; ``None`` when it is not one."""
    text = str(message or "").strip().lower()
    if not text or len(text) > 60:
        return None
    # "no tour", "no, guide me" — read the verb, not just the yes/no.
    has_guided = any(word in text for word in _GUIDED_WORDS)
    has_open = any(word in text for word in _OPEN_WORDS)
    if has_guided and has_open:
        # Both present: the specific word wins over the bare yes/no.
        specific_guided = any(w in text for w in ("guide", "guided", "tour", "walk"))
        specific_open = any(w in text for w in ("open", "form", "myself", "manual", "own"))
        if specific_guided and not specific_open:
            return GUIDED
        if specific_open and not specific_guided:
            return OPEN
        return None
    if has_guided:
        return GUIDED
    if has_open:
        return OPEN
    return None


def has_pending_answer(state: dict[str, Any]) -> bool:
    """True when this turn carries an answer to a choice still outstanding.

    Used by every agent's entry route to send the turn to ``merge_state``
    instead of the extractor, which has no field to bind this to.
    """
    if not is_enabled():
        return False
    collected = state.get("collected_data") or {}
    # Only while our own question is outstanding — see ASKING.
    if not is_pending(collected):
        return False
    if str(state.get("action_override") or "") in GUIDED_TOUR_ACTIONS:
        return True
    return parse_answer(state.get("user_message")) is not None


def apply_answer(collected: dict[str, Any], state: dict[str, Any]) -> bool:
    """Record a tapped or typed choice. True when one was recorded."""
    if answered(collected) or not is_pending(collected):
        return False
    if apply_action(collected, state.get("action_override")):
        return True
    typed = parse_answer(state.get("user_message"))
    if typed is None:
        return False
    collected[GUIDED_TOUR_KEY] = typed
    return True


def apply_action(collected: dict[str, Any], action: str | None) -> bool:
    """Write the choice for a chip action. True when one was applied."""
    chosen = GUIDED_TOUR_ACTIONS.get(str(action or ""))
    if chosen is None:
        return False
    collected[GUIDED_TOUR_KEY] = chosen
    return True
