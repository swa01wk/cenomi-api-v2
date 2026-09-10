"""Load session + draft state from DB at the start of each graph turn.

Reads ``conversation_state_service`` from runtime state (injected by the API
layer before graph invocation).  Returns ``{}`` gracefully when the service is
absent or the session has no prior draft — first-turn behaviour is unaffected.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.services.conversation_state_service import StateLoadError
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)


@trace_node("load_session", "CHAIN")
async def load_session_node(state: ServiceRequestState) -> dict[str, Any]:
    state_service = state.get("conversation_state_service")  # type: ignore[typeddict-item]
    if state_service is None:
        return {}

    session_id = state.get("session_id")
    if not session_id:
        return {}

    try:
        loaded: dict[str, Any] = await state_service.load(UUID(str(session_id)))
        if loaded:
            log.debug(
                "load_session_node.state_restored",
                session_id=session_id,
                keys=list(loaded.keys()),
            )
        # ``missing_fields`` is recomputed each turn by ``missing_field_node``
        # (and by ``_route_after_validation`` for routing purposes).  Carrying a
        # stale list across turns misleads ``field_extraction`` (which uses it as
        # an LLM hint) and ``response_generation`` (which includes it in the LLM
        # context).  Reset it here so every turn starts with an empty list.
        loaded.pop("missing_fields", None)
        # ``faq_sources`` are per-turn RAG citations set by ``faq_node``.
        # Resetting prevents stale citations from a FAQ turn leaking into a
        # subsequent SR workflow turn's API response.
        loaded.pop("faq_sources", None)

        # Session isolation: if the loaded session has no active workflow
        # (workflow_stage is None or CANCELLED), clear transient per-turn state
        # so a new request always starts clean without stale field leakage.
        workflow_stage = loaded.get("workflow_stage")
        if workflow_stage in (None, "CANCELLED"):
            for transient_key in (
                "active_field",
                "extracted_fields",
                "corrected_fields",
                "validation_errors",
                "response_ui",
            ):
                loaded.pop(transient_key, None)
            log.debug(
                "load_session_node.transient_state_cleared",
                session_id=session_id,
                workflow_stage=workflow_stage,
            )

        return loaded
    except StateLoadError:
        # Propagate: continuing with {} makes the turn look like a brand new
        # conversation, and save_state_node would then persist that emptiness
        # over the user's real draft. Aborting the turn is recoverable.
        raise
    except Exception:
        log.exception("load_session_node.failed", session_id=session_id)
        return {}
