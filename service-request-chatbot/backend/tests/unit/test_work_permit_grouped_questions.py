"""Work Permit grouped questions + suggestion chips."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.shared.missing_field_node import (
    _build_wp_group_ask,
    _build_wp_singleton_ask,
    _should_ask_wp_group,
    missing_field_node,
)
from app.agents.schemas.work_permit_form_fields import (
    resolve_wp_group,
    coerce_tenant_field_value,
)
from app.agents.services.work_permit_suggestions_service import (
    build_department_options,
    build_planneddate_options,
    build_wp_decorations,
    build_wp_type_options,
    build_measureofsafety_options,
)


def _lease_base(**extra: Any) -> dict[str, Any]:
    data = {
        "lease_code": "t0105712",
        "lease_id": 456,
        "property_id": 789,
        "unit_codes": ["FF050"],
        "mall": "Jawharat Jeddah",
        "brand": "Under Armour",
        "tenant_profile_id": 1,
    }
    data.update(extra)
    # Confirmed subtype unless the test is specifically probing unconfirmed type.
    if data.get("work_permit_type") and "_wp_type_confirmed" not in extra:
        data["_wp_type_confirmed"] = True
    return data


class TestWpFieldGroups:
    def test_cold_identity_group_resolves(self) -> None:
        missing = [
            "department",
            "unit_type",
            "owner",
            "trade_name",
            "responsible_person",
            "phone",
            "type_work",
        ]
        group = resolve_wp_group("CONSTRUCTION_COLD", missing)
        assert group is not None
        assert group[0] == "department"
        assert "type_work" not in group
        assert len(group) == 6

    def test_hot_contact_group_resolves(self) -> None:
        missing = ["unit_type", "contractor_name", "phone", "planneddate"]
        group = resolve_wp_group("CONSTRUCTION_HOT", missing)
        assert group == ("unit_type", "contractor_name", "phone")

    def test_should_ask_wp_group_requires_two_open(self) -> None:
        collected = _lease_base(work_permit_type="CONSTRUCTION_HOT")
        assert _should_ask_wp_group(
            "work_permit_agent",
            "CREATE_WORK_PERMIT",
            ["unit_type", "contractor_name"],
            collected,
        )
        assert not _should_ask_wp_group(
            "work_permit_agent",
            "CREATE_WORK_PERMIT",
            ["planneddate"],
            collected,
        )


class TestWpSuggestions:
    def test_type_chips_cover_seven_subtypes(self) -> None:
        opts = build_wp_type_options()
        assert len(opts) == 7
        actions = {o["action"] for o in opts}
        assert "select_wp_type_construction_hot" in actions
        assert "select_wp_type_operations" in actions

    def test_department_chips(self) -> None:
        opts = build_department_options()
        assert {o["value"] for o in opts} == {"leasing", "finance", "operations"}

    def test_planneddate_presets_are_json(self) -> None:
        import json
        from datetime import date

        opts = build_planneddate_options(today=date(2026, 5, 1))
        assert len(opts) >= 2
        parsed = json.loads(opts[0]["value"])
        assert parsed["worktimedate"]
        assert parsed["completionworkdate"]
        coerced = coerce_tenant_field_value(
            "planneddate", opts[0]["value"], field_type="planneddate"
        )
        assert isinstance(coerced, dict)
        assert coerced["worktimedate"] == "01/05/2026"

    def test_measureofsafety_bulk_chip(self) -> None:
        import json

        opts = build_measureofsafety_options({})
        assert opts[0]["label"].startswith("All common")
        payload = json.loads(opts[0]["value"])
        assert payload["fire_extinguisher"] == "yes"

    def test_typeofwork_label_maps_to_portal_key(self) -> None:
        coerced = coerce_tenant_field_value(
            "typeofwork",
            "Maintenance inside the shop",
            field_type="typeofwork",
        )
        assert coerced == {"value": ["maintenance"], "otheroption": ""}

    def test_typeofwork_typo_maps_to_maintenance(self) -> None:
        coerced = coerce_tenant_field_value(
            "typeofwork",
            {"value": "Maintenanace inside the shop", "otheroption": ""},
            field_type="typeofwork",
        )
        assert coerced["value"] == ["maintenance"]

    def test_typeofwork_unknown_goes_to_others(self) -> None:
        coerced = coerce_tenant_field_value(
            "typeofwork",
            "Custom specialty work",
            field_type="typeofwork",
        )
        assert coerced == {
            "value": ["others"],
            "otheroption": "Custom specialty work",
        }

    def test_decorations_for_type(self) -> None:
        deco = build_wp_decorations(fields=["work_permit_type"])
        assert deco is not None
        assert deco["suggestions"]["kind"] == "labeled_options"
        assert len(deco["suggestions"]["options"]) == 7

    def test_decorations_for_typeofwork(self) -> None:
        deco = build_wp_decorations(fields=["typeofwork"])
        assert deco is not None
        assert deco["suggestions"]["kind"] == "labeled_options"
        opts = deco["suggestions"]["options"]
        assert len(opts) == 7
        by_value = {o["value"]: o["label"] for o in opts}
        assert by_value["construction"] == "Construction"
        assert by_value["shopfitting"] == "Shop Fitting"
        assert by_value["maintenance"] == "Maintenance inside the shop"
        assert by_value["others"] == "Others"


class TestWpGroupAskShape:
    def test_group_ask_emits_fields_and_department_chips(self) -> None:
        collected = _lease_base(work_permit_type="CONSTRUCTION_COLD")
        group = [
            "department",
            "unit_type",
            "owner",
            "trade_name",
            "responsible_person",
            "phone",
        ]
        patch = _build_wp_group_ask(collected, group, group + ["type_work"])
        ui = patch["response_ui"]
        assert ui["type"] == "text_question"
        assert ui["field"] == "department"
        assert ui["fields"] == group
        assert ui.get("verbatim") is True
        opts = patch["response_decorations"]["suggestions"]["options"]
        assert any(o["value"] == "leasing" for o in opts)

    def test_singleton_planneddate_has_presets(self) -> None:
        collected = _lease_base(work_permit_type="CONSTRUCTION_HOT")
        patch = _build_wp_singleton_ask(
            "planneddate",
            "Ask for planned date",
            ["planneddate"],
            collected,
        )
        opts = patch["response_decorations"]["suggestions"]["options"]
        assert len(opts) >= 2
        assert patch["active_field"] == "planneddate"

    def test_singleton_typeofwork_has_chips(self) -> None:
        collected = _lease_base(work_permit_type="OPERATIONS")
        patch = _build_wp_singleton_ask(
            "typeofwork",
            "Ask for type of work",
            ["typeofwork", "permission_given_to"],
            collected,
        )
        opts = patch["response_decorations"]["suggestions"]["options"]
        assert len(opts) == 7
        assert any(o["value"] == "shopfitting" for o in opts)
        assert patch["active_field"] == "typeofwork"


@pytest.mark.asyncio
class TestWpMissingFieldNodeIntegration:
    async def test_cold_asks_description_after_type_and_lease(self) -> None:
        """Optional description/title are asked once lease + type are known."""
        state = {
            "active_agent": "work_permit_agent",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "collected_data": _lease_base(
                work_permit_type="CONSTRUCTION_COLD",
                lease_brand_mall="t0105712 / Under Armour / Jawharat Jeddah",
            ),
            "validation_errors": [],
            "documents": [],
        }
        result = await missing_field_node(state)
        assert result.get("active_field") in {"description", "title"}

    async def test_cold_asks_identity_group_after_optional_text(self) -> None:
        """After description/title are skipped, ask the cold identity group."""
        state = {
            "active_agent": "work_permit_agent",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "collected_data": _lease_base(
                work_permit_type="CONSTRUCTION_COLD",
                lease_brand_mall="t0105712 / Under Armour / Jawharat Jeddah",
                description="",
                title="",
            ),
            "validation_errors": [],
            "documents": [],
        }
        result = await missing_field_node(state)
        ui = result.get("response_ui") or {}
        assert ui.get("fields")
        assert "department" in ui["fields"]
        assert "owner" in ui["fields"]
        assert result.get("active_field") == "department"

    async def test_type_asked_before_lease_when_empty(self) -> None:
        """No subtype and no lease → ask work_permit_type chips first."""
        state = {
            "active_agent": "work_permit_agent",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "collected_data": {},
            "validation_errors": [],
            "documents": [],
        }
        result = await missing_field_node(state)
        assert result.get("active_field") == "work_permit_type"
        opts = (result.get("response_decorations") or {}).get("suggestions", {}).get(
            "options", []
        )
        assert len(opts) == 7
        assert result.get("response_ui", {}).get("field") == "work_permit_type"

    async def test_seeded_type_asks_lease_next(self) -> None:
        """Subtype confirmed (Create with AI seed) → ask lease, not type again."""
        state = {
            "active_agent": "work_permit_agent",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "collected_data": {
                "work_permit_type": "CONSTRUCTION_HOT",
                "_wp_type_confirmed": True,
            },
            "validation_errors": [],
            "documents": [],
        }
        result = await missing_field_node(state)
        assert result.get("active_field") == "lease_code"
        assert result.get("active_field") != "work_permit_type"

    async def test_unconfirmed_inferred_type_asks_chips(self) -> None:
        """LLM-guessed type without confirm flag → clear and show subtype chips."""
        state = {
            "active_agent": "work_permit_agent",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "collected_data": {"work_permit_type": "OPERATIONS"},
            "validation_errors": [],
            "documents": [],
        }
        result = await missing_field_node(state)
        assert result.get("active_field") == "work_permit_type"
        assert "work_permit_type" not in (result.get("collected_data") or {})
        opts = (result.get("response_decorations") or {}).get("suggestions", {}).get(
            "options", []
        )
        assert len(opts) == 7

    async def test_ops_asks_permission_after_typeofwork(self) -> None:
        """Portal marks Permission Given To required — ask after type of work."""
        state = {
            "active_agent": "work_permit_agent",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "collected_data": _lease_base(
                work_permit_type="OPERATIONS",
                lease_brand_mall="t0105712 / Brand / Mall",
                description="",
                title="",
                department="leasing",
                unit_type="Store",
                phone="501234567",
                typeofwork={"value": ["maintenance"], "otheroption": ""},
            ),
            "validation_errors": [],
            "documents": [],
        }
        result = await missing_field_node(state)
        assert result.get("active_field") == "permission_given_to"

    async def test_ops_typeofwork_ask_has_chips(self) -> None:
        state = {
            "active_agent": "work_permit_agent",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "collected_data": _lease_base(
                work_permit_type="OPERATIONS",
                lease_brand_mall="t0105712 / Brand / Mall",
                description="",
                title="",
                department="operations",
                unit_type="Store",
                phone="8486697081",
            ),
            "validation_errors": [],
            "documents": [],
        }
        result = await missing_field_node(state)
        assert result.get("active_field") == "typeofwork"
        opts = (result.get("response_decorations") or {}).get("suggestions", {}).get(
            "options", []
        )
        assert len(opts) == 7
        assert {o["value"] for o in opts} >= {
            "construction",
            "shopfitting",
            "stockdelivery",
            "others",
        }

    async def test_ops_unit_phone_group_has_unit_type_chips(self) -> None:
        collected = _lease_base(work_permit_type="OPERATIONS")
        result = _build_wp_group_ask(
            collected,
            ["unit_type", "phone"],
            ["unit_type", "phone", "typeofwork"],
        )
        opts = (result.get("response_decorations") or {}).get("suggestions", {}).get(
            "options", []
        )
        labels = {o.get("label") for o in opts}
        assert {"Retail", "F&B", "Store"} <= labels

    async def test_hot_type_singleton_has_chips(self) -> None:
        state = {
            "active_agent": "work_permit_agent",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "collected_data": _lease_base(),  # no work_permit_type
            "validation_errors": [],
            "documents": [],
        }
        result = await missing_field_node(state)
        # lease is present; next user gap is work_permit_type
        assert result.get("active_field") == "work_permit_type"
        opts = (result.get("response_decorations") or {}).get("suggestions", {}).get(
            "options", []
        )
        assert len(opts) == 7


class TestWpLeasePrefill:
    def test_enrich_copies_unit_type_when_present(self) -> None:
        from app.agents.graph.nodes.shared.lease_lookup_node import _enrich_collected_data
        from app.agents.services.lease_lookup_service import LeaseRecord

        record = LeaseRecord.model_validate(
            {
                "lease_code": "LC-1",
                "lease_id": 1,
                "contract_id": 1,
                "brand": "Brand",
                "brand_id": 1,
                "mall": "Mall",
                "property_id": 1,
                "tenant_profile_id": 1,
                "unit_codes": ["U1"],
                "contracted_area": 10.0,
                "city": "Jeddah",
                "lease_brand_mall": "LC-1 / Brand / Mall",
                "company_name": "Acme Co",
                "unit_type": "Retail",
            }
        )
        enriched = _enrich_collected_data({"work_permit_type": "CONSTRUCTION_HOT"}, record)
        assert enriched["unit_type"] == "Retail"
        assert enriched["company"] == "Acme Co"
        assert enriched["mall"] == "Mall"
        assert "phone" not in enriched or enriched.get("phone") in (None, "")
        assert "owner" not in enriched or enriched.get("owner") in (None, "")
        assert "trade_name" not in enriched or enriched.get("trade_name") in (None, "")

    def test_enrich_does_not_invent_unit_type(self) -> None:
        from app.agents.graph.nodes.shared.lease_lookup_node import _enrich_collected_data
        from app.agents.services.lease_lookup_service import LeaseRecord

        record = LeaseRecord.model_validate(
            {
                "lease_code": "LC-1",
                "lease_id": 1,
                "contract_id": 1,
                "brand": "Brand",
                "brand_id": 1,
                "mall": "Mall",
                "property_id": 1,
                "tenant_profile_id": 1,
                "unit_codes": ["U1"],
                "contracted_area": 10.0,
                "city": "Jeddah",
                "lease_brand_mall": "LC-1 / Brand / Mall",
            }
        )
        enriched = _enrich_collected_data({}, record)
        assert enriched.get("unit_type") in (None, "")
