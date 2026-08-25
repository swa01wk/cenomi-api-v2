"""Unit tests — OPEN_NOTIFICATION seeding (orchestration helper)."""

from __future__ import annotations

from app.services.chat_orchestration_service import build_open_notification_seed


def test_handover_seeds_rdd_handover_agent() -> None:
    seed = build_open_notification_seed(
        {"id": "11711", "sub_category": "HANDOVER"},
        session_active_agent=None,
    )
    assert seed == {
        "intent": "OPEN_NOTIFICATION",
        "active_agent": "rdd_handover_agent",
        "service_category": "FIT_OUT_AND_HANDOVER",
        "sub_category": "HANDOVER",
    }


def test_fitout_sub_categories_map_to_fitout_agents() -> None:
    expected = {
        "FIT_OUT_CONFIRM_FOS": "rdd_fitout_start_agent",
        "FIT_OUT_CONFIRM_END": "rdd_fitout_end_agent",
        "FIT_OUT_CONFIRM_TRADING": "rdd_trading_confirmation_agent",
    }
    for sub, agent in expected.items():
        seed = build_open_notification_seed(
            {"id": "1", "sub_category": sub}, session_active_agent=None
        )
        assert seed["active_agent"] == agent
        assert seed["sub_category"] == sub


def test_sub_category_case_insensitive() -> None:
    seed = build_open_notification_seed(
        {"id": "1", "sub_category": "handover"}, session_active_agent=None
    )
    assert seed["active_agent"] == "rdd_handover_agent"


def test_unmapped_ho_seeds_intent_only() -> None:
    """FIT_OUT_CONFIRM_HO has no chatbot agent — sr_status_sync re-derives."""
    seed = build_open_notification_seed(
        {"id": "11720", "sub_category": "FIT_OUT_CONFIRM_HO"},
        session_active_agent=None,
    )
    assert seed == {"intent": "OPEN_NOTIFICATION"}


def test_missing_sub_category_seeds_intent_only() -> None:
    seed = build_open_notification_seed({"id": "11711"}, session_active_agent=None)
    assert seed == {"intent": "OPEN_NOTIFICATION"}


def test_live_session_agent_never_clobbered() -> None:
    seed = build_open_notification_seed(
        {"id": "11711", "sub_category": "HANDOVER"},
        session_active_agent="work_permit_agent",
    )
    assert "active_agent" not in seed
    assert seed["intent"] == "OPEN_NOTIFICATION"


def test_process_turn_signature_parity() -> None:
    """Every param of _process_turn_serialised must be declared on process_turn.

    Regression guard: the viewing_* params were once added to process_turn but
    not forwarded, producing a NameError inside _process_turn_serialised.
    """
    import inspect

    from app.services.chat_orchestration_service import ChatOrchestrationService

    outer = set(inspect.signature(ChatOrchestrationService.process_turn).parameters)
    inner = set(
        inspect.signature(ChatOrchestrationService._process_turn_serialised).parameters
    )
    assert inner == outer
