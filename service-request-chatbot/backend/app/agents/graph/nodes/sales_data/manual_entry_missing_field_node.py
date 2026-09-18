"""Manual Entry Missing Field Node.

Checks collected_data for missing required fields and asks the user for them
one at a time.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.sales_data_schema import (
    MANUAL_ENTRY_FIELD_QUESTIONS,
    MANUAL_ENTRY_REQUIRED_FIELDS,
)
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)


@trace_node("manual_entry_missing_field", "AGENT")
async def manual_entry_missing_field_node(state: ServiceRequestState) -> dict[str, Any]:
    """Ask the user for the next missing required field."""
    collected: dict = state.get("collected_data") or {}

    for field in MANUAL_ENTRY_REQUIRED_FIELDS:
        if not collected.get(field):
            question = MANUAL_ENTRY_FIELD_QUESTIONS.get(
                field, f"Please provide your {field}."
            )
            log.info("manual_entry_missing_field.asking", field=field)
            return {
                "active_field": field,
                "status": "WAITING_FOR_USER",
                "response_message": question,
                "response_ui": {
                    "type": "text_prompt",
                    "field": field,
                    "message": question,
                },
            }

    # All fields present — should not reach here in normal flow
    log.info("manual_entry_missing_field.all_collected")
    return {"status": "IN_PROGRESS"}
