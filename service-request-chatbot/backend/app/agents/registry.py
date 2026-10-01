"""Service Request Agent Registry.

Maps ``(service_category, sub_category)`` pairs to the agent configuration
that should handle the corresponding workflow.  This is the single source of
truth for agent routing; supervisor and registry nodes must both consult it.

Adding a new service type requires only:
1. Inserting a new entry here.
2. Implementing the corresponding agent node.
"""

from __future__ import annotations

from typing import TypedDict


class AgentConfig(TypedDict):
    """Configuration record for a single registered agent."""

    agent_name: str
    """Canonical snake_case identifier used as the ``active_agent`` state key."""

    display_name: str
    """Human-readable name for logging and UI display."""

    schema_key: str
    """Key that downstream nodes use to resolve the correct field/stage schema."""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SERVICE_REQUEST_AGENT_REGISTRY: dict[str, dict[str, AgentConfig]] = {
    "FIT_OUT_AND_HANDOVER": {
        "HANDOVER": {
            "agent_name": "rdd_handover_agent",
            "display_name": "RDD Handover Agent",
            "schema_key": "handover_service_request_schema",
        },
        "HANDED_OVER": {
            "agent_name": "rdd_handover_agent",
            "display_name": "RDD Handover Agent",
            "schema_key": "handover_service_request_schema",
        },
        "FIT_OUT_CONFIRM_FOS": {
            "agent_name": "rdd_fitout_start_agent",
            "display_name": "RDD Fitout Start Agent",
            "schema_key": "fitout_confirm_schema",
        },
        "FIT_OUT_CONFIRM_END": {
            "agent_name": "rdd_fitout_end_agent",
            "display_name": "RDD Fitout End Agent",
            "schema_key": "fitout_confirm_schema",
        },
        "FIT_OUT_CONFIRM_TRADING": {
            "agent_name": "rdd_trading_confirmation_agent",
            "display_name": "RDD Trading Confirmation Agent",
            "schema_key": "fitout_confirm_schema",
        },
        "OPEN_CEILING_INSPECTION": {
            "agent_name": "rdd_ceiling_inspection_agent",
            "display_name": "RDD Ceiling Inspection Agent",
            "schema_key": "ceiling_schema",
        },
        "CLOSE_CEILING_INSPECTION": {
            "agent_name": "rdd_ceiling_inspection_agent",
            "display_name": "RDD Ceiling Inspection Agent",
            "schema_key": "ceiling_schema",
        },
        # "Raise a ceiling inspection" naming neither — the entry node asks
        # which one before anything is collected.
        "CEILING_INSPECTION": {
            "agent_name": "rdd_ceiling_inspection_agent",
            "display_name": "RDD Ceiling Inspection Agent",
            "schema_key": "ceiling_schema",
        },
    },
    "WORK_PERMIT": {
        # Legacy registry key used by supervisor CREATE before subtype is known.
        "WORK_PERMIT": {
            "agent_name": "work_permit_agent",
            "display_name": "Work Permit Agent",
            "schema_key": "work_permit_service_request_schema",
        },
        "CONSTRUCTION_HOT": {
            "agent_name": "work_permit_agent",
            "display_name": "Work Permit Agent",
            "schema_key": "work_permit_service_request_schema",
        },
        "CONSTRUCTION_COLD": {
            "agent_name": "work_permit_agent",
            "display_name": "Work Permit Agent",
            "schema_key": "work_permit_service_request_schema",
        },
        "CONSTRUCTION_ROOF_ACCESS": {
            "agent_name": "work_permit_agent",
            "display_name": "Work Permit Agent",
            "schema_key": "work_permit_service_request_schema",
        },
        "MAINTENANCE_HOT": {
            "agent_name": "work_permit_agent",
            "display_name": "Work Permit Agent",
            "schema_key": "work_permit_service_request_schema",
        },
        "MAINTENANCE_COLD": {
            "agent_name": "work_permit_agent",
            "display_name": "Work Permit Agent",
            "schema_key": "work_permit_service_request_schema",
        },
        "MAINTENANCE_ROOF_ACCESS": {
            "agent_name": "work_permit_agent",
            "display_name": "Work Permit Agent",
            "schema_key": "work_permit_service_request_schema",
        },
        "OPERATIONS": {
            "agent_name": "work_permit_agent",
            "display_name": "Work Permit Agent",
            "schema_key": "work_permit_service_request_schema",
        },
    },
    "SALES_DATA": {
        "BULK_UPLOAD": {
            "agent_name": "sales_data_agent",
            "display_name": "Sales Data Agent",
            "schema_key": "sales_data_schema",
        },
        "MANUAL_ENTRY": {
            "agent_name": "sales_data_agent",
            "display_name": "Sales Data Agent",
            "schema_key": "sales_data_schema",
        },
        "STATUS_CHECK": {
            "agent_name": "sales_data_agent",
            "display_name": "Sales Data Agent",
            "schema_key": "sales_data_schema",
        },
        "ADMIN_REVIEW": {
            "agent_name": "sales_data_agent",
            "display_name": "Sales Data Agent",
            "schema_key": "sales_data_schema",
        },
        "SUPPORTING_DOC": {
            "agent_name": "sales_data_agent",
            "display_name": "Sales Data Agent",
            "schema_key": "sales_data_schema",
        },
    },
}


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def lookup_agent(service_category: str, sub_category: str) -> AgentConfig | None:
    """Return the ``AgentConfig`` for the given routing keys, or ``None``.

    Lookup is case-sensitive; callers must normalise the keys before calling.
    """
    return (
        SERVICE_REQUEST_AGENT_REGISTRY
        .get(service_category or "", {})
        .get(sub_category or "")
    )


def list_registered_agents() -> list[AgentConfig]:
    """Return a flat list of every ``AgentConfig`` in the registry."""
    return [
        agent_cfg
        for sub_map in SERVICE_REQUEST_AGENT_REGISTRY.values()
        for agent_cfg in sub_map.values()
    ]


def is_registered(service_category: str, sub_category: str) -> bool:
    """Return ``True`` if the pair has an entry in the registry."""
    return lookup_agent(service_category, sub_category) is not None
