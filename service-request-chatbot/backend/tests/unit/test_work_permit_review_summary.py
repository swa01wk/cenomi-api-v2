"""Unit tests — Work Permit review summary facts and conversational wording."""

from __future__ import annotations

from app.agents.services.work_permit_review_summary import (
    build_wp_review_facts,
    build_wp_review_prompt_suggestions,
    build_wp_review_summary,
    compose_wp_review_message,
)


def _cold_collected() -> dict:
    return {
        "mall": "Nakheel Mall",
        "brand": "Zara",
        "unit_codes": ["U-12"],
        "work_permit_type": "CONSTRUCTION_COLD",
        "type_work": "Fixture installation",
        "list_equipment": "Ladder, drill",
        "work_info": "After hours only",
        "responsible_person": "Ahmed",
        "planneddate": {
            "worktimedate": "25/09/2026",
            "completionworkdate": "26/09/2026",
            "worktimefrom": "09:00",
            "worktimeto": "17:00",
        },
        "workers": [
            {"full_name": "Ali"},
            {"full_name": "Omar"},
            {"full_name": "Sara"},
        ],
    }


def _ops_collected() -> dict:
    return {
        "mall": "Red Sea Mall",
        "brand": "Nike",
        "unit_codes": "A1",
        "work_permit_type": "OPERATIONS",
        "typeofwork": {
            "value": ["shopfitting", "stockdelivery"],
            "otheroption": "",
        },
        "effective_starting": {
            "datefrom": "01/10/2026",
            "dateto": "03/10/2026",
        },
        "firstperiod": {"fromLT": "10:00", "toLT": "14:00"},
        "entry_gate": "Gate 3",
    }


class TestWpReviewSummary:
    def test_cold_facts_emphasized(self) -> None:
        fields = build_wp_review_facts(
            _cold_collected(), workflow_stage="WP_DD_REVIEW"
        )
        keys = {f["key"] for f in fields}
        assert "mall" in keys
        assert "brand" in keys
        assert "type_of_work" in keys
        assert "schedule" in keys
        assert "workers" in keys
        assert "current_hop" in keys
        assert all(f.get("emphasis") for f in fields if f["key"] in keys)
        # Not a full form dump — notes/title omitted when empty.
        assert "notes" not in keys
        assert "title" not in keys

    def test_ops_typeofwork_labels(self) -> None:
        fields = build_wp_review_facts(
            _ops_collected(), workflow_stage="WP_MM_REVIEW", subtype="OPERATIONS"
        )
        tow = next(f for f in fields if f["key"] == "type_of_work")
        assert "Shop Fitting" in tow["value"]
        assert "Stock Delivery" in tow["value"]
        assert "shopfitting" not in tow["value"]

    def test_message_is_conversational(self) -> None:
        msg = compose_wp_review_message(
            _cold_collected(), workflow_stage="WP_DD_REVIEW"
        )
        assert msg.startswith("Hi!")
        assert "**Zara** wants to carry out **fixture installation**" in msg
        assert "at **Nakheel Mall, Unit U-12**" in msg
        assert "from **25/09/2026 to 26/09/2026**" in msg
        # Multi-hop permit: the pending line names the hop.
        assert "**Construction – Cold Work** permit is now pending your **DD review**" in msg
        assert msg.endswith("What would you like to do?")
        assert "Please review this Work Permit hop" not in msg

    def test_message_greets_by_first_name(self) -> None:
        msg = compose_wp_review_message(
            _ops_collected(),
            workflow_stage="WP_MM_OPS_REVIEW",
            subtype="OPERATIONS",
            first_name="Kishan",
        )
        assert msg.startswith("Hi Kishan!")
        assert "**Nike** wants to carry out **shop fitting and stock delivery work**" in msg
        # Single-hop permit: the pending line names the permit review.
        assert "The **Operations review** is now pending with you." in msg

    def test_message_degrades_without_facts(self) -> None:
        msg = compose_wp_review_message(
            {"work_permit_type": "OPERATIONS"},
            workflow_stage="WP_MM_OPS_REVIEW",
            subtype="OPERATIONS",
            first_name="  ",
        )
        assert msg.startswith("Hi!")
        assert "A tenant has requested an **Operations** permit." in msg
        assert " at **" not in msg
        assert "from **" not in msg

    def test_single_day_uses_on(self) -> None:
        collected = _cold_collected()
        collected["planneddate"]["completionworkdate"] = "25/09/2026"
        msg = compose_wp_review_message(collected, workflow_stage="WP_DD_REVIEW")
        assert ", on **25/09/2026**." in msg


class TestWpReviewSummaryItems:
    def test_ops_summary_splits_dates_and_times(self) -> None:
        items = build_wp_review_summary(_ops_collected(), subtype="OPERATIONS")
        by_key = {i["key"]: i for i in items}
        assert list(by_key) == [
            "brand",
            "mall",
            "unit",
            "schedule",
            "request_type",
            "type_of_work",
        ]
        assert by_key["schedule"]["value"] == "01/10/2026 – 03/10/2026"
        assert by_key["schedule"]["detail"] == "10:00 – 14:00"
        assert by_key["request_type"]["value"] == "Operations"
        assert by_key["brand"]["icon"] == "store"

    def test_cold_summary_uses_planned_dates(self) -> None:
        items = build_wp_review_summary(_cold_collected())
        schedule = next(i for i in items if i["key"] == "schedule")
        assert schedule["value"] == "25/09/2026 – 26/09/2026"
        assert schedule["detail"] == "09:00 – 17:00"
        tow = next(i for i in items if i["key"] == "type_of_work")
        assert tow["value"] == "Fixture installation"

    def test_empty_items_skipped_and_no_empty_detail(self) -> None:
        items = build_wp_review_summary(
            {"brand": "Nike", "work_permit_type": "OPERATIONS"}, subtype="OPERATIONS"
        )
        assert [i["key"] for i in items] == ["brand", "request_type"]
        assert all("detail" not in i for i in items)

    def test_prompt_suggestions_have_no_workflow_action(self) -> None:
        deco = build_wp_review_prompt_suggestions(_cold_collected())
        assert deco["kind"] == "labeled_options"
        for opt in deco["options"]:
            assert "action" not in opt or opt.get("action") is None
