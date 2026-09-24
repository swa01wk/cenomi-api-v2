"""Work Permit subtype confirmation + unit_type/phone split extraction."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.shared.field_extraction_node import (
    _apply_wp_bare_phone,
    _apply_wp_unit_type_phone_split,
)
from app.agents.graph.nodes.shared.merge_state_node import (
    _wp_seed_message_confirms_type,
    merge_state_node,
)
from app.agents.graph.nodes.work_permit.work_permit_entry_node import (
    work_permit_entry_node,
)


class TestWpSeedTypeConfirm:
    def test_seed_message_matches_construction_hot(self) -> None:
        msg = "I want to create a Work Permit service request (Construction - Hot)"
        assert _wp_seed_message_confirms_type(msg, "CONSTRUCTION_HOT")
        assert not _wp_seed_message_confirms_type(msg, "OPERATIONS")

    def test_seed_message_matches_operations(self) -> None:
        msg = "I want to create a Work Permit service request (Operations)"
        assert _wp_seed_message_confirms_type(msg, "OPERATIONS")

    def test_bare_create_does_not_confirm(self) -> None:
        msg = "I want to create a Work Permit service request"
        assert not _wp_seed_message_confirms_type(msg, "CONSTRUCTION_HOT")


@pytest.mark.asyncio
class TestWpTypeChipSetsConfirm:
    async def test_select_wp_type_sets_confirmed(self) -> None:
        state: dict[str, Any] = {
            "active_agent": "work_permit_agent",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "action_override": "select_wp_type_operations",
            "collected_data": {},
            "user_message": " ",
        }
        result = await work_permit_entry_node(state)
        collected = result.get("collected_data") or {}
        assert collected.get("work_permit_type") == "OPERATIONS"
        assert collected.get("_wp_type_confirmed") is True
        assert result.get("sub_category") == "OPERATIONS"


@pytest.mark.asyncio
class TestWpMergeSeedConfirm:
    async def test_seed_turn_marks_type_confirmed(self) -> None:
        state: dict[str, Any] = {
            "active_agent": "work_permit_agent",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "user_message": (
                "I want to create a Work Permit service request (Construction - Hot)"
            ),
            "collected_data": {},
            "extracted_fields": {
                "work_permit_type": {
                    "value": "CONSTRUCTION_HOT",
                    "confidence": 1.0,
                }
            },
            "corrected_fields": {},
            "active_field": None,
        }
        result = await merge_state_node(state)
        collected = result.get("collected_data") or {}
        assert collected.get("work_permit_type") == "CONSTRUCTION_HOT"
        assert collected.get("_wp_type_confirmed") is True
        assert result.get("sub_category") == "CONSTRUCTION_HOT"

    async def test_weak_inference_does_not_confirm(self) -> None:
        state: dict[str, Any] = {
            "active_agent": "work_permit_agent",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "user_message": "Create work permit request",
            "collected_data": {},
            "extracted_fields": {
                "work_permit_type": {
                    "value": "OPERATIONS",
                    "confidence": 0.7,
                }
            },
            "corrected_fields": {},
            "active_field": None,
        }
        result = await merge_state_node(state)
        collected = result.get("collected_data") or {}
        assert collected.get("work_permit_type") == "OPERATIONS"
        assert not collected.get("_wp_type_confirmed")


class TestWpUnitPhoneSplit:
    def test_store_comma_phone(self) -> None:
        extracted: dict[str, Any] = {}
        _apply_wp_unit_type_phone_split(
            extracted,
            user_message="Store, 8486697081",
            active_field="unit_type",
            missing_fields=["unit_type", "phone"],
            response_ui={"fields": ["unit_type", "phone"]},
            workflow_stage="CREATE_WORK_PERMIT",
            active_agent="work_permit_agent",
        )
        assert extracted["unit_type"]["value"] == "Store"
        assert extracted["phone"]["value"] == "8486697081"

    def test_fills_missing_sibling_only(self) -> None:
        extracted: dict[str, Any] = {
            "unit_type": {"value": "Retail", "confidence": 1.0},
        }
        _apply_wp_unit_type_phone_split(
            extracted,
            user_message="Retail, 8486697081",
            active_field="unit_type",
            missing_fields=["unit_type", "phone"],
            response_ui={"fields": ["unit_type", "phone"]},
            workflow_stage="CREATE_WORK_PERMIT",
            active_agent="work_permit_agent",
        )
        assert extracted["unit_type"]["value"] == "Retail"
        assert extracted["phone"]["value"] == "8486697081"

    def test_skips_when_not_wp_group(self) -> None:
        extracted: dict[str, Any] = {}
        _apply_wp_unit_type_phone_split(
            extracted,
            user_message="Store, 8486697081",
            active_field="department",
            missing_fields=["department"],
            response_ui={"fields": ["department"]},
            workflow_stage="CREATE_WORK_PERMIT",
            active_agent="work_permit_agent",
        )
        assert extracted == {}


class TestWpBarePhone:
    def test_digits_only_when_phone_active(self) -> None:
        """Live loop: unit_type done, user sends bare digits for phone."""
        extracted: dict[str, Any] = {}
        _apply_wp_bare_phone(
            extracted,
            user_message="84884848478",
            active_field="phone",
            missing_fields=["phone"],
            response_ui={"field": "phone"},
            workflow_stage="CREATE_WORK_PERMIT",
            active_agent="work_permit_agent",
        )
        assert extracted["phone"]["value"] == "84884848478"

    def test_digits_only_when_phone_still_in_group_missing(self) -> None:
        extracted: dict[str, Any] = {}
        _apply_wp_bare_phone(
            extracted,
            user_message="8486697081",
            active_field="unit_type",
            missing_fields=["unit_type", "phone"],
            response_ui={"fields": ["unit_type", "phone"]},
            workflow_stage="CREATE_WORK_PERMIT",
            active_agent="work_permit_agent",
        )
        assert extracted["phone"]["value"] == "8486697081"
        assert "unit_type" not in extracted

    def test_does_not_override_llm_phone(self) -> None:
        extracted: dict[str, Any] = {
            "phone": {"value": "111", "confidence": 1.0},
        }
        _apply_wp_bare_phone(
            extracted,
            user_message="8486697081",
            active_field="phone",
            missing_fields=["phone"],
            response_ui={},
            workflow_stage="CREATE_WORK_PERMIT",
            active_agent="work_permit_agent",
        )
        assert extracted["phone"]["value"] == "111"
