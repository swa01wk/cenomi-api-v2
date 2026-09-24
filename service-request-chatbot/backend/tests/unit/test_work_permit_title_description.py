"""Work Permit title/description: generators, skip-fill, short-flow (Handover-style)."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.shared.merge_state_node import (
    _AI_FIELD_META_KEY,
    merge_state_node,
)
from app.agents.graph.nodes.shared.missing_field_node import missing_field_node
from app.agents.schemas.work_permit_schema import (
    AUTO_GENERATED_FIELDS,
    WP_AI_FILLED_FIELDS,
    generate_wp_description,
    generate_wp_title,
    get_missing_fields,
    wp_location_label,
)
from app.agents.services import sr_autofill_service
from app.core.config import get_settings


class TestWpTitleDescriptionGenerators:
    def test_description_uses_lease_code_only(self) -> None:
        collected = {
            "work_permit_type": "OPERATIONS",
            "lease_brand_mall": "t0105712 / UA / Jeddah",
            "brand": "Other",
            "mall": "Other Mall",
            "lease_code": "t0105712",
        }
        assert wp_location_label(collected).startswith("t0105712")
        assert generate_wp_description(collected) == (
            "Operations work permit for t0105712."
        )

    def test_title_uses_lease_code_only(self) -> None:
        collected = {
            "work_permit_type": "OPERATIONS",
            "brand": "Bershka",
            "mall": "Jawharat",
            "lease_code": "t0028922",
            "lease_brand_mall": "t0028922 - Brand Bershka - Jawharat Jeddah",
        }
        assert generate_wp_title(collected) == "Operations – t0028922"

    def test_title_compact_construction_hot(self) -> None:
        collected = {
            "work_permit_type": "CONSTRUCTION_HOT",
            "brand": "Under Armour",
            "mall": "Jeddah",
            "lease_code": "t0105712",
        }
        assert generate_wp_title(collected) == "Construction Hot – t0105712"

    def test_lease_code_fallback(self) -> None:
        collected = {
            "work_permit_type": "MAINTENANCE_COLD",
            "lease_code": "LC-99",
        }
        assert generate_wp_title(collected) == "Maintenance Cold – LC-99"
        assert generate_wp_description(collected).endswith("LC-99.")

    def test_empty_without_lease_code(self) -> None:
        assert generate_wp_title({"work_permit_type": "OPERATIONS"}) == ""
        assert generate_wp_description({"work_permit_type": "OPERATIONS"}) == ""
        assert (
            generate_wp_title(
                {
                    "work_permit_type": "OPERATIONS",
                    "brand": "Bershka",
                    "mall": "Jawharat",
                }
            )
            == ""
        )
        assert (
            generate_wp_description(
                {
                    "work_permit_type": "OPERATIONS",
                    "lease_brand_mall": "t0105712 / UA / Jeddah",
                }
            )
            == ""
        )

    def test_title_truncated_at_50(self) -> None:
        title = generate_wp_title(
            {
                "work_permit_type": "OPERATIONS",
                "lease_code": "t" + ("0" * 80),
            }
        )
        assert len(title) == 50
        assert title.endswith("…")

    def test_description_truncated_at_200(self) -> None:
        desc = generate_wp_description(
            {
                "work_permit_type": "OPERATIONS",
                "lease_code": "X" * 250,
            }
        )
        assert len(desc) == 200
        assert desc.endswith("…")

    def test_not_auto_generated_fields(self) -> None:
        assert AUTO_GENERATED_FIELDS == frozenset()
        assert WP_AI_FILLED_FIELDS == frozenset({"title", "description"})


class TestWpSkipFill:
    """Empty string = skip → fill; None = not asked; user text preserved."""

    @pytest.mark.asyncio
    async def test_skip_fills_wp_human_prose(self) -> None:
        state: dict[str, Any] = {
            "active_agent": "work_permit_agent",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "user_message": "none",
            "collected_data": {
                "work_permit_type": "OPERATIONS",
                "_wp_type_confirmed": True,
                "lease_code": "LC-1",
                "lease_brand_mall": "LC-1 / Brand / Mall",
                "title": "",
                "description": "",
            },
            "extracted_fields": {},
            "corrected_fields": {},
            "active_field": None,
            "confirmation_status": None,
        }
        result = await merge_state_node(state)
        collected = result.get("collected_data") or {}
        assert collected["title"] == "Operations – LC-1"
        assert collected["description"] == "Operations work permit for LC-1."
        # Skip-fill is deterministic (no short-flow meta unless short-flow ran).
        assert collected.get(_AI_FIELD_META_KEY) in (None, {})

    @pytest.mark.asyncio
    async def test_none_not_filled_so_bot_can_ask(self) -> None:
        state: dict[str, Any] = {
            "active_agent": "work_permit_agent",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "user_message": "ok",
            "collected_data": {
                "work_permit_type": "OPERATIONS",
                "_wp_type_confirmed": True,
                "lease_brand_mall": "LC-1 / Brand / Mall",
                # title/description absent → None
            },
            "extracted_fields": {},
            "corrected_fields": {},
            "active_field": None,
            "confirmation_status": None,
        }
        result = await merge_state_node(state)
        collected = result.get("collected_data") or {}
        assert collected.get("title") is None
        assert collected.get("description") is None

    @pytest.mark.asyncio
    async def test_user_typed_value_not_overwritten(self) -> None:
        state: dict[str, Any] = {
            "active_agent": "work_permit_agent",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "user_message": "ok",
            "collected_data": {
                "work_permit_type": "OPERATIONS",
                "_wp_type_confirmed": True,
                "lease_brand_mall": "LC-1 / Brand / Mall",
                "title": "My custom title",
                "description": "My custom description",
            },
            "extracted_fields": {},
            "corrected_fields": {},
            "active_field": None,
            "confirmation_status": None,
        }
        result = await merge_state_node(state)
        collected = result.get("collected_data") or {}
        assert collected["title"] == "My custom title"
        assert collected["description"] == "My custom description"

    def test_missing_fields_asks_when_none(self) -> None:
        collected = {
            "work_permit_type": "OPERATIONS",
            "_wp_type_confirmed": True,
            "lease_code": "LC-1",
            "lease_id": 1,
            "lease_brand_mall": "LC-1 / Brand / Mall",
        }
        missing = get_missing_fields("CREATE_WORK_PERMIT", collected)
        assert "description" in missing
        assert "title" in missing

    def test_missing_fields_skip_empty_string(self) -> None:
        collected = {
            "work_permit_type": "OPERATIONS",
            "_wp_type_confirmed": True,
            "lease_code": "LC-1",
            "lease_id": 1,
            "lease_brand_mall": "LC-1 / Brand / Mall",
            "title": "",
            "description": "",
        }
        missing = get_missing_fields("CREATE_WORK_PERMIT", collected)
        assert "description" not in missing
        assert "title" not in missing


class TestWpShortFlowAutofill:
    @pytest.fixture(autouse=True)
    def _enable_short_flow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(get_settings(), "handover_short_flow_enabled", True)
        monkeypatch.setattr(get_settings(), "handover_ai_autofill_enabled", False)

    def test_short_flow_skips_asking(self) -> None:
        collected = {
            "work_permit_type": "OPERATIONS",
            "_wp_type_confirmed": True,
            "lease_code": "LC-1",
            "lease_id": 1,
            "lease_brand_mall": "LC-1 / Brand / Mall",
        }
        missing = get_missing_fields("CREATE_WORK_PERMIT", collected)
        assert "description" not in missing
        assert "title" not in missing

    @pytest.mark.asyncio
    async def test_merge_short_flow_fills_with_template_provenance(self) -> None:
        state: dict[str, Any] = {
            "active_agent": "work_permit_agent",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "user_message": "ok",
            "collected_data": {
                "work_permit_type": "CONSTRUCTION_HOT",
                "_wp_type_confirmed": True,
                "lease_code": "t0105712",
                "lease_id": 1,
                "lease_brand_mall": "t0105712 / Under Armour / Jawharat Jeddah",
                "brand": "Under Armour",
                "mall": "Jawharat Jeddah",
            },
            "extracted_fields": {},
            "corrected_fields": {},
            "active_field": None,
            "confirmation_status": None,
        }
        result = await merge_state_node(state)
        collected = result.get("collected_data") or {}
        assert collected.get("title") == "Construction Hot – t0105712"
        assert collected.get("description") == (
            "Construction – Hot Work work permit for t0105712."
        )
        meta = collected.get(_AI_FIELD_META_KEY) or {}
        assert meta["title"]["source"] == "template"
        assert meta["description"]["source"] == "template"
        assert "wp_auto" not in str(meta)

    @pytest.mark.asyncio
    async def test_autofill_service_wp_templates(self) -> None:
        collected = {
            "work_permit_type": "OPERATIONS",
            "_wp_type_confirmed": True,
            "lease_code": "LC-1",
            "brand": "Brand",
            "mall": "Mall",
            "lease_brand_mall": "LC-1 / Brand / Mall",
        }
        values, provenance = await sr_autofill_service.autofill_text_fields(
            collected, "CREATE_WORK_PERMIT", use_ai=False
        )
        assert values["title"] == "Operations – LC-1"
        assert values["description"] == "Operations work permit for LC-1."
        assert provenance["title"]["source"] == "template"

    @pytest.mark.asyncio
    async def test_autofill_does_not_overwrite_user(self) -> None:
        collected = {
            "work_permit_type": "OPERATIONS",
            "_wp_type_confirmed": True,
            "lease_brand_mall": "LC-1 / Brand / Mall",
            "title": "User title",
            "description": "User description",
        }
        values, _ = await sr_autofill_service.autofill_text_fields(
            collected, "CREATE_WORK_PERMIT", use_ai=False
        )
        assert values == {}

    @pytest.mark.asyncio
    async def test_does_not_refresh_lease_code_title_when_brand_arrives(self) -> None:
        """Lease-code-only title/desc are final — brand must not rewrite them."""
        collected = {
            "work_permit_type": "OPERATIONS",
            "_wp_type_confirmed": True,
            "lease_code": "t0028922",
            "brand": "Bershka",
            "mall": "Jawharat Jeddah",
            "lease_brand_mall": "t0028922 - Brand Bershka - Jawharat Jeddah",
            "title": "Operations – t0028922",
            "description": "Operations work permit for t0028922.",
        }
        values, _ = await sr_autofill_service.autofill_text_fields(
            collected, "CREATE_WORK_PERMIT", use_ai=False
        )
        assert values == {}

    @pytest.mark.asyncio
    async def test_merge_keeps_lease_code_only_on_skip_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(get_settings(), "handover_short_flow_enabled", False)
        state: dict[str, Any] = {
            "active_agent": "work_permit_agent",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "user_message": "ok",
            "collected_data": {
                "work_permit_type": "OPERATIONS",
                "_wp_type_confirmed": True,
                "lease_code": "t0028922",
                "brand": "Bershka",
                "mall": "Jawharat",
                "title": "Operations – t0028922",
                "description": "Operations work permit for t0028922.",
            },
            "extracted_fields": {},
            "corrected_fields": {},
            "active_field": None,
            "confirmation_status": None,
        }
        result = await merge_state_node(state)
        collected = result.get("collected_data") or {}
        assert collected["title"] == "Operations – t0028922"
        assert collected["description"] == "Operations work permit for t0028922."


class TestHandoverGeneratorRegression:
    """Path A/B generators and slug title stay on sr_autofill_service."""

    def test_path_a_description_and_slug_title(self) -> None:
        desc = sr_autofill_service.generate_description(
            "t0112136", "Brand", "Mall", already_trading=False
        )
        assert desc.startswith("Handover inspection for lease t0112136")
        title = sr_autofill_service.generate_title("t0112136", "Custom user words here")
        assert title == "handover-t0112136-custom-user-words-here"

    def test_path_b_description(self) -> None:
        desc = sr_autofill_service.generate_description(
            "t0112136", "Brand", "Mall", already_trading=True
        )
        assert desc.startswith("Already Trading handover for lease t0112136")

    def test_was_skipped_shared(self) -> None:
        assert sr_autofill_service.was_skipped("") is True
        assert sr_autofill_service.was_skipped("  ") is True
        assert sr_autofill_service.was_skipped(None) is False
        assert sr_autofill_service.was_skipped("hello") is False


@pytest.mark.asyncio
class TestWpAskOrder:
    async def test_missing_field_asks_description_when_not_short_flow(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(get_settings(), "handover_short_flow_enabled", False)
        state = {
            "active_agent": "work_permit_agent",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "collected_data": {
                "work_permit_type": "CONSTRUCTION_HOT",
                "_wp_type_confirmed": True,
                "lease_code": "t0105712",
                "lease_id": 456,
                "property_id": 789,
                "unit_codes": ["FF050"],
                "mall": "Jawharat Jeddah",
                "brand": "Under Armour",
                "tenant_profile_id": 1,
                "lease_brand_mall": "t0105712 / Under Armour / Jawharat Jeddah",
                "unit_type": "Retail",
                # description/title still None — should be asked before form widgets
            },
            "validation_errors": [],
            "documents": [],
        }
        result = await missing_field_node(state)
        active = result.get("active_field")
        assert active in {"description", "title"}

    async def test_short_flow_does_not_ask_title_description(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(get_settings(), "handover_short_flow_enabled", True)
        state = {
            "active_agent": "work_permit_agent",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "collected_data": {
                "work_permit_type": "CONSTRUCTION_HOT",
                "_wp_type_confirmed": True,
                "lease_code": "t0105712",
                "lease_id": 456,
                "property_id": 789,
                "unit_codes": ["FF050"],
                "mall": "Jawharat Jeddah",
                "brand": "Under Armour",
                "tenant_profile_id": 1,
                "lease_brand_mall": "t0105712 / Under Armour / Jawharat Jeddah",
                "unit_type": "Retail",
                "contractor_name": "Acme",
                "phone": "8486697081",
                "planneddate": {
                    "worktimedate": "01/05/2026",
                    "worktimefrom": "09:00",
                    "worktimeto": "17:00",
                    "completionworkdate": "01/05/2026",
                    "completionfrom": "09:00",
                    "completionto": "17:00",
                },
                "workers": {
                    "instructor_name": "Bob",
                    "worker": [
                        {
                            "name": "A",
                            "designation": "W",
                            "iqama_number": "1",
                            "document_id": "1",
                            "worker_qualification": "x",
                        }
                    ],
                },
                "measureofsafety": {"fire_extinguisher": "yes"},
                "title": (
                    "Construction – Hot Work – t0105712 / Under Armour / Jawharat Jeddah"
                ),
                "description": (
                    "Construction – Hot Work work permit for "
                    "t0105712 / Under Armour / Jawharat Jeddah."
                ),
            },
            "validation_errors": [],
            "documents": [],
        }
        result = await missing_field_node(state)
        active = result.get("active_field")
        ui_fields = (result.get("response_ui") or {}).get("fields") or []
        assert active not in {"title", "description"}
        assert "title" not in ui_fields
        assert "description" not in ui_fields
