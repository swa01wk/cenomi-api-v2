"""Unit tests — notification viewing-context intent classification."""

from __future__ import annotations

import pytest

from app.agents.services.notification_intent import (
    classify_notification_intent,
    classify_notification_intent_from_state,
    is_list_notifications_message,
    should_bypass_for_notification_context,
)
from app.agents.services.viewing_context import (
    extract_notification_id,
    references_viewing_notification,
    resolve_notification_id,
    resolve_service_request_id,
    viewing_ids_from_state,
)


@pytest.mark.parametrize(
    "message,expected",
    [
        ("show me NTF-000012", "NTF-000012"),
        ("Explain NTF-12", "NTF-000012"),
        ("open notification NTF0009", "NTF-000009"),
        ("notif-000003", "NTF-000003"),
    ],
)
def test_extract_notification_id(message: str, expected: str) -> None:
    assert extract_notification_id(message) == expected
    assert classify_notification_intent(message) == "GET_NOTIFICATION_DETAIL"


def test_list_messages_classified_as_list() -> None:
    for message in (
        "Show my notifications",
        "List all notifications",
        "Show my inbox",
        "Any unread notifications?",
    ):
        assert is_list_notifications_message(message)
        assert classify_notification_intent(message) == "LIST_NOTIFICATIONS"


def test_list_does_not_match_explicit_id() -> None:
    message = "show me NTF-000012"
    assert not is_list_notifications_message(message)
    assert classify_notification_intent(message) == "GET_NOTIFICATION_DETAIL"


def test_viewing_context_detail_without_explicit_id() -> None:
    message = "Can you explain this notification?"
    viewing_id = "NTF-000012"
    assert classify_notification_intent(message, viewing_id) == "GET_NOTIFICATION_DETAIL"
    assert resolve_notification_id(message, viewing_id) == viewing_id


def test_viewing_context_explain_this() -> None:
    assert (
        classify_notification_intent("explain this", "NTF-000012")
        == "GET_NOTIFICATION_DETAIL"
    )


def test_viewing_context_mark_read_without_explicit_id() -> None:
    message = "Please mark this as read"
    viewing_id = "NTF-000012"
    assert classify_notification_intent(message, viewing_id) == "MARK_NOTIFICATION_READ"
    assert resolve_notification_id(message, viewing_id) == viewing_id


def test_explicit_id_wins_over_viewing_context() -> None:
    message = "explain NTF-000099"
    viewing_id = "NTF-000012"
    assert resolve_notification_id(message, viewing_id) == "NTF-000099"
    assert classify_notification_intent(message, viewing_id) == "GET_NOTIFICATION_DETAIL"


def test_viewing_context_not_used_for_unrelated_message() -> None:
    assert classify_notification_intent("What malls are available?", "NTF-000012") is None
    assert not references_viewing_notification("What malls are available?")
    assert resolve_notification_id("What malls are available?", "NTF-000012") is None


def test_empty_message_with_viewing_id_opens_detail() -> None:
    assert classify_notification_intent("", "NTF-000012") == "GET_NOTIFICATION_DETAIL"
    assert resolve_notification_id("", "NTF-000012") == "NTF-000012"


def test_action_override_mark_read() -> None:
    state = {
        "user_message": "",
        "action_override": "mark_notification_read",
        "viewing_notification_id": "NTF-000012",
    }
    assert classify_notification_intent_from_state(state) == "MARK_NOTIFICATION_READ"


def test_bypass_when_mid_handover_and_viewing_notification() -> None:
    state = {
        "active_agent": "rdd_handover_agent",
        "user_message": "explain this notification",
        "viewing_notification_id": "NTF-000012",
    }
    assert should_bypass_for_notification_context(state) is True


def test_no_bypass_for_ordinary_handover_followup() -> None:
    state = {
        "active_agent": "rdd_handover_agent",
        "user_message": "What's next?",
    }
    assert should_bypass_for_notification_context(state) is False


def test_viewing_ids_from_msp_context_and_snapshot() -> None:
    state = {
        "msp_context": {
            "viewing_notification_id": "NTF-1",
            "viewing_notification": {
                "id": "NTF-000001",
                "related_sr_id": "11711",
                "title": "FM review ready",
            },
        }
    }
    notif_id, sr_id = viewing_ids_from_state(state)
    assert notif_id == "NTF-1"
    assert sr_id == "11711"


def test_status_question_resolves_viewing_sr() -> None:
    assert (
        resolve_service_request_id("What's the status?", "11711") == "11711"
    )
