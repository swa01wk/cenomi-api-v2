"""Unit tests — Work Permit review summary facts and conversational wording."""

from __future__ import annotations

import pytest

from app.agents.services.work_permit_review_summary import (
    build_wp_detail_files,
    build_wp_review_details,
    build_wp_review_facts,
    build_wp_review_prompt_suggestions,
    build_wp_review_summary,
    compose_wp_history_sentence,
    compose_wp_review_message,
    group_wp_detail_sections,
    readable_wp_company,
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
        assert "**Construction – Cold Work** permit is now pending **DD review**" in msg
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

    def test_verified_company_is_the_first_item(self) -> None:
        collected = {
            **_ops_collected(),
            "lease_code": "t0099892",
            "company": "Retail Co",
            "company_lease_code": "t0099892",
        }
        items = build_wp_review_summary(collected, subtype="OPERATIONS")
        assert [i["key"] for i in items][:4] == ["company", "brand", "mall", "unit"]
        assert items[0] == {
            "key": "company",
            "label": "Company",
            "value": "Retail Co",
            "icon": "briefcase",
        }

    def test_full_summary_ends_with_lease(self) -> None:
        collected = {
            **_ops_collected(),
            "lease_code": "t0099892",
            "company": "Retail Co",
            "company_lease_code": "t0099892",
        }
        items = build_wp_review_summary(collected, subtype="OPERATIONS")
        assert [i["key"] for i in items] == [
            "company",
            "brand",
            "mall",
            "unit",
            "schedule",
            "request_type",
            "type_of_work",
            "lease",
        ]
        assert items[-1] == {
            "key": "lease",
            "label": "Lease",
            "value": "t0099892",
            "icon": "key",
        }

    def test_lease_picker_value_fills_the_lease_item(self) -> None:
        items = build_wp_review_summary(
            {**_ops_collected(), "lease": "t0099892"}, subtype="OPERATIONS"
        )
        assert items[-1]["key"] == "lease"
        assert items[-1]["value"] == "t0099892"

    def test_numeric_company_name_alone_gives_no_company_item(self) -> None:
        collected = {**_ops_collected(), "lease_code": "t0099892", "company_name": "4012"}
        items = build_wp_review_summary(collected, subtype="OPERATIONS")
        assert "company" not in [i["key"] for i in items]
        assert "4012" not in [i["value"] for i in items]

    def test_numeric_company_name_without_lease_gives_no_company_item(self) -> None:
        items = build_wp_review_summary(
            {**_ops_collected(), "company_name": "4012"}, subtype="OPERATIONS"
        )
        assert "company" not in [i["key"] for i in items]


class TestReadableWpCompany:
    def test_verified_company_for_the_current_lease(self) -> None:
        collected = {
            "lease_code": " T0099892 ",
            "company": "Retail Co",
            "company_lease_code": "t0099892",
        }
        assert readable_wp_company(collected) == "Retail Co"

    def test_lease_picker_value_counts_as_the_lease_code(self) -> None:
        collected = {"lease": "t0099892", "company": "Retail Co", "company_lease_code": "t0099892"}
        assert readable_wp_company(collected) == "Retail Co"

    def test_unverified_company_hidden_when_lease_known(self) -> None:
        assert readable_wp_company({"lease_code": "t0099892", "company": "zara"}) == ""

    def test_company_verified_for_another_lease_hidden(self) -> None:
        collected = {
            "lease_code": "t0099892",
            "company": "Old Co",
            "company_lease_code": "t0011111",
        }
        assert readable_wp_company(collected) == ""

    def test_non_digit_company_name_hidden_when_lease_known(self) -> None:
        assert readable_wp_company({"lease_code": "t0099892", "company_name": "Old Co"}) == ""

    def test_label_for_the_current_lease_is_shown(self) -> None:
        collected = {
            "lease_code": "t0099892",
            "brand": "Zara",
            "mall": "Mall of Dhahran",
            "company": "zara",
            "lease_brand_mall": "t0099892-Zara-Mall of Dhahran-Retail Co--شركة التجزئة",
        }
        assert readable_wp_company(collected) == "Retail Co"

    def test_label_for_another_lease_is_not_shown(self) -> None:
        collected = {
            "lease_code": "t0099892",
            "brand": "Zara",
            "mall": "Mall of Dhahran",
            "lease_brand_mall": "t0011111-Zara-Mall of Dhahran-Retail Co--شركة التجزئة",
        }
        assert readable_wp_company(collected) == ""

    def test_without_lease_code_company_then_named_company_name(self) -> None:
        assert readable_wp_company({"company": "Retail Co", "company_name": "Other"}) == "Retail Co"
        assert readable_wp_company({"company_name": "Retail Co"}) == "Retail Co"
        assert readable_wp_company({"company_name": "4012"}) == ""
        assert readable_wp_company({}) == ""


class TestWpReviewDetailsSummaryKey:
    def test_cold_details_point_at_the_summary_items_they_repeat(self) -> None:
        details = build_wp_review_details(_cold_collected(), subtype="CONSTRUCTION_COLD")
        tagged = {d["key"]: d.get("summaryKey") for d in details}
        assert tagged["brand"] == "brand"
        assert tagged["mall"] == "mall"
        assert tagged["unit_codes"] == "unit"
        assert tagged["work_permit_type"] == "request_type"
        assert tagged["type_work"] == "type_of_work"
        assert tagged["schedule_dates"] == "schedule"
        assert tagged["schedule_time"] == "schedule"
        assert tagged["list_equipment"] is None
        assert tagged["responsible_person"] is None

    def test_ops_schedule_fields_fold_into_schedule(self) -> None:
        details = build_wp_review_details(_ops_collected(), subtype="OPERATIONS")
        tagged = {d["key"]: d.get("summaryKey") for d in details}
        assert tagged["typeofwork"] == "type_of_work"
        assert tagged["schedule_dates"] == "schedule"
        assert tagged["schedule_time"] == "schedule"
        assert tagged["entry_gate"] is None
        assert not {"effective_starting", "firstperiod", "secondperiod"} & set(tagged)


def _keys(rows: list) -> list[str]:
    return [r["key"] for r in rows]


def _values(rows: list) -> dict[str, str]:
    return {r["key"]: r["value"] for r in rows}


class TestWpReviewDetailsCanonicalRows:
    def test_construction_cold_full_rows_have_no_raw_codes(self) -> None:
        collected = {
            **_cold_collected(),
            "sub_category": "CONSTRUCTION_COLD",
            "company": "Fawaz Abdulaziz Al Hokair and Partners CO",
            "company_lease_code": "t0099892",
            "company_name": "4012",
            "lease_code": "t0099892",
            "start_date": "2026-09-25T00:00:00.000Z",
            "end_date": "2026-09-26T00:00:00.000Z",
            "planneddate": {
                "worktimedate": "14/10/2026",
                "completionworkdate": "19/10/2026",
                "worktimefrom": "2026-10-14T12:07:00.000Z",
                "worktimefromLT": "05:37 PM",
                "worktimeto": "2026-10-14T14:07:00.000Z",
                "worktimetoLT": "07:37 PM",
                "completionfrom": "2026-10-14T15:07:00.000Z",
                "completionfromLT": "08:37 PM",
                "completionto": "2026-10-14T16:07:00.000Z",
                "completiontoLT": "09:37 PM",
            },
        }
        rows = build_wp_review_details(collected, subtype="CONSTRUCTION_COLD")
        values = _values(rows)
        assert _keys(rows)[:8] == [
            "company",
            "brand",
            "mall",
            "unit_codes",
            "lease_code",
            "work_permit_type",
            "type_work",
            "schedule_dates",
        ]
        assert values["company"] == "Fawaz Abdulaziz Al Hokair and Partners CO"
        assert values["lease_code"] == "t0099892"
        assert values["work_permit_type"] == "Construction – Cold Work"
        assert values["schedule_dates"] == "14/10/2026 – 19/10/2026"
        assert values["schedule_time"] == "05:37 PM – 07:37 PM"
        assert values["schedule_second_period"] == "08:37 PM – 09:37 PM"
        shown = " ".join(values.values())
        assert "CONSTRUCTION_COLD" not in shown
        assert "T12:07" not in shown
        assert "4012" not in shown
        assert not {"sub_category", "start_date", "end_date", "company_name"} & set(values)

    def test_company_from_lease_label_when_company_name_is_numeric(self) -> None:
        collected = {
            "mall": "Mall of Dhahran",
            "brand": "Zara",
            "lease_code": "t0099892",
            "company_name": "4012",
            "lease_brand_mall": "t0099892-Zara-Mall of Dhahran-Retail Co--شركة التجزئة",
            "work_permit_type": "CONSTRUCTION_HOT",
        }
        values = _values(build_wp_review_details(collected, subtype="CONSTRUCTION_HOT"))
        assert values["company"] == "Retail Co"

    def test_location_rows_lead_with_company_brand_mall_unit_then_lease(self) -> None:
        collected = {
            **_cold_collected(),
            "lease_code": "t0099892",
            "company": "Retail Co",
            "company_lease_code": "t0099892",
        }
        rows = build_wp_review_details(collected, subtype="CONSTRUCTION_COLD")
        assert _keys(rows)[:5] == ["company", "brand", "mall", "unit_codes", "lease_code"]
        assert rows[0]["summaryKey"] == "company"
        assert rows[4]["summaryKey"] == "lease"

    def test_iso_only_times_are_hidden(self) -> None:
        collected = {
            "work_permit_type": "MAINTENANCE_ROOF_ACCESS",
            "planneddate": {
                "worktimedate": "10/10/2026",
                "completionworkdate": "10/10/2026",
                "worktimefrom": "2026-10-10T11:00:00.000Z",
                "worktimeto": "2026-10-10T12:00:00.000Z",
            },
        }
        values = _values(build_wp_review_details(collected, subtype="MAINTENANCE_ROOF_ACCESS"))
        assert values["schedule_dates"] == "10/10/2026"
        assert "schedule_time" not in values

    def test_maintenance_hot_collapses_equal_dates_and_times(self) -> None:
        collected = {
            "work_permit_type": "MAINTENANCE_HOT",
            "sub_category": "MAINTENANCE_HOT",
            "department": "operations",
            "planneddate": {
                "worktimedate": "10/10/2026",
                "completionworkdate": "10/10/2026",
                "worktimefromLT": "03:28 PM",
                "worktimetoLT": "03:28 PM",
            },
        }
        rows = build_wp_review_details(collected, subtype="MAINTENANCE_HOT")
        values = _values(rows)
        assert values["schedule_dates"] == "10/10/2026"
        assert values["schedule_time"] == "03:28 PM"
        assert values["work_permit_type"] == "Maintenance – Hot Work"
        assert values["department"] == "Operations"
        assert "sub_category" not in values

    def test_construction_hot_safety_and_workers(self) -> None:
        collected = {
            "work_permit_type": "CONSTRUCTION_HOT",
            "measureofsafety": {
                "fire_extinguisher": "No - لا",
                "fire_hoses": "Yes - نعم",
                "sprinker_on": "",
            },
            "workers": {
                "worker": [
                    {
                        "name": "Ali",
                        "designation": "Welder",
                        "worker_qualification": "Certified",
                        "iqama_number": "2345678901",
                        "document_id": "doc-9",
                    },
                    {"name": "Omar", "designation": "Helper"},
                ],
                "instructor_name": "Sami",
            },
        }
        rows = build_wp_review_details(collected, subtype="CONSTRUCTION_HOT")
        by_key = {r["key"]: r for r in rows}
        assert by_key["measureofsafety.fire_extinguisher"]["label"] == "Fire extinguisher"
        assert by_key["measureofsafety.fire_extinguisher"]["value"] == "No"
        assert by_key["measureofsafety.fire_hoses"]["value"] == "Yes"
        assert "measureofsafety.sprinker_on" not in by_key
        assert all(r["section"] == "safety" for k, r in by_key.items() if k.startswith("measure"))
        assert by_key["workers.1"]["value"] == "Ali · Welder · Certified · Iqama 2345678901"
        assert by_key["workers.2"]["value"] == "Omar · Helper"
        assert by_key["workers.instructor"]["value"] == "Sami"
        shown = " ".join(r["value"] for r in rows)
        assert "لا" not in shown and "نعم" not in shown
        assert "doc-9" not in shown

    def test_cold_list_workers_one_row_each_without_ids(self) -> None:
        collected = {
            "work_permit_type": "MAINTENANCE_COLD",
            "workers": [
                {
                    "full_name": "Ali",
                    "designation": "Electrician",
                    "company_name": "ABC Contracting",
                    "phone": "500000000",
                    "country_code": "+966",
                    "iqama_number": "111",
                    "document_id": "doc-1",
                }
            ],
        }
        values = _values(build_wp_review_details(collected, subtype="MAINTENANCE_COLD"))
        assert values["workers.1"] == "Ali · Electrician · ABC Contracting · Phone 500000000 · Iqama 111"
        assert "+966" not in values["workers.1"]

    def test_sections_tag_every_row(self) -> None:
        rows = build_wp_review_details(_cold_collected(), subtype="CONSTRUCTION_COLD")
        sections = {r["key"]: r["section"] for r in rows}
        assert sections["mall"] == "location"
        assert sections["work_permit_type"] == "permit"
        assert sections["schedule_dates"] == "schedule"
        assert sections["list_equipment"] == "work"
        assert sections["workers.1"] == "workers"

    def test_repeat_calls_are_identical(self) -> None:
        first = build_wp_review_details(_cold_collected(), subtype="CONSTRUCTION_COLD")
        second = build_wp_review_details(_cold_collected(), subtype="CONSTRUCTION_COLD")
        assert first == second

    def test_groups_keep_source_order_and_omit_empty_sections(self) -> None:
        rows = build_wp_review_details(_cold_collected(), subtype="CONSTRUCTION_COLD")
        sections = group_wp_detail_sections(rows)
        assert [section["id"] for section in sections] == [
            "request",
            "work",
            "schedule",
            "workers",
        ]
        assert [section["icon"] for section in sections] == [
            "location",
            "tool",
            "calendar",
            "person",
        ]
        request = next(section for section in sections if section["id"] == "request")
        labels = [field["label"] for field in request["fields"]]
        assert labels.index("Brand") < labels.index("Mall") < labels.index("Permit / Request Type")
        work = next(section for section in sections if section["id"] == "work")
        assert "Worker 1" not in [field["label"] for field in work["fields"]]
        workers = next(section for section in sections if section["id"] == "workers")
        assert [field["label"] for field in workers["fields"]] == [
            "Worker 1",
            "Worker 2",
            "Worker 3",
        ]

    def test_files_keep_incoming_order_then_workers_and_drop_duplicates(self) -> None:
        files = build_wp_detail_files(
            {
                "workers": [
                    {"full_name": "Second", "document_id": "doc-2"},
                    {"full_name": "Already attached", "document_id": "doc-1"},
                ]
            },
            [
                {"document_id": "doc-b", "file_name": "second.pdf"},
                {"document_id": "doc-a", "file_name": "first.pdf"},
                {"document_id": "doc-1", "file_name": "iqama.pdf"},
            ],
        )
        assert [file["documentId"] for file in files] == [
            "doc-b",
            "doc-a",
            "doc-1",
            "doc-2",
        ]
        assert files[2]["fileName"] == "iqama.pdf"
        assert files[3]["label"] == "Iqama — Second"


def _history(*rows: dict) -> list[dict]:
    return list(rows)


_ALI_CREATED = {
    "status": "CREATED",
    "operator_name": "Ali Hassan",
    "operator_role": "PRIMARY_TENANT_CONTACT",
    "user_type": "ADMIN",
    "created_at": "2026-09-28T08:00:00.000Z",
}


class TestWpHistorySentence:
    def test_submitted_only_names_the_submitter(self) -> None:
        assert compose_wp_history_sentence(_history(_ALI_CREATED)) == (
            "**Ali Hassan** (Tenant) submitted this request."
        )

    def test_submitter_then_latest_step(self) -> None:
        sentence = compose_wp_history_sentence(
            _history(
                _ALI_CREATED,
                {"status": "APPROVED", "operator_name": "Omar", "operator_role": "FM_MANAGER",
                 "created_at": "2026-09-28T12:00:00.000Z"},
                {"status": "APPROVED", "operator_name": "Sara", "operator_role": "DD_ENGINEER",
                 "created_at": "2026-09-29T10:15:00.000Z"},
            )
        )
        assert sentence == (
            "**Ali Hassan** (Tenant) submitted this request, and "
            "**Sara** (DD Engineer) approved it."
        )

    def test_latest_step_without_a_submission_row(self) -> None:
        sentence = compose_wp_history_sentence(
            _history({"status": "APPROVED", "operator_name": "Sara", "operator_role": 12,
                      "created_at": "2026-09-29T10:15:00.000Z"})
        )
        assert sentence == "**Sara** (DD Engineer) approved this request."

    def test_creator_fills_in_the_submitter(self) -> None:
        sentence = compose_wp_history_sentence(
            _history({"status": "ACTION_REQUIRED", "operator_role": "FM_MANAGER"}),
            creator={"name": "Ali Hassan", "role": "PRIMARY_TENANT_CONTACT",
                     "created_at": "2026-09-28T08:00:00Z"},
        )
        assert sentence == (
            "**Ali Hassan** (Tenant) submitted this request, and "
            "the **FM Manager** asked for more information."
        )

    def test_creator_alone(self) -> None:
        assert compose_wp_history_sentence(None, creator={"name": "Ali Hassan"}) == (
            "**Ali Hassan** submitted this request."
        )

    def test_tenant_user_type_is_a_role(self) -> None:
        sentence = compose_wp_history_sentence(
            _history({"status": "CREATED", "operator_name": "Ali", "user_type": "TENANT"})
        )
        assert sentence == "**Ali** (Tenant) submitted this request."

    def test_form_saves_and_skips_are_ignored(self) -> None:
        sentence = compose_wp_history_sentence(
            _history(
                {"status": "CREATED", "operator_name": "Ali", "user_type": "TENANT"},
                {"status": "UPDATE", "operator_name": "Omar"},
                {"status": "SKIPPED"},
                {"status": "RE_EXAMINE", "operator_name": "Omar"},
            )
        )
        assert sentence == "**Ali** (Tenant) submitted this request."

    def test_no_actor_goes_passive_without_inventing_a_person(self) -> None:
        sentence = compose_wp_history_sentence(
            _history(
                {"status": "CREATED", "operator_name": "Ali"},
                {"status": "REJECTED", "created_at": "not-a-date"},
            )
        )
        assert sentence == "**Ali** submitted this request, and it was rejected."

    def test_dates_are_never_shown(self) -> None:
        sentence = compose_wp_history_sentence(
            _history(
                _ALI_CREATED,
                {"status": "APPROVED", "operator_name": "Sara", "operator_role": "DD_ENGINEER",
                 "created_at": "2026-09-29T10:15:00.000Z"},
            )
        )
        assert "/2026" not in sentence
        assert " on **" not in sentence

    @pytest.mark.parametrize(
        "role", ["COMPANY_ADMIN", "COMPANY_ADMIN_CENTRAL", "AUTHORIZED_SIGNATORY", "STORE MANAGER"]
    )
    def test_tenant_side_roles_read_tenant(self, role: str) -> None:
        sentence = compose_wp_history_sentence(
            _history({"status": "CREATED", "operator_name": "Tenant Central",
                      "operator_role": role, "user_type": "ADMIN"})
        )
        assert sentence == "**Tenant Central** (Tenant) submitted this request."

    def test_leasing_manager_keeps_its_label(self) -> None:
        sentence = compose_wp_history_sentence(
            _history({"status": "CREATED", "operator_name": "Lina",
                      "operator_role": "LEASING_MANAGER"})
        )
        assert sentence == "**Lina** (Leasing Manager) submitted this request."

    @pytest.mark.parametrize(
        "reply", ["RE_EXAMINE", "UPDATE", "IN_PROCESS", "RE_SUBMITTED", "SUBMITTED", "SUBMIT"]
    )
    def test_tenant_reply_to_send_back_is_a_resubmission(self, reply: str) -> None:
        sentence = compose_wp_history_sentence(
            _history(
                {"status": "CREATED", "operator_name": "Tenant Central",
                 "operator_role": "COMPANY_ADMIN"},
                {"status": "ACTION_REQUIRED", "operator_name": "IT User7",
                 "operator_role": "MALL_MANAGER"},
                {"status": reply, "operator_name": "Tenant Central",
                 "operator_role": "COMPANY_ADMIN"},
            )
        )
        assert sentence == (
            "**Tenant Central** (Tenant) submitted this request, and "
            "resubmitted it with the requested information."
        )

    def test_submitted_again_after_mall_manager_send_back(self) -> None:
        # SR 341529: FM approved, the Mall Manager sent it back, and the
        # tenant's resubmission came back as a second "Submitted" row.
        sentence = compose_wp_history_sentence(
            _history(
                {"status": "SUBMITTED", "operator_name": "Tenant Central",
                 "operator_role": "COMPANY_ADMIN_CENTRAL"},
                {"status": "APPROVED", "operator_name": "IT User6",
                 "operator_role": "FM_MANAGER", "comment": "confirm_approve_wp"},
                {"status": "ACTION_REQUIRED", "operator_name": "IT User7",
                 "operator_role": "MALL_MANAGER", "comment": "ask_more_info_wp"},
                {"status": "SUBMITTED", "operator_name": "Tenant Central",
                 "operator_role": "COMPANY_ADMIN_CENTRAL"},
            )
        )
        assert sentence == (
            "**Tenant Central** (Tenant) submitted this request, and "
            "resubmitted it with the requested information."
        )

    def test_repeat_submission_without_send_back_is_not_a_step(self) -> None:
        sentence = compose_wp_history_sentence(
            _history(
                {"status": "SUBMITTED", "operator_name": "Tenant Central",
                 "operator_role": "COMPANY_ADMIN"},
                {"status": "APPROVED", "operator_name": "Sara", "operator_role": "DD_ENGINEER"},
                {"status": "SUBMITTED", "operator_name": "Tenant Central",
                 "operator_role": "COMPANY_ADMIN"},
            )
        )
        assert sentence == (
            "**Tenant Central** (Tenant) submitted this request, and "
            "**Sara** (DD Engineer) approved it."
        )

    def test_unanswered_send_back_names_the_reviewer(self) -> None:
        sentence = compose_wp_history_sentence(
            _history(
                {"status": "CREATED", "operator_name": "Tenant Central",
                 "operator_role": "COMPANY_ADMIN"},
                {"status": "ACTION_REQUIRED", "operator_name": "IT User7",
                 "operator_role": "MALL_MANAGER"},
            )
        )
        assert sentence == (
            "**Tenant Central** (Tenant) submitted this request, and "
            "**IT User7** (Mall Manager) asked for more information."
        )

    def test_resubmission_by_someone_else_keeps_their_name(self) -> None:
        sentence = compose_wp_history_sentence(
            _history(
                {"status": "CREATED", "operator_name": "Tenant Central",
                 "operator_role": "COMPANY_ADMIN"},
                {"status": "REQUESTED_RE_UPLOAD", "operator_name": "IT User7",
                 "operator_role": "MALL_MANAGER"},
                {"status": "UPDATE", "operator_name": "Omar", "user_type": "TENANT"},
            )
        )
        assert sentence == (
            "**Tenant Central** (Tenant) submitted this request, and "
            "**Omar** (Tenant) resubmitted it with the requested information."
        )

    def test_later_decision_after_resubmission_wins(self) -> None:
        sentence = compose_wp_history_sentence(
            _history(
                {"status": "CREATED", "operator_name": "Tenant Central",
                 "operator_role": "COMPANY_ADMIN"},
                {"status": "ACTION_REQUIRED", "operator_name": "IT User7"},
                {"status": "RE_EXAMINE", "operator_name": "Tenant Central"},
                {"status": "APPROVED", "operator_name": "Sara", "operator_role": "DD_ENGINEER"},
                {"status": "UPDATE", "operator_name": "Sara"},
            )
        )
        assert sentence == (
            "**Tenant Central** (Tenant) submitted this request, and "
            "**Sara** (DD Engineer) approved it."
        )

    def test_history_rows_are_not_mutated(self) -> None:
        rows = _history(
            {"status": "CREATED", "operator_name": "A"},
            {"status": "ACTION_REQUIRED", "operator_name": "B"},
            {"status": "RE_EXAMINE", "operator_name": "A"},
        )
        compose_wp_history_sentence(rows)
        assert rows[2]["status"] == "RE_EXAMINE"

    def test_nothing_known_gives_no_sentence(self) -> None:
        assert compose_wp_history_sentence(None) == ""
        assert compose_wp_history_sentence([{"status": "UPDATE"}, "junk"]) == ""  # type: ignore[list-item]
        assert compose_wp_history_sentence(None, creator={"name": " "}) == ""

    def test_unknown_status_is_humanized(self) -> None:
        sentence = compose_wp_history_sentence(
            _history({"status": "ON_HOLD", "operator_name": "Sara"})
        )
        assert sentence.startswith("**Sara** moved this request to **")
        assert "ON_HOLD" not in sentence

    def test_message_ends_with_history_then_question(self) -> None:
        msg = compose_wp_review_message(
            _cold_collected(),
            workflow_stage="WP_FM_REVIEW",
            request_history=_history(
                _ALI_CREATED,
                {"status": "APPROVED", "operator_name": "Sara", "operator_role": "DD_ENGINEER"},
            ),
        )
        parts = msg.split("\n\n")
        assert len(parts) == 4
        assert parts[0] == "Hi!"
        assert parts[1].startswith("**Zara** wants to carry out")
        assert parts[2] == (
            "This **Construction – Cold Work** permit is now pending **FM review**."
        )
        assert parts[3] == (
            "**Ali Hassan** (Tenant) submitted this request, and "
            "**Sara** (DD Engineer) approved it. What would you like to do?"
        )

    def test_message_without_history_keeps_question_on_pending_line(self) -> None:
        parts = compose_wp_review_message(
            _cold_collected(), workflow_stage="WP_DD_REVIEW"
        ).split("\n\n")
        assert len(parts) == 3
        assert parts[2].endswith("pending **DD review**. What would you like to do?")
        assert "Last action" not in "".join(parts)

