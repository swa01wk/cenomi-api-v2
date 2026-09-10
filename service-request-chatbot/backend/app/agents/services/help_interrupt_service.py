"""Deterministic detection of a help question asked mid-workflow.

Once ``active_agent`` is set, ``_route_after_load`` sends every turn straight to
that agent and the supervisor never runs again — so a question asked while a
service request is being collected is bound to whatever field is open. Observed
live: "Help me with Dashboard" was taken as a lease identifier and answered with
"Sorry, I couldn't find a lease matching that information." The session could
not be recovered by refreshing, because the state is server-side; only deleting
it cleared ``active_agent``.

Re-running the supervisor every turn would fix that and cost far more than it
saves: an LLM call per turn, and field answers like "t0033400", "none" or
"21/08/2026" classified as intents. This detector is the narrow alternative —
a regex over question phrasing, run before the agent binds the message.

``faq_node`` writes only ``response_message`` / ``status``, so answering
mid-flow leaves ``collected_data``, ``active_field`` and ``workflow_stage``
untouched and collection resumes on the next turn.
"""

from __future__ import annotations

import re

# Fields whose answer is arbitrary prose. A question is a perfectly good note
# ("How do I get site access?"), so the interrupt must never fire while one of
# these is open — the same carve-out the correction detector needs, and for the
# same reason. A lease code, date or enum can never be mistaken for a question,
# so the risk lives entirely here.
_FREE_TEXT_FIELDS: frozenset[str] = frozenset(
    {"title", "description", "notes", "comments"}
)

# Explicit help phrasings. Deliberately narrow: an unrecognised question is
# merely answered as a field value (today's behaviour), whereas a false positive
# derails a request the user is halfway through.
_HELP_CUES: tuple[str, ...] = (
    r"^\s*help\b",
    r"\bhelp me\b",
    r"\bcan you (?:help|explain|tell me)\b",
    r"\bwhat (?:is|are|does|do)\b",
    r"\bhow (?:do|can|would) i\b",
    r"\bhow (?:does|do) (?:the|this|it)\b",
    r"\bwhere (?:can|do) i\b",
    r"\bwhy (?:is|are|does|do)\b",
    r"\btell me about\b",
    r"\bexplain\b",
)
_HELP_CUE_RE = re.compile("|".join(_HELP_CUES), re.IGNORECASE)

# Phrases that look like questions but are the user driving the workflow —
# these must stay with the agent.
_WORKFLOW_INTENT_RE = re.compile(
    r"\b(?:create|raise|submit|approve|cancel|update|change|edit)\b",
    re.IGNORECASE,
)


def detect_help_interrupt(
    user_message: str,
    *,
    active_agent: str | None,
    active_field: str | None,
) -> bool:
    """True when *user_message* is a help question asked mid-workflow.

    Requires an active workflow: outside one the supervisor already classifies
    intent, so there is nothing to intercept.
    """
    if not active_agent:
        return False
    if active_field in _FREE_TEXT_FIELDS:
        return False

    message = (user_message or "").strip()
    if not message:
        return False

    # "how do i create a handover request" is workflow intent, not a help
    # question — leave it with the agent that is already handling it.
    if _WORKFLOW_INTENT_RE.search(message):
        return False

    return bool(_HELP_CUE_RE.search(message))
