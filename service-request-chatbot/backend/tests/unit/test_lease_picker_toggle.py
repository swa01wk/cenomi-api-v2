"""Unit tests — the eligible-lease browse picker is off by default.

Asked for a lease code, the Handover CREATE flow used to answer with a list of
leases the user had not asked about ("Careem (t002134)", "Load more leases"),
which is noise in front of a question they can simply answer. The picker is now
gated behind HANDOVER_LEASE_PICKER_ENABLED, default off, so the flow asks for
the lease code as plain text.
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
    def test_disabled_by_default(self) -> None:
        assert settings.handover_lease_picker_enabled is False
        assert should_browse_leases(_state()) is False

    def test_can_be_re_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "handover_lease_picker_enabled", True)
        assert should_browse_leases(_state()) is True

    @pytest.mark.parametrize("blank", ["", " ", "   "])
    def test_blank_env_value_stays_disabled(self, blank: str) -> None:
        """Key Vault stores "empty" as a space — that must not enable it."""
        built = Settings(  # type: ignore[call-arg]
            _env_file=None,
            DATABASE_URL="postgresql+asyncpg://u:p@h:5432/d",
            HANDOVER_LEASE_PICKER_ENABLED=blank,
        )
        assert built.handover_lease_picker_enabled is False

    def test_explicit_true_env_value_enables(self) -> None:
        built = Settings(  # type: ignore[call-arg]
            _env_file=None,
            DATABASE_URL="postgresql+asyncpg://u:p@h:5432/d",
            HANDOVER_LEASE_PICKER_ENABLED="true",
        )
        assert built.handover_lease_picker_enabled is True

    def test_other_agents_still_excluded_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The flag must not widen the picker to fitout / work-permit agents."""
        monkeypatch.setattr(settings, "handover_lease_picker_enabled", True)
        assert should_browse_leases(_state(active_agent="rdd_fitout_start_agent")) is False

    def test_resolved_lease_still_excluded_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "handover_lease_picker_enabled", True)
        assert should_browse_leases(_state(collected_data={"lease_id": 9001})) is False

    def test_reviewer_inbox_picker_is_unaffected(self) -> None:
        """Only the lease picker is gated — the pending-SR inbox picker stays."""
        inbox = build_handover_inbox_picker()
        assert inbox["suggestions"]["kind"] == "resource_picker"
        assert inbox["suggestions"]["resource"] == "service_requests"


class TestFlowAsksForLeaseCodeInstead:
    @pytest.mark.asyncio
    async def test_no_identifiers_asks_as_plain_text(self) -> None:
        service = MagicMock()
        service.lookup = AsyncMock()
        result = await lease_lookup_node(_state(), service=service)  # type: ignore[arg-type]

        assert result["status"] == "WAITING_FOR_USER"
        assert result["response_ui"]["field"] == "lease_code"
        assert result.get("response_decorations") is None
        assert service.lookup.called is False

    @pytest.mark.asyncio
    async def test_picker_returned_when_explicitly_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "handover_lease_picker_enabled", True)
        service = MagicMock()
        service.lookup = AsyncMock()
        result = await lease_lookup_node(_state(), service=service)  # type: ignore[arg-type]

        assert result["status"] == "IN_PROGRESS"
        assert result["response_decorations"]["suggestions"]["resource"] == "leases"
