"""Unit tests for fitout_progress_schema.py (phase 1).

Coverage
--------
TestStages                  — stage registry, required fields, terminal stages
TestGetMissingFields        — the one-question rule for CREATE_SR and APPROVE_SR
TestRolePermissions         — DD_ENGINEER-only create and approve
TestTitle                   — platform title convention
TestFieldSets               — allowed / backend-derived / optional field sets
TestExtractedFieldsModel    — backend-only and unknown keys are stripped
"""

from __future__ import annotations

from app.agents.schemas.fitout_progress_schema import (
    ALLOWED_EXTRACTED_FIELDS,
    BACKEND_DERIVED_FIELDS,
    BACKEND_ONLY_FIELDS,
    COMMENT_FIELD,
    OPTIONAL_FIELDS,
    PROGRESS_CONFIRMED_KEY,
    PROGRESS_FIELD,
    STAGE_REGISTRY,
    SUB_CATEGORY,
    TERMINAL_STAGES,
    USER_SUPPLIED_FIELDS,
    WORKFLOW_STAGES,
    FitoutProgressExtractedFields,
    build_title,
    get_missing_fields,
    get_required_fields,
    role_can_act_on_stage,
    role_can_approve,
)

LEASE = {
    "lease_brand_mall": "t0105712 - Brand Under Armour - Jawharat Jeddah",
    "lease_code": "t0105712",
    "lease_id": 95404,
}


# ---------------------------------------------------------------------------
# TestStages
# ---------------------------------------------------------------------------


class TestStages:
    def test_sub_category(self) -> None:
        assert SUB_CATEGORY == "FITOUT_PROGRESS"

    def test_two_working_stages(self) -> None:
        assert WORKFLOW_STAGES == ("CREATE_SR", "APPROVE_SR")
        assert set(STAGE_REGISTRY) == {"CREATE_SR", "APPROVE_SR"}

    def test_create_requires_lease_and_percentage(self) -> None:
        assert get_required_fields("CREATE_SR") == (
            "lease_brand_mall",
            "lease_code",
            "lease_id",
            PROGRESS_FIELD,
        )

    def test_approve_requires_only_the_percentage(self) -> None:
        assert get_required_fields("APPROVE_SR") == (PROGRESS_FIELD,)

    def test_unknown_stage_requires_nothing(self) -> None:
        assert get_required_fields("FM_REVIEW") == ()

    def test_terminal_stages(self) -> None:
        assert TERMINAL_STAGES == {"SR_CREATED", "SR_APPROVED", "SR_REJECTED"}

    def test_no_cancelled_stage(self) -> None:
        # The platform workflow has no CANCELLED action for this sub-category —
        # a cancel PATCH would be a 400, so there is no such stage to reach.
        assert "CANCELLED" not in TERMINAL_STAGES
        assert "CANCELLED" not in STAGE_REGISTRY

    def test_no_documents_on_either_stage(self) -> None:
        for stage in STAGE_REGISTRY.values():
            assert stage.required_documents == ()


# ---------------------------------------------------------------------------
# TestGetMissingFields
# ---------------------------------------------------------------------------


class TestGetMissingFields:
    def test_empty_create_asks_lease_and_percentage(self) -> None:
        assert get_missing_fields("CREATE_SR", {}) == [
            "lease_brand_mall",
            PROGRESS_FIELD,
        ]

    def test_create_with_lease_asks_only_the_percentage(self) -> None:
        assert get_missing_fields("CREATE_SR", dict(LEASE)) == [PROGRESS_FIELD]

    def test_create_complete(self) -> None:
        collected = {**LEASE, PROGRESS_FIELD: "82", PROGRESS_CONFIRMED_KEY: True}
        assert get_missing_fields("CREATE_SR", collected) == []

    def test_create_zero_percent_is_an_answer(self) -> None:
        # "0" is a real answer (fit-out not started); only "" / None are missing.
        collected = {**LEASE, PROGRESS_FIELD: "0", PROGRESS_CONFIRMED_KEY: True}
        assert get_missing_fields("CREATE_SR", collected) == []

    def test_create_empty_string_percentage_is_missing(self) -> None:
        collected = {**LEASE, PROGRESS_FIELD: ""}
        assert get_missing_fields("CREATE_SR", collected) == [PROGRESS_FIELD]

    def test_create_does_not_ask_for_comment(self) -> None:
        collected = {**LEASE, PROGRESS_FIELD: "82"}
        assert COMMENT_FIELD not in get_missing_fields("CREATE_SR", collected)

    def test_approve_accepts_lease_code_alone(self) -> None:
        # The approver is looking at the SR; never re-ask for the lease.
        collected = {
            "lease_code": "t0105712",
            PROGRESS_FIELD: "65",
            PROGRESS_CONFIRMED_KEY: True,
        }
        assert get_missing_fields("APPROVE_SR", collected) == []

    def test_approve_accepts_lease_id_alone(self) -> None:
        collected = {
            "lease_id": 95404,
            PROGRESS_FIELD: "65",
            PROGRESS_CONFIRMED_KEY: True,
        }
        assert get_missing_fields("APPROVE_SR", collected) == []

    def test_approve_asks_the_percentage_even_when_prefilled(self) -> None:
        # The stored payload pre-fills the number; approving it without asking
        # would skip the only question this flow has.
        collected = {"lease_code": "t0105712", PROGRESS_FIELD: "65"}
        assert get_missing_fields("APPROVE_SR", collected) == [PROGRESS_FIELD]

    def test_approve_satisfied_once_the_user_answers(self) -> None:
        collected = {
            "lease_code": "t0105712",
            PROGRESS_FIELD: "82",
            PROGRESS_CONFIRMED_KEY: True,
        }
        assert get_missing_fields("APPROVE_SR", collected) == []

    def test_create_asks_even_when_the_form_pre_filled_it(self) -> None:
        # The web form computes the same elapsed-time default this agent
        # suggests and writes it in as soon as a lease is picked, and the
        # form→chat bridge syncs it back. That is the form's arithmetic, not an
        # answer: counting it took a live session from the question straight to
        # "Recording fit-out progress at 100%".
        collected = {**LEASE, PROGRESS_FIELD: "100"}
        assert PROGRESS_CONFIRMED_KEY not in collected
        assert get_missing_fields("CREATE_SR", collected) == [PROGRESS_FIELD]

    def test_create_settles_once_the_user_answers(self) -> None:
        collected = {**LEASE, PROGRESS_FIELD: "100", PROGRESS_CONFIRMED_KEY: True}
        assert get_missing_fields("CREATE_SR", collected) == []

    def test_unknown_stage_asks_nothing(self) -> None:
        assert get_missing_fields("SR_APPROVED", {}) == []


