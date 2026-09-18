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


# ---------------------------------------------------------------------------
# NL list filters
# ---------------------------------------------------------------------------


from app.agents.services.notification_intent import parse_notification_list_filters


@pytest.mark.parametrize(
    "message,subs,unread",
    [
        ("show my notifications", None, None),
        ("show unread notifications", None, True),
        ("any new notifications?", None, True),
        ("show handover notifications", ("HANDOVER",), None),
        ("list my FOS notifications", ("FIT_OUT_CONFIRM_FOS",), None),
        ("fit-out start notifications", ("FIT_OUT_CONFIRM_FOS",), None),
        ("fit out end notifications", ("FIT_OUT_CONFIRM_END",), None),
        ("show trading notifications", ("FIT_OUT_CONFIRM_TRADING",), None),
        # FIT_OUT_CONFIRM_HO decommissioned — this now reads as plain handover.
        ("handover confirmation notifications", ("HANDOVER",), None),
        (
            "unread handover and trading notifications",
            ("HANDOVER", "FIT_OUT_CONFIRM_TRADING"),
            True,
        ),
        ("show sales data notifications", ("SALES_DATA",), None),
        ("unread sales-data alerts", ("SALES_DATA",), True),
    ],
)
def test_parse_notification_list_filters(message, subs, unread) -> None:
    filters = parse_notification_list_filters(message)
    assert filters.sub_categories == subs
    assert filters.unread == unread


def test_filters_empty_message() -> None:
    filters = parse_notification_list_filters("")
    assert filters.sub_categories is None
    assert filters.unread is None


# ---------------------------------------------------------------------------
# Seeded OPEN_NOTIFICATION turns bypass the notification side-path
# ---------------------------------------------------------------------------


def test_seeded_open_turn_not_classified() -> None:
    state = {
        "user_message": "",
        "action_override": "open_notification",
        "backend_refs": {"sr_id": "11711", "opened_from_notification": True},
        "viewing_notification_id": "NTF-000012",
    }
    assert classify_notification_intent_from_state(state) is None
    assert should_bypass_for_notification_context(state) is False


def test_legacy_open_action_without_seed_still_detail() -> None:
    state = {
        "user_message": "",
        "action_override": "open_notification",
        "backend_refs": {},
        "viewing_notification_id": "NTF-000012",
    }
    assert classify_notification_intent_from_state(state) == "GET_NOTIFICATION_DETAIL"


@pytest.mark.parametrize(
    "message,expected",
    [
        # Copilot chip messages (TC-3662)
        ("Show my handover notifications", ("HANDOVER",)),
        (
            "Show my fit-out notifications",
            ("FIT_OUT_CONFIRM_FOS", "FIT_OUT_CONFIRM_END"),
        ),
        ("Show my trading notifications", ("FIT_OUT_CONFIRM_TRADING",)),
        # A stage keyword still narrows to that one flow.
        ("fit-out start notifications", ("FIT_OUT_CONFIRM_FOS",)),
        ("fit out end notifications", ("FIT_OUT_CONFIRM_END",)),
    ],
)
def test_copilot_chip_messages_map_to_filters(message, expected) -> None:
    filters = parse_notification_list_filters(message)
    assert filters.sub_categories == expected


@pytest.mark.parametrize(
    "message",
    [
        "Show my handover notifications",
        "Show my fit-out notifications",
        "Show my trading notifications",
    ],
)
def test_copilot_chip_messages_are_list_intent(message: str) -> None:
    assert is_list_notifications_message(message)
    assert classify_notification_intent(message) == "LIST_NOTIFICATIONS"
