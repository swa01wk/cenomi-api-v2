"""Human-readable labels for agent identifiers.

``active_agent`` is stored as a code (``rdd_trading_confirmation_agent``)
because it is a routing key.  Anything a person reads — a dashboard column, a
report, an export — should show the name instead.

Labels come from ``SERVICE_REQUEST_AGENT_REGISTRY`` so there is one source of
truth: adding an agent there gives it a label everywhere, with no second map
to keep in sync.  Agents outside that registry (help, notifications) are
named here, and anything unknown is title-cased rather than shown raw, so a
new agent never leaks a snake_case identifier into the UI.
"""

from __future__ import annotations

from functools import lru_cache

from app.agents.registry import SERVICE_REQUEST_AGENT_REGISTRY

# Agents that handle no service-request workflow and so are absent from the
# SR registry.
_NON_WORKFLOW_LABELS: dict[str, str] = {
    "help_agent": "Help Agent",
    "notification_agent": "Notification Agent",
    "supervisor": "Supervisor",
}

# Shown when a turn was never routed — a greeting, or a turn that failed
# before classification.
UNROUTED_LABEL = "Unrouted"


@lru_cache(maxsize=1)
def _registry_labels() -> dict[str, str]:
    """Build ``agent_name -> display_name`` from the SR agent registry."""
    labels: dict[str, str] = {}
    for agents in SERVICE_REQUEST_AGENT_REGISTRY.values():
        for config in agents.values():
            name = config.get("agent_name")
            display = config.get("display_name")
            if name and display:
                labels[name] = display
    return labels


# FAQ and notification turns are answered without ``active_agent`` ever being
# set — the supervisor routes straight to those nodes. Reporting them as
# "Unrouted" misattributes real work, so the intent names the handler instead.
_HELP_INTENTS: frozenset[str] = frozenset({"ASK_HELP", "UNKNOWN"})


def agent_display_name(agent: str | None, intent: str | None = None) -> str:
    """Return a human label for *agent*, falling back to *intent*.

    A known agent uses its registry name; an unknown one is title-cased from
    the identifier. When no agent was recorded, the intent identifies the
    handler where it can, and "Unrouted" is the last resort.
    """
    if agent:
        known = _registry_labels().get(agent) or _NON_WORKFLOW_LABELS.get(agent)
        if known:
            return known
        # Unknown agent: "some_new_agent" -> "Some New Agent".
        return " ".join(part.capitalize() for part in agent.split("_") if part)

    normalized = (intent or "").upper()
    if normalized in _HELP_INTENTS:
        return _NON_WORKFLOW_LABELS["help_agent"]

    from app.agents.services.notification_intent import NOTIFICATION_INTENTS

    if normalized in NOTIFICATION_INTENTS or normalized == "OPEN_NOTIFICATION":
        return _NON_WORKFLOW_LABELS["notification_agent"]

    return UNROUTED_LABEL
