"""Unit tests for resource_picker_decorations."""

from __future__ import annotations

from app.agents.services.resource_picker_decorations import (
    build_create_lease_picker,
    build_handover_inbox_picker,
    build_resource_picker,
    lease_picker_purpose,
    should_browse_leases,
)
from app.core.config import settings


def test_build_resource_picker_shape() -> None:
    deco = build_resource_picker("leases", filters={"limit": 10, "offset": 0})
    spec = deco["suggestions"]
    assert spec["kind"] == "resource_picker"
    assert spec["resource"] == "leases"
    assert spec["filters"]["limit"] == 10


def test_build_handover_inbox_picker() -> None:
    deco = build_handover_inbox_picker()
    spec = deco["suggestions"]
    assert spec["resource"] == "service_requests"
    assert spec["filters"]["sub_category"] == "HANDOVER"
    assert spec["filters"]["self"] is True


def test_build_create_lease_picker() -> None:
    deco = build_create_lease_picker()
    spec = deco["suggestions"]
    assert spec["resource"] == "leases"
    assert spec["selection"]["mode"] == "select_lease"


def test_create_lease_picker_asks_for_five_latest_activated() -> None:
    """The picker leads with the 5 most recently activated eligible leases."""
    spec = build_create_lease_picker()["suggestions"]
    assert spec["filters"] == {
        "limit": 5,
        "offset": 0,
        "sort_by": "activation_date",
        "order": "desc",
    }


def test_create_lease_picker_is_searchable() -> None:
    """Search box is opt-in per resource — the SR inbox picker must not get one."""
    assert build_create_lease_picker()["suggestions"]["searchable"] is True
    assert "searchable" not in build_handover_inbox_picker()["suggestions"]


def test_build_resource_picker_keeps_non_int_filter_values() -> None:
    """Filters carry strings (sort_by/order), not just ints."""
    spec = build_resource_picker(
        "leases", filters={"limit": 5, "order": "desc"}
    )["suggestions"]
    assert spec["filters"]["order"] == "desc"


def test_should_browse_leases_create_sr_unresolved(monkeypatch) -> None:
    """On by default again (HANDOVER_LEASE_PICKER_ENABLED); the flag still
    turns it off without a code change."""
    state = {
        "active_agent": "rdd_handover_agent",
        "workflow_stage": "CREATE_SR",
        "collected_data": {},
    }
    assert should_browse_leases(state) is True

    monkeypatch.setattr(settings, "handover_lease_picker_enabled", False)
    assert should_browse_leases(state) is False


def test_should_browse_leases_defaults_stage_to_create_sr(monkeypatch) -> None:
    state = {"active_agent": "rdd_handover_agent", "collected_data": {}}
    assert should_browse_leases(state) is True

    monkeypatch.setattr(settings, "handover_lease_picker_enabled", False)
    assert should_browse_leases(state) is False


def test_should_browse_leases_needs_a_known_handover_session() -> None:
    """An unpinned turn must not be offered handover leases.

    It is how a Fitout Start Confirmation came to show them: no agent on the
    turn, the picker went out anyway, and picking a lease carried the session
    into handover.
    """
    assert should_browse_leases({"collected_data": {}}) is False
    assert should_browse_leases({"active_agent": "", "collected_data": {}}) is False
    assert should_browse_leases({"active_agent": None, "collected_data": {}}) is False


def test_each_confirmation_agent_browses_its_own_leases() -> None:
    """Same picker as handover's, scoped to the workflow's candidate leases."""
    for agent in (
        "rdd_fitout_start_agent",
        "rdd_fitout_end_agent",
        "rdd_trading_confirmation_agent",
    ):
        state = {"active_agent": agent, "collected_data": {}}
        assert should_browse_leases(state) is True, agent


def test_should_browse_leases_not_for_a_workflow_without_a_list() -> None:
    state = {"active_agent": "work_permit_agent", "collected_data": {}}
    assert should_browse_leases(state) is False


def test_lease_picker_purpose_per_agent() -> None:
    """Each confirmation confirms the next milestone, so it lists the leases
    already holding the one before it."""
    assert (
        lease_picker_purpose({"active_agent": "rdd_fitout_start_agent"})
        == "fitout_confirm"
    )
    assert lease_picker_purpose({"active_agent": "rdd_fitout_end_agent"}) == "fitout_end"
    assert (
        lease_picker_purpose({"active_agent": "rdd_trading_confirmation_agent"})
        == "trading_confirm"
    )
    # Handover keeps the handover-eligibility rules — no purpose.
    assert lease_picker_purpose({"active_agent": "rdd_handover_agent"}) is None
    assert lease_picker_purpose({}) is None


def test_the_scoped_picker_is_the_handover_one_plus_a_purpose() -> None:
    """Same kind, resource, page size, search box and selection mode."""
    handover = build_create_lease_picker()["suggestions"]
    scoped = build_create_lease_picker("fitout_end")["suggestions"]

    assert scoped["kind"] == handover["kind"] == "resource_picker"
    assert scoped["resource"] == handover["resource"] == "leases"
    assert scoped["searchable"] is handover["searchable"] is True
    assert scoped["selection"] == handover["selection"] == {"mode": "select_lease"}
    assert scoped["filters"] == {**handover["filters"], "purpose": "fitout_end"}


def test_the_handover_picker_carries_no_purpose() -> None:
    assert "purpose" not in build_create_lease_picker()["suggestions"]["filters"]


def test_should_browse_leases_resolved_lease() -> None:
    state = {
        "active_agent": "rdd_handover_agent",
        "workflow_stage": "CREATE_SR",
        "collected_data": {"lease_id": 1},
    }
    assert should_browse_leases(state) is False


def test_should_browse_leases_work_permit_stage() -> None:
    state = {"workflow_stage": "CREATE_WORK_PERMIT", "collected_data": {}}
    assert should_browse_leases(state) is False
