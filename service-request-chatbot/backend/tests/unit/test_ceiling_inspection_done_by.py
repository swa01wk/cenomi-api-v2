"""Unit tests — the ceiling inspection's "Inspection conducted by" follows the form.

The platform form shows the role id stored in its skeleton field
``inspection_done_by``; the assistant must seed, show and submit that same id
rather than assume RDD PM.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.ceiling import ceiling_entry_node as entry_mod
from app.agents.graph.nodes.ceiling.ceiling_confirmation_node import build_confirmation_card
from app.agents.graph.nodes.ceiling.ceiling_payload_builder_node import build_create_ceiling_payload
from app.agents.schemas import ceiling_schema as cs
from app.agents.services import ceiling_inspection_done_by as idb
from app.agents.services import service_request_api_service as sr_api
from app.agents.services.service_request_api_service import find_form_field
from app.types.chat import AuthContext

OCI = cs.SUB_CATEGORY_OCI
FM_MANAGER_ROLE_ID = 20

_SKELETON = {
    "data": [
        {
            "sub_category": OCI,
            "forms": {
                "skelton": {
                    "common": [
                        {"id": 1, "name": "title", "type": "text"},
                        {
                            "id": 513,
                            "name": "inspection_done_by",
                            "type": "ReadOnlyRoleField",
                            "value": FM_MANAGER_ROLE_ID,
                        },
                    ]
                }
            },
        }
    ]
}


class _FormService:
    """Stands in for the platform adapter; counts form reads."""

    def __init__(self, value: Any) -> None:
        self.value = value
        self.calls = 0

    async def get_form_field_value(
        self, service_category: str, sub_category: str, field_name: str
    ) -> Any:
        self.calls += 1
        return self.value


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    idb.clear_cache()


def _use(monkeypatch: pytest.MonkeyPatch, service: _FormService) -> None:
    monkeypatch.setattr(sr_api, "get_service_request_api_service", lambda: service)


def test_find_form_field_searches_nested_skeleton() -> None:
    assert find_form_field(_SKELETON, "inspection_done_by")["value"] == FM_MANAGER_ROLE_ID
    assert find_form_field(_SKELETON, "missing") is None
    assert find_form_field("not a form", "inspection_done_by") is None


async def test_resolves_the_forms_role_and_caches_it(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _FormService(FM_MANAGER_ROLE_ID)
    _use(monkeypatch, service)

    assert await idb.resolve_inspection_done_by(OCI) == FM_MANAGER_ROLE_ID
    assert await idb.resolve_inspection_done_by(OCI) == FM_MANAGER_ROLE_ID
    assert service.calls == 1


@pytest.mark.parametrize("value", [None, "", "FM_MANAGER", 0])
async def test_falls_back_when_the_form_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, value: Any
) -> None:
    service = _FormService(value)
    _use(monkeypatch, service)

    assert await idb.resolve_inspection_done_by(OCI) == cs.INSPECTION_DONE_BY_ROLE_ID
    # A failed read is not cached — the next turn tries again.
    await idb.resolve_inspection_done_by(OCI)
    assert service.calls == 2


async def test_unresolved_sub_category_skips_the_form(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _FormService(FM_MANAGER_ROLE_ID)
    _use(monkeypatch, service)

    assert await idb.resolve_inspection_done_by(cs.SUB_CATEGORY_UNRESOLVED) == 12
    assert await idb.resolve_inspection_done_by(None) == 12
    assert service.calls == 0


async def test_entry_seeds_the_forms_role(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fm(_sub_category: str | None) -> int:
        return FM_MANAGER_ROLE_ID

    monkeypatch.setattr(entry_mod, "resolve_inspection_done_by", _fm)
    state = {
        "active_agent": cs.AGENT_NAME,
        "intent": cs.INTENT_CREATE,
        "workflow_stage": None,
        "sub_category": None,
        "user_message": "Raise an OCI for lease t0105712",
        "auth_context": AuthContext(subject_id="u-1", roles=frozenset({"DD_ENGINEER"})),
        "backend_refs": {},
        "collected_data": {},
        "documents": [],
        "confirmation_status": None,
        "action_override": None,
    }

    result = await entry_mod.ceiling_entry_node(state)  # type: ignore[arg-type]

    assert result["collected_data"]["inspection_done_by"] == FM_MANAGER_ROLE_ID


def test_card_and_payload_carry_the_seeded_role() -> None:
    collected = {
        "lease_code": "t0105712",
        "lease_id": 95404,
        "title": "Ceiling services inspection",
        "description": "Inspect above-ceiling services",
        "startDate": "2026-10-01",
        "endDate": "2026-10-02",
        "comments": "Ready",
        "inspection_done_by": FM_MANAGER_ROLE_ID,
    }

    payload = build_create_ceiling_payload(collected, OCI)
    inner = payload.get("payload", payload)
    assert inner["inspection_done_by"] == FM_MANAGER_ROLE_ID
    assert inner["inspectionDoneBy"] == FM_MANAGER_ROLE_ID

    card = build_confirmation_card(collected, OCI)
    rows = [row for row in _walk(card) if row.get("key") == "inspection_done_by"]
    assert rows and rows[0]["value"] == FM_MANAGER_ROLE_ID


def _walk(node: Any) -> list[dict[str, Any]]:
    """Every dict inside the card, whatever its nesting."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        found.append(node)
        for child in node.values():
            found.extend(_walk(child))
    elif isinstance(node, list):
        for child in node:
            found.extend(_walk(child))
    return found
