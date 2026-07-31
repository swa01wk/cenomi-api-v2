"""Unit tests for fitout_confirm_payload_builder_node (C2).

Coverage
--------
TestBuildCreateFosFitoutPayload    — FOS yes/no branch payload shape
TestBuildCreateEndFitoutPayload    — END yes/no branch payload shape
TestBuildCreateTradingPayload      — TRADING yes/no branch + no documents_ids
TestBuildApprovePayload            — PATCH body: status=APPROVED + service_request_id
TestDateFormatting                 — YYYY-MM-DD normalisation
TestPayloadBuilderNode             — fitout_confirm_payload_builder_node graph node
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.graph.nodes.fitout_confirm.fitout_confirm_payload_builder_node import (
    build_create_fitout_confirm_payload,
    build_approve_fitout_confirm_payload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_COLLECTED: dict[str, Any] = {
    "lease_brand_mall": "t0105712 - Brand Under Armour - Jawharat Jeddah",
    "lease_code": "t0105712",
    "lease_id": 95404,
    "pms_tenant_code": "t0105712",
    "company_name": "116",
    "brand_name": "Brand Under Armour",
    "mall_name": "Jawharat Jeddah",
    "base_fitout_start_date": "2026-05-14",
    "base_fitout_end_date": "2026-06-01",
    "base_trading_date": "2026-06-15",
    "actual_handover_date_readonly": "2026-05-12",
}


# ---------------------------------------------------------------------------
# TestBuildCreateFosFitoutPayload
# ---------------------------------------------------------------------------


class TestBuildCreateFosFitoutPayload:
    def _collected_yes(self) -> dict[str, Any]:
        return {
            **_BASE_COLLECTED,
            "fitout_start_done": "yes",
            "actual_fitout_start_date": "2026-05-14",
        }

    def _collected_no(self) -> dict[str, Any]:
        return {
            **_BASE_COLLECTED,
            "fitout_start_done": "no",
            "planned_fitout_start_date": "2026-05-20",
            "planned_fitout_end_date": "2026-06-10",
            "planned_trading_date": "2026-06-25",
            "delay_reason": "Material procurement delays",
        }

    def test_yes_branch_actual_date_in_payload(self) -> None:
        payload = build_create_fitout_confirm_payload(self._collected_yes(), "FIT_OUT_CONFIRM_FOS")
        assert payload["payload"]["actual_fitout_start_date"] == "2026-05-14"

    def test_yes_branch_planned_fields_empty(self) -> None:
        payload = build_create_fitout_confirm_payload(self._collected_yes(), "FIT_OUT_CONFIRM_FOS")
        inner = payload["payload"]
        assert inner["planned_fitout_start_date"] == ""
        assert inner["planned_fitout_end_date"] == ""
        assert inner["planned_trading_date"] == ""

    def test_yes_branch_delay_reason_empty(self) -> None:
        payload = build_create_fitout_confirm_payload(self._collected_yes(), "FIT_OUT_CONFIRM_FOS")
        assert payload["payload"]["delay_reason"] == ""

    def test_no_branch_planned_dates_present(self) -> None:
        payload = build_create_fitout_confirm_payload(self._collected_no(), "FIT_OUT_CONFIRM_FOS")
        inner = payload["payload"]
        assert inner["planned_fitout_start_date"] == "2026-05-20"
        assert inner["planned_fitout_end_date"] == "2026-06-10"
        assert inner["planned_trading_date"] == "2026-06-25"

    def test_no_branch_actual_date_empty(self) -> None:
        payload = build_create_fitout_confirm_payload(self._collected_no(), "FIT_OUT_CONFIRM_FOS")
        assert payload["payload"]["actual_fitout_start_date"] == ""

    def test_fos_payload_has_documents_ids_key(self) -> None:
        payload = build_create_fitout_confirm_payload(self._collected_yes(), "FIT_OUT_CONFIRM_FOS")
        assert "documents_ids" in payload["payload"]

    def test_fos_title_auto_filled(self) -> None:
        payload = build_create_fitout_confirm_payload(self._collected_yes(), "FIT_OUT_CONFIRM_FOS")
        assert payload["title"] == "Confirm Fitout Start - t0105712"

    def test_sub_category_in_top_level(self) -> None:
        payload = build_create_fitout_confirm_payload(self._collected_yes(), "FIT_OUT_CONFIRM_FOS")
        assert payload["sub_category"] == "FIT_OUT_CONFIRM_FOS"
        assert payload["service_category"] == "FIT_OUT_AND_HANDOVER"

    def test_lease_id_in_top_level(self) -> None:
        payload = build_create_fitout_confirm_payload(self._collected_yes(), "FIT_OUT_CONFIRM_FOS")
        assert payload["lease_id"] == 95404

    def test_service_request_id_empty_for_create(self) -> None:
        payload = build_create_fitout_confirm_payload(self._collected_yes(), "FIT_OUT_CONFIRM_FOS")
        assert payload["service_request_id"] == ""


# ---------------------------------------------------------------------------
# TestBuildCreateEndFitoutPayload
# ---------------------------------------------------------------------------


class TestBuildCreateEndFitoutPayload:
    def _collected_yes(self) -> dict[str, Any]:
        return {
            **_BASE_COLLECTED,
            "actual_fitout_start_date_readonly": "2026-05-14",
            "fitout_complete_done": "yes",
            "actual_fitout_complete_date": "2026-06-03",
        }

    def _collected_no(self) -> dict[str, Any]:
        return {
            **_BASE_COLLECTED,
            "actual_fitout_start_date_readonly": "2026-05-14",
            "fitout_complete_done": "no",
            "planned_fitout_complete_date": "2026-06-10",
            "planned_trading_date": "2026-06-25",
            "delay_reason": "Delayed completion",
        }

    def test_end_yes_branch_actual_date_present(self) -> None:
        payload = build_create_fitout_confirm_payload(self._collected_yes(), "FIT_OUT_CONFIRM_END")
        assert payload["payload"]["actual_fitout_complete_date"] == "2026-06-03"

    def test_end_yes_branch_planned_empty(self) -> None:
        payload = build_create_fitout_confirm_payload(self._collected_yes(), "FIT_OUT_CONFIRM_END")
        assert payload["payload"]["planned_fitout_complete_date"] == ""

    def test_end_no_branch_planned_dates_present(self) -> None:
        payload = build_create_fitout_confirm_payload(self._collected_no(), "FIT_OUT_CONFIRM_END")
        inner = payload["payload"]
        assert inner["planned_fitout_complete_date"] == "2026-06-10"
        assert inner["planned_trading_date"] == "2026-06-25"

    def test_end_title_auto_filled(self) -> None:
        payload = build_create_fitout_confirm_payload(self._collected_yes(), "FIT_OUT_CONFIRM_END")
        assert payload["title"] == "Confirm Fitout End - t0105712"


# ---------------------------------------------------------------------------
# TestBuildCreateTradingPayload
# ---------------------------------------------------------------------------


class TestBuildCreateTradingPayload:
    def _collected_yes(self) -> dict[str, Any]:
        return {
            "lease_brand_mall": "t0105712 - Brand - Mall",
            "lease_code": "t0105712",
            "lease_id": 95404,
            "pms_tenant_code": "t0105712",
            "company_name": "116",
            "brand_name": "Brand",
            "mall_name": "Mall",
            "base_trading_date": "2026-06-15",
            "actual_fitout_end_date_readonly": "2026-06-03",
            "trading_done": "yes",
            "actual_trading_date": "2026-06-18",
        }

    def _collected_no(self) -> dict[str, Any]:
        return {
            **self._collected_yes(),
            "trading_done": "no",
            "actual_trading_date": "",
            "planned_trading_date": "2026-06-25",
            "delay_reason": "Unit not ready",
        }

    def test_trading_yes_actual_date_present(self) -> None:
        payload = build_create_fitout_confirm_payload(self._collected_yes(), "FIT_OUT_CONFIRM_TRADING")
        assert payload["payload"]["actual_trading_date"] == "2026-06-18"

    def test_trading_no_planned_date_present(self) -> None:
        payload = build_create_fitout_confirm_payload(self._collected_no(), "FIT_OUT_CONFIRM_TRADING")
        assert payload["payload"]["planned_trading_date"] == "2026-06-25"

    def test_trading_no_documents_ids_key(self) -> None:
        """TRADING payload must NOT have a documents_ids key."""
        payload = build_create_fitout_confirm_payload(self._collected_yes(), "FIT_OUT_CONFIRM_TRADING")
        assert "documents_ids" not in payload["payload"], (
            "TRADING payload must not include documents_ids (no documents section)"
        )

    def test_trading_no_comments_key(self) -> None:
        """TRADING payload must NOT have a comments key."""
        payload = build_create_fitout_confirm_payload(self._collected_yes(), "FIT_OUT_CONFIRM_TRADING")
        assert "comments" not in payload["payload"], (
            "TRADING payload must not include comments (no comments section)"
        )

    def test_trading_title_auto_filled(self) -> None:
        payload = build_create_fitout_confirm_payload(self._collected_yes(), "FIT_OUT_CONFIRM_TRADING")
        assert payload["title"] == "Confirm Trading - t0105712"


# ---------------------------------------------------------------------------
# TestBuildApprovePayload
# ---------------------------------------------------------------------------


class TestBuildApprovePayload:
    def _collected(self) -> dict[str, Any]:
        return {
            **_BASE_COLLECTED,
            "fitout_start_done": "yes",
            "actual_fitout_start_date": "2026-05-14",
        }

    def test_approve_payload_has_status_approved(self) -> None:
        payload = build_approve_fitout_confirm_payload(
            self._collected(), "FIT_OUT_CONFIRM_FOS", sr_id="9512", approve=True
        )
        assert payload["status"] == "APPROVED"

    def test_update_only_payload_has_status_in_process(self) -> None:
        payload = build_approve_fitout_confirm_payload(
            self._collected(), "FIT_OUT_CONFIRM_FOS", sr_id="9512", approve=False
        )
        assert payload["status"] == "IN_PROCESS"

    def test_approve_payload_has_service_request_id(self) -> None:
        payload = build_approve_fitout_confirm_payload(
            self._collected(), "FIT_OUT_CONFIRM_FOS", sr_id="9512", approve=True
        )
        assert payload["service_request_id"] == "9512"

    def test_approve_payload_includes_full_payload_fields(self) -> None:
        payload = build_approve_fitout_confirm_payload(
            self._collected(), "FIT_OUT_CONFIRM_FOS", sr_id="9512", approve=True
        )
        assert "payload" in payload
        assert "actual_fitout_start_date" in payload["payload"]


# ---------------------------------------------------------------------------
# TestDateFormatting
# ---------------------------------------------------------------------------


class TestDateFormatting:
    def test_iso_date_preserved(self) -> None:
        collected = {
            **_BASE_COLLECTED,
            "fitout_start_done": "yes",
            "actual_fitout_start_date": "2026-05-14",
        }
        payload = build_create_fitout_confirm_payload(collected, "FIT_OUT_CONFIRM_FOS")
        assert payload["payload"]["actual_fitout_start_date"] == "2026-05-14"

    def test_non_iso_date_becomes_empty(self) -> None:
        """Dates not in YYYY-MM-DD format are normalised to empty string."""
        collected = {
            **_BASE_COLLECTED,
            "fitout_start_done": "yes",
            "actual_fitout_start_date": "14/05/2026",  # wrong format
        }
        payload = build_create_fitout_confirm_payload(collected, "FIT_OUT_CONFIRM_FOS")
        assert payload["payload"]["actual_fitout_start_date"] == ""


# ---------------------------------------------------------------------------
# TestPayloadBuilderNode
# ---------------------------------------------------------------------------


class TestPayloadBuilderNode:
    def _base_state(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "workflow_stage": "CREATE_SR",
            "sub_category": "FIT_OUT_CONFIRM_FOS",
            "collected_data": {
                **_BASE_COLLECTED,
                "fitout_start_done": "yes",
                "actual_fitout_start_date": "2026-05-14",
            },
            "backend_refs": {},
            "documents": [],
        }
        base.update(overrides)
        return base

    @pytest.mark.asyncio
    async def test_node_builds_create_payload(self) -> None:
        from app.agents.graph.nodes.fitout_confirm.fitout_confirm_payload_builder_node import (
            fitout_confirm_payload_builder_node,
        )

        result = await fitout_confirm_payload_builder_node(self._base_state())
        backend_refs = result.get("backend_refs", {})
        assert "create_payload" in backend_refs
        assert backend_refs["create_payload"]["sub_category"] == "FIT_OUT_CONFIRM_FOS"

    @pytest.mark.asyncio
    async def test_node_fails_for_unknown_sub_category(self) -> None:
        from app.agents.graph.nodes.fitout_confirm.fitout_confirm_payload_builder_node import (
            fitout_confirm_payload_builder_node,
        )

        state = self._base_state(sub_category="UNKNOWN_SC")
        result = await fitout_confirm_payload_builder_node(state)
        assert result.get("status") == "FAILED"
