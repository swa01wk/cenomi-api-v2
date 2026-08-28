"""Unit tests — compact LLM context builder."""

from __future__ import annotations

from app.agents.services.context_builder import (
    MAX_RECENT_MESSAGES,
    build_agent_context,
    format_context_for_prompt,
)


def test_context_excludes_full_history() -> None:
    history = [{"role": "user", "content": f"message {i}"} for i in range(20)]
    state = {
        "user_message": "explain this",
        "conversation_history": history,
        "collected_data": {"brand": "Nike", "mall": "Riyadh Park"},
        "missing_fields": ["startDate"],
        "workflow_stage": "CREATE_SR",
        "backend_refs": {"sr_id": "11711"},
        "viewing_notification_id": "NTF-000012",
    }
    context = build_agent_context(state)
    assert len(context["recent_messages"]) <= MAX_RECENT_MESSAGES
    assert context["authoritative_state"]["brand"] == "Nike"
    assert context["authoritative_state"]["sr_id"] == "11711"
    assert context["viewing_notification_id"] == "NTF-000012"
    assert "events" not in context
    rendered = format_context_for_prompt(context)
    assert "Viewing notification ID: NTF-000012" in rendered
    assert "message 19" in rendered
    assert "message 0" not in rendered


def test_context_includes_notification_snapshot() -> None:
    state = {
        "user_message": "what's this about",
        "viewing_notification": {
            "id": "NTF-000012",
            "title": "Handover awaiting FM review",
            "body": "SR 11711 is ready for FM review.",
            "related_sr_id": "11711",
        },
    }
    context = build_agent_context(state)
    assert context["viewing_notification"]["title"] == "Handover awaiting FM review"
    assert "related_sr_id: 11711" in format_context_for_prompt(context)
