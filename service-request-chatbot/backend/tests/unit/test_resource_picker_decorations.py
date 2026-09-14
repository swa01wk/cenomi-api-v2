"""Unit tests for resource_picker_decorations."""

from __future__ import annotations

from app.agents.services.resource_picker_decorations import (
    build_create_lease_picker,
    build_handover_inbox_picker,
    build_resource_picker,
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


def test_should_browse_leases_create_sr_unresolved(monkeypatch) -> None:
    """The stage/lease conditions still hold — but only when the picker is
    enabled. It is off by default now (HANDOVER_LEASE_PICKER_ENABLED): the
    browse chips listed leases the user had not asked for, in front of a
    question they can simply answer."""
    state = {"workflow_stage": "CREATE_SR", "collected_data": {}}
    assert should_browse_leases(state) is False

    monkeypatch.setattr(settings, "handover_lease_picker_enabled", True)
    assert should_browse_leases(state) is True


def test_should_browse_leases_defaults_stage_to_create_sr(monkeypatch) -> None:
    assert should_browse_leases({"collected_data": {}}) is False

    monkeypatch.setattr(settings, "handover_lease_picker_enabled", True)
    assert should_browse_leases({"collected_data": {}}) is True


def test_should_browse_leases_resolved_lease() -> None:
    state = {"workflow_stage": "CREATE_SR", "collected_data": {"lease_id": 1}}
    assert should_browse_leases(state) is False


def test_should_browse_leases_work_permit_stage() -> None:
    state = {"workflow_stage": "CREATE_WORK_PERMIT", "collected_data": {}}
    assert should_browse_leases(state) is False