# ---------------------------------------------------------------------------
# TestRolePermissions
# ---------------------------------------------------------------------------


class TestRolePermissions:
    def test_dd_engineer_can_create_and_approve(self) -> None:
        assert role_can_act_on_stage("DD_ENGINEER", "CREATE_SR")
        assert role_can_act_on_stage("DD_ENGINEER", "APPROVE_SR")
        assert role_can_approve("DD_ENGINEER")

    def test_mall_manager_cannot(self) -> None:
        assert not role_can_act_on_stage("MALL_MANAGER", "CREATE_SR")
        assert not role_can_approve("MALL_MANAGER")

    def test_operations_cannot(self) -> None:
        assert not role_can_act_on_stage("OPERATIONS", "APPROVE_SR")
        assert not role_can_approve("OPERATIONS")

    def test_unknown_role_cannot(self) -> None:
        assert not role_can_act_on_stage("", "CREATE_SR")
        assert not role_can_approve("TENANT")


# ---------------------------------------------------------------------------
# TestTitle
# ---------------------------------------------------------------------------


class TestTitle:
    def test_title_matches_platform_convention(self) -> None:
        assert build_title("t0105712") == "Fitout Progress - t0105712"

    def test_title_without_a_code(self) -> None:
        assert build_title("") == "Fitout Progress"
        assert build_title("   ") == "Fitout Progress"


# ---------------------------------------------------------------------------
# TestFieldSets
# ---------------------------------------------------------------------------


class TestFieldSets:
    def test_percentage_is_extractable(self) -> None:
        assert PROGRESS_FIELD in ALLOWED_EXTRACTED_FIELDS
        assert PROGRESS_FIELD not in BACKEND_DERIVED_FIELDS

    def test_comment_is_singular_and_optional(self) -> None:
        # The FITOUT_PROGRESS form uses `comment`; the fitout *confirmation*
        # sub-categories use `comments`. Sending the wrong key is a 400.
        assert COMMENT_FIELD == "comment"
        assert OPTIONAL_FIELDS == {"comment"}

    def test_user_supplies_only_the_percentage_and_lease_hints(self) -> None:
        assert USER_SUPPLIED_FIELDS == {PROGRESS_FIELD, COMMENT_FIELD}

    def test_dates_are_backend_only(self) -> None:
        for field in (
            "fitout_start_date",
            "fitout_end_date",
            "actual_fitout_start_date",
            "base_fitout_end_date",
        ):
            assert field in BACKEND_ONLY_FIELDS

    def test_recorded_progress_is_backend_only(self) -> None:
        assert "recorded_fitout_progress" in BACKEND_ONLY_FIELDS

    def test_backend_only_is_a_subset_of_backend_derived(self) -> None:
        assert BACKEND_ONLY_FIELDS <= BACKEND_DERIVED_FIELDS


# ---------------------------------------------------------------------------
# TestExtractedFieldsModel
# ---------------------------------------------------------------------------


class TestExtractedFieldsModel:
    def test_keeps_the_percentage(self) -> None:
        model = FitoutProgressExtractedFields.model_validate(
            {"fields": {PROGRESS_FIELD: {"value": "82", "confidence": 0.9}}}
        )
        assert model.to_flat_dict() == {PROGRESS_FIELD: "82"}
        assert model.to_confidence_dict() == {PROGRESS_FIELD: 0.9}

    def test_strips_backend_only_fields(self) -> None:
        model = FitoutProgressExtractedFields.model_validate(
            {
                "fields": {
                    PROGRESS_FIELD: {"value": "82"},
                    "lease_id": {"value": "95404"},
                    "fitout_start_date": {"value": "2026-07-15"},
                    "tenant_profile_id": {"value": "116"},
                }
            }
        )
        assert set(model.to_flat_dict()) == {PROGRESS_FIELD}

    def test_strips_unknown_fields(self) -> None:
        model = FitoutProgressExtractedFields.model_validate(
            {"fields": {"delay_reason": {"value": "material shortage"}}}
        )
        assert model.to_flat_dict() == {}

    def test_keeps_lease_hints_and_comment(self) -> None:
        model = FitoutProgressExtractedFields.model_validate(
            {
                "fields": {
                    "lease_code": {"value": "t0105712"},
                    "brand": {"value": "Under Armour"},
                    COMMENT_FIELD: {"value": "MEP finished last week"},
                }
            }
        )
        assert set(model.to_flat_dict()) == {"lease_code", "brand", COMMENT_FIELD}
