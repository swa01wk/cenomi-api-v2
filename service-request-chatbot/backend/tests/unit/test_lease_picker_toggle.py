"""Unit tests — the eligible-lease browse picker and its kill switch.

The first version of this picker was disabled: asked for a lease code, the
Handover CREATE flow answered with leases the user had not asked about
("Careem (t002134)", "Load more leases"), which is noise in front of a question
they can simply answer. It is back on by default in a form that narrows rather
than pads — the 5 most recently activated eligible leases, a search box, and a
read-more pager. HANDOVER_LEASE_PICKER_ENABLED=false still turns it off without
a code change, falling back to the plain lease-code question.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.graph.nodes.shared.lease_lookup_node import lease_lookup_node
from app.agents.services.resource_picker_decorations import (
    build_handover_inbox_picker,
    should_browse_leases,
)
from app.core.config import Settings, settings


def _state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "active_agent": "rdd_handover_agent",
        "workflow_stage": "CREATE_SR",
        "collected_data": {},
    }
    state.update(overrides)
    return state


class TestLeasePickerToggle:
    def test_enabled_by_default(self) -> None:
        assert settings.handover_lease_picker_enabled is True
        assert should_browse_leases(_state()) is True

    def test_can_be_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "handover_lease_picker_enabled", False)
        assert should_browse_leases(_state()) is False

    @pytest.mark.parametrize("blank", ["", " ", "   "])
    def test_blank_env_value_keeps_the_default(self, blank: str) -> None:
        """Key Vault stores "empty" as a space — that must not read as false."""
        built = Settings(  # type: ignore[call-arg]
            _env_file=None,
            DATABASE_URL="postgresql+asyncpg://u:p@h:5432/d",
            HANDOVER_LEASE_PICKER_ENABLED=blank,
        )
        assert built.handover_lease_picker_enabled is True

    def test_explicit_false_env_value_disables(self) -> None:
        built = Settings(  # type: ignore[call-arg]
            _env_file=None,
            DATABASE_URL="postgresql+asyncpg://u:p@h:5432/d",
            HANDOVER_LEASE_PICKER_ENABLED="false",
        )
        assert built.handover_lease_picker_enabled is False

    def test_a_workflow_without_a_lease_list_is_still_excluded(self) -> None:
        """Re-enabling must not widen the picker to workflows with no list.

        The three fitout confirmations now have their own — each scoped by
        ``purpose`` to the leases holding its prerequisite milestone. Work Permit
        create shares Handover's eligible-lease list. An unknown session still
        must not be offered handover leases.
        """
        assert should_browse_leases(_state(active_agent="notification_agent")) is False
        assert should_browse_leases(_state(active_agent="")) is False

    def test_resolved_lease_still_excluded(self) -> None:
        assert should_browse_leases(_state(collected_data={"lease_id": 9001})) is False

    def test_reviewer_inbox_picker_is_unaffected(self) -> None:
        """Only the lease picker is gated — the pending-SR inbox picker stays."""
        inbox = build_handover_inbox_picker()
        assert inbox["suggestions"]["kind"] == "resource_picker"
        assert inbox["suggestions"]["resource"] == "service_requests"


class TestFlowOffersThePicker:
    @pytest.mark.asyncio
    async def test_no_identifiers_offers_picker(self) -> None:
        service = MagicMock()
        service.lookup = AsyncMock()
        result = await lease_lookup_node(_state(), service=service)  # type: ignore[arg-type]

        spec = result["response_decorations"]["suggestions"]
        assert result["status"] == "IN_PROGRESS"
        assert spec["resource"] == "leases"
        assert spec["filters"]["limit"] == 5
        assert spec["filters"]["sort_by"] == "activation_date"
        assert spec["searchable"] is True
        # No identifiers to look up — the chips come from the platform list
        # endpoint the frontend calls, not from a lease lookup here.
        assert service.lookup.called is False

    @pytest.mark.asyncio
    async def test_asks_as_plain_text_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "handover_lease_picker_enabled", False)
        service = MagicMock()
        service.lookup = AsyncMock()
        result = await lease_lookup_node(_state(), service=service)  # type: ignore[arg-type]

        assert result["status"] == "WAITING_FOR_USER"
        assert result["response_ui"]["field"] == "lease_code"
        assert result.get("response_decorations") is None
        assert service.lookup.called is False
