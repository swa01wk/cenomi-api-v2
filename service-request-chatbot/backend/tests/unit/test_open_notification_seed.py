"""Unit tests — OPEN_NOTIFICATION seeding (open_notification_node)."""

from __future__ import annotations

import pytest

from app.agents.graph.nodes.notification.open_notification_node import (
    SUB_CATEGORY_TO_AGENT,
    build_open_notification_seed,
    open_notification_node,
)


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


def test_work_permit_sub_categories_map_to_work_permit_agent() -> None:
    wp_subs = (
        "CONSTRUCTION_HOT",
        "CONSTRUCTION_COLD",
        "CONSTRUCTION_ROOF_ACCESS",
        "MAINTENANCE_HOT",
        "MAINTENANCE_COLD",
        "MAINTENANCE_ROOF_ACCESS",
        "OPERATIONS",
    )
    for sub in wp_subs:
        seed = build_open_notification_seed(
            {"id": "12801", "sub_category": sub}, session_active_agent=None
        )
        assert seed == {
            "intent": "OPEN_NOTIFICATION",
            "active_agent": "work_permit_agent",
            "service_category": "WORK_PERMIT",
            "sub_category": sub,
        }


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


def test_sub_category_to_agent_registry_backed() -> None:
    """Spec export exists, is registry-backed, and excludes decommissioned HO."""
    assert SUB_CATEGORY_TO_AGENT["HANDOVER"] == "rdd_handover_agent"
    assert SUB_CATEGORY_TO_AGENT["FIT_OUT_CONFIRM_FOS"] == "rdd_fitout_start_agent"
    assert SUB_CATEGORY_TO_AGENT["FIT_OUT_CONFIRM_END"] == "rdd_fitout_end_agent"
    assert SUB_CATEGORY_TO_AGENT["FIT_OUT_CONFIRM_TRADING"] == "rdd_trading_confirmation_agent"
    assert SUB_CATEGORY_TO_AGENT["SALES_DATA"] == "sales_data_agent"
    assert SUB_CATEGORY_TO_AGENT["CONSTRUCTION_HOT"] == "work_permit_agent"
    assert SUB_CATEGORY_TO_AGENT["OPERATIONS"] == "work_permit_agent"
    assert "FIT_OUT_CONFIRM_HO" not in SUB_CATEGORY_TO_AGENT


@pytest.mark.asyncio
async def test_node_seeds_backend_refs_and_agent() -> None:
    updates = await open_notification_node(
        {
            "active_notification_id": {"id": "11711", "sub_category": "HANDOVER"},
            "backend_refs": {"user_role": "FM_MANAGER"},
        }
    )
    assert updates["backend_refs"]["sr_id"] == "11711"
    assert updates["backend_refs"]["opened_from_notification"] is True
    assert updates["backend_refs"]["user_role"] == "FM_MANAGER"
    assert updates["active_agent"] == "rdd_handover_agent"
    assert updates["intent"] == "OPEN_NOTIFICATION"


@pytest.mark.asyncio
async def test_node_accepts_bare_string_ref() -> None:
    """Reconciled-spec fallback: a bare string is the SR id."""
    updates = await open_notification_node(
        {"active_notification_id": "320971", "backend_refs": {}}
    )
    assert updates["backend_refs"]["sr_id"] == "320971"
    assert "active_agent" not in updates


@pytest.mark.asyncio
async def test_node_missing_ref_returns_error_turn() -> None:
    updates = await open_notification_node({"backend_refs": {}})
    assert "backend_refs" not in updates
    assert updates["response_ui"]["type"] == "message"
    assert "service request" in updates["response_message"].lower() or "lease" in updates[
        "response_message"
    ].lower()


@pytest.mark.asyncio
async def test_sales_data_open_seeds_notification_context() -> None:
    updates = await open_notification_node(
        {
            "active_notification_id": {
                "id": "LC-1001",
                "sub_category": "SALES_DATA",
                "message": "Please submit sales data as of month August for Brand@Mall.",
                "key": "SALES_DATA_PENDING",
            },
            "backend_refs": {},
        }
    )
    assert updates["active_agent"] == "sales_data_agent"
    assert updates["intent"] == "UPLOAD_SALES_DATA"
    assert updates["service_category"] == "SALES_DATA"
    assert "sr_id" not in updates["backend_refs"]
    ctx = updates["backend_refs"]["notification_context"]
    assert ctx["lease_code"] == "LC-1001"
    assert ctx["period_hint"] == "month August"
    assert ctx["source"] == "notification"
    assert updates["corrected_fields"]["notification_context"] == ctx


def test_sales_data_pending_key_maps_to_upload() -> None:
    seed = build_open_notification_seed(
        {"id": "LC-1001", "sub_category": "SALES_DATA_PENDING"},
        session_active_agent=None,
    )
    assert seed["active_agent"] == "sales_data_agent"
    assert seed["intent"] == "UPLOAD_SALES_DATA"


def test_sales_data_supporting_doc_key_maps_to_doc_intent() -> None:
    seed = build_open_notification_seed(
        {
            "id": "LC-1001",
            "sub_category": "SALES_DATA",
            "key": "SALES_DATA_NEED_UPLOAD_SUPPORTING_DOCUMENT",
        },
        session_active_agent=None,
    )
    assert seed["intent"] == "UPLOAD_SUPPORTING_DOCUMENT"


def test_sales_data_submitted_maps_to_admin_review() -> None:
    seed = build_open_notification_seed(
        {"id": "LC-1001", "sub_category": "SALES_DATA_SUBMITTED"},
        session_active_agent=None,
    )
    assert seed["intent"] == "REVIEW_SALES_DATA"


def test_sales_data_open_sets_agent_even_if_session_has_handover() -> None:
    seed = build_open_notification_seed(
        {"id": "LC-1001", "sub_category": "SALES_DATA"},
        session_active_agent="rdd_handover_agent",
    )
    assert seed["active_agent"] == "sales_data_agent"
