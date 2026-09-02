"""Unit tests — verify handover schema completeness and correctness.

These tests act as a contract: if any stage, field, document, or permission
mapping is accidentally removed or renamed, a test will fail loudly before the
change reaches production.
"""

from __future__ import annotations

import pytest

from app.agents.schemas.handover_schema import (
    ALLOWED_EXTRACTED_FIELDS,
    ALL_DOCUMENT_TYPES,
    ASSIGNMENT_TYPE,
    BACKEND_DERIVED_FIELDS,
    CREATE_SR_STAGE,
    CREATE_HANDED_OVER_SR_STAGE,
    FM_ALLOWED_DOCUMENTS,
    FM_REVIEW_STAGE,
    HANDED_OVER_ALLOWED_DOCUMENTS,
    PERMISSION_MAP,
    RDD_ALLOWED_DOCUMENTS,
    RDD_REQUIRED_DOCUMENTS,
    RDD_REVIEW_STAGE,
    SERVICE_CATEGORY,
    STAGE_REGISTRY,
    SUB_CATEGORY,
    SUB_CATEGORY_HANDED_OVER,
    USER_SUPPLIED_FIELDS,
    WORKFLOW_STAGES,
    HandoverExtractionSchema,
    StageDefinition,
    get_missing_fields,
    get_required_documents,
    get_required_fields,
    get_stage,
    role_can_act_on_stage,
)


# ── Constants ─────────────────────────────────────────────────────────────────


class TestConstants:
    def test_service_category(self) -> None:
        assert SERVICE_CATEGORY == "FIT_OUT_AND_HANDOVER"

    def test_sub_category(self) -> None:
        assert SUB_CATEGORY == "HANDOVER"

    def test_assignment_type(self) -> None:
        assert ASSIGNMENT_TYPE == "workflow"


# ── Stage registry ────────────────────────────────────────────────────────────


class TestStageRegistry:
    def test_all_stages_present(self) -> None:
        assert set(STAGE_REGISTRY.keys()) == {
            "CREATE_SR", "FM_REVIEW", "RDD_REVIEW", "CREATE_HANDED_OVER_SR"
        }

    def test_workflow_stages_ordered(self) -> None:
        assert set(WORKFLOW_STAGES) == {"CREATE_SR", "FM_REVIEW", "RDD_REVIEW", "CREATE_HANDED_OVER_SR"}

    def test_registry_values_are_stage_definitions(self) -> None:
        for stage_def in STAGE_REGISTRY.values():
            assert isinstance(stage_def, StageDefinition)

    def test_stage_names_consistent_with_keys(self) -> None:
        for key, stage_def in STAGE_REGISTRY.items():
            assert stage_def.stage == key, (
                f"Key '{key}' does not match StageDefinition.stage '{stage_def.stage}'"
            )


# ── CREATE_SR stage ───────────────────────────────────────────────────────────


class TestCreateSRStage:
    _EXPECTED_FIELDS = (
        "tenant_profile_id",
        "property_id",
        "lease_code",
        "lease_id",
        "brand_id",
        "mall",
        "brand",
        "lease",
        "unit_codes",
        "city",
        "contracted_area",
        "already_handovered",  # Handover Type* — asked first after lease resolution
        "title",
        "description",
        "startDate",
        "inspectionTime",    # Fix 2: optional HH:MM time field
        "endDate",
        "inspectionEndTime",  # optional HH:MM — end of the inspection window
        "inspection_done_by",
        "notes",
        "comments",          # Fix 4: comments re-added as required (optional value allowed)
    )

    def test_role(self) -> None:
        assert CREATE_SR_STAGE.role == "MALL_MANAGER"

    def test_required_fields_complete(self) -> None:
        assert set(CREATE_SR_STAGE.required_fields) == set(self._EXPECTED_FIELDS)

    def test_required_fields_count(self) -> None:
        assert len(CREATE_SR_STAGE.required_fields) == len(self._EXPECTED_FIELDS)

    def test_comments_in_required_fields(self) -> None:
        """comments is now a required (optional) field — bot asks for it as a distinct turn (Fix 4)."""
        assert "comments" in CREATE_SR_STAGE.required_fields

    def test_no_required_documents(self) -> None:
        assert CREATE_SR_STAGE.required_documents == ()


# ── FM_REVIEW stage ───────────────────────────────────────────────────────────


class TestFMReviewStage:
    _EXPECTED_FIELDS = (
        "unit_readiness_date",
        "comments",  # comments re-added as required (optional value allowed)
    )
    _EXPECTED_DOCS = (
        "SR_HANDOVER_CHECKLIST",
        "SR_HANDOVER_SITE_SURVEY",
        "SR_COP_CHECKLIST_OTHER",  # Fix 2: was SR_HANDOVER_OTHER
    )

    def test_role(self) -> None:
        assert FM_REVIEW_STAGE.role == "FM_MANAGER"

    def test_required_fields_complete(self) -> None:
        assert set(FM_REVIEW_STAGE.required_fields) == set(self._EXPECTED_FIELDS)

    def test_required_documents_complete(self) -> None:
        assert set(FM_REVIEW_STAGE.required_documents) == set(self._EXPECTED_DOCS)

    def test_required_documents_count(self) -> None:
        assert len(FM_REVIEW_STAGE.required_documents) == 3

    def test_cop_checklist_in_fm_docs(self) -> None:
        """Fix 2: SR_COP_CHECKLIST_OTHER replaced SR_HANDOVER_OTHER in FM stage."""
        assert "SR_COP_CHECKLIST_OTHER" in FM_REVIEW_STAGE.required_documents
        assert "SR_COP_CHECKLIST_OTHER" in FM_ALLOWED_DOCUMENTS
        assert "SR_HANDOVER_OTHER" not in FM_REVIEW_STAGE.required_documents
        assert "SR_HANDOVER_OTHER" not in FM_ALLOWED_DOCUMENTS


# ── RDD_REVIEW stage ──────────────────────────────────────────────────────────


class TestRDDReviewStage:
    _EXPECTED_FIELDS = (
        "guideLineLink",
        # Meeting Report outcome — asked with Approve / Approve-with-changes chips
        # once that report is uploaded. rdd_upload_doc_type is gone: uploads moved
        # to the document_requirement card, which shows all types at once.
        "document_status",
        # trading_date intentionally excluded — auto-computed as fitout_end_date + 8 days
        "actual_handover_date",
        "fitout_start_date",
        "fitout_end_date",
        "comments",  # Fix 4: comments re-added as required (optional value allowed)
    )
    _EXPECTED_DOCS = (
        "DR_SR_HANDOVER_REPORT",         # mandatory
        "SR_REJECTED_HANDOVER_REPORT",   # optional additional
        "SR_HANDOVER_OTHER",             # optional additional
    )

    def test_role(self) -> None:
        assert RDD_REVIEW_STAGE.role == "DD_ENGINEER"

    def test_required_fields_complete(self) -> None:
        assert set(RDD_REVIEW_STAGE.required_fields) == set(self._EXPECTED_FIELDS)

    def test_document_status_asked_after_guideline_and_before_dates(self) -> None:
        """The Meeting Report outcome is collected between the guideline link
        and the three handover dates — see get_missing_fields' docstring for why
        it needs no condition here."""
        fields = RDD_REVIEW_STAGE.required_fields
        assert fields.index("document_status") == fields.index("guideLineLink") + 1
        assert fields.index("document_status") < fields.index("actual_handover_date")

    def test_required_documents_complete(self) -> None:
        assert set(RDD_REVIEW_STAGE.required_documents) == set(self._EXPECTED_DOCS)

    def test_required_documents_count(self) -> None:
        assert len(RDD_REVIEW_STAGE.required_documents) == 3


# ── CREATE_HANDED_OVER_SR stage (Path B) ─────────────────────────────────────


class TestCreateHandedOverSRStage:
    _EXPECTED_FIELDS = (
        "tenant_profile_id",
        "property_id",
        "lease_code",
        "lease_id",
        "brand_id",
        "mall",
        "brand",
        "lease",
        "unit_codes",
        "city",
        "contracted_area",
        "title",
        "actual_shop_handover_date",
        "actual_fitout_start_date",
        "actual_fitout_end_date",
        "actual_trading_date",
        "comments",
    )

    def test_role(self) -> None:
        assert CREATE_HANDED_OVER_SR_STAGE.role == "MALL_MANAGER"

    def test_required_fields_complete(self) -> None:
        assert set(CREATE_HANDED_OVER_SR_STAGE.required_fields) == set(self._EXPECTED_FIELDS)

    def test_no_required_documents(self) -> None:
        assert CREATE_HANDED_OVER_SR_STAGE.required_documents == ()

    def test_sub_category_constant(self) -> None:
        assert SUB_CATEGORY_HANDED_OVER == "HANDED_OVER"

    def test_path_b_stage_in_registry(self) -> None:
        from app.agents.schemas.handover_schema import STAGE_REGISTRY
        assert "CREATE_HANDED_OVER_SR" in STAGE_REGISTRY


# ── Derived field sets ────────────────────────────────────────────────────────


class TestDerivedFieldSets:
    def test_allowed_extracted_fields_is_union_of_all_stages(self) -> None:
        expected = frozenset(
            CREATE_SR_STAGE.required_fields
            + FM_REVIEW_STAGE.required_fields
            + RDD_REVIEW_STAGE.required_fields
            + ("document_status",)  # conditional Meeting Report path
        )
        assert ALLOWED_EXTRACTED_FIELDS == expected

    def test_backend_derived_fields_subset_of_allowed(self) -> None:
        assert BACKEND_DERIVED_FIELDS <= ALLOWED_EXTRACTED_FIELDS

    def test_backend_derived_fields_content(self) -> None:
        expected = {
            "tenant_profile_id",
            "property_id",
            "lease_id",
            "brand_id",
            "mall",
            "brand",
            "lease",
            "unit_codes",
            "city",
            "contracted_area",
        }
        assert BACKEND_DERIVED_FIELDS == frozenset(expected)

    def test_user_supplied_fields_disjoint_from_backend(self) -> None:
        assert USER_SUPPLIED_FIELDS.isdisjoint(BACKEND_DERIVED_FIELDS)

    def test_user_supplied_plus_backend_equals_allowed(self) -> None:
        assert USER_SUPPLIED_FIELDS | BACKEND_DERIVED_FIELDS == ALLOWED_EXTRACTED_FIELDS


# ── Document registries ───────────────────────────────────────────────────────


class TestDocumentRegistries:
    def test_fm_allowed_documents_matches_fm_stage(self) -> None:
        assert FM_ALLOWED_DOCUMENTS == FM_REVIEW_STAGE.required_documents

    def test_rdd_required_documents_matches_rdd_stage(self) -> None:
        # RDD_REQUIRED_DOCUMENTS is the mandatory subset; RDD_REVIEW_STAGE also
        # lists optional uploads (SR_REJECTED_HANDOVER_REPORT, SR_HANDOVER_OTHER).
        assert frozenset(RDD_REQUIRED_DOCUMENTS) <= frozenset(RDD_REVIEW_STAGE.required_documents)

    def test_all_document_types_includes_handed_over(self) -> None:
        """ALL_DOCUMENT_TYPES must include Path B doc type SR_HANDED_OVER_OTHER."""
        assert "SR_HANDED_OVER_OTHER" in ALL_DOCUMENT_TYPES

    def test_all_document_types_is_union(self) -> None:
        from app.agents.schemas.handover_schema import HANDED_OVER_ALLOWED_DOCUMENTS
        expected = (
            frozenset(FM_ALLOWED_DOCUMENTS)
            | frozenset(RDD_ALLOWED_DOCUMENTS)
            | frozenset(HANDED_OVER_ALLOWED_DOCUMENTS)
        )
        assert ALL_DOCUMENT_TYPES == expected

    def test_fm_and_rdd_docs_disjoint(self) -> None:
        assert frozenset(FM_ALLOWED_DOCUMENTS).isdisjoint(frozenset(RDD_REQUIRED_DOCUMENTS))


# ── Permission map ────────────────────────────────────────────────────────────


class TestPermissionMap:
    def test_all_roles_present(self) -> None:
        assert set(PERMISSION_MAP.keys()) == {"MALL_MANAGER", "FM_MANAGER", "DD_ENGINEER", "OPERATIONS"}

    def test_mall_manager_can_act_on_create_sr(self) -> None:
        assert "CREATE_SR" in PERMISSION_MAP["MALL_MANAGER"]

    def test_mall_manager_can_act_on_create_handed_over_sr(self) -> None:
        """Path B: MALL_MANAGER is permitted to CREATE_HANDED_OVER_SR."""
        assert "CREATE_HANDED_OVER_SR" in PERMISSION_MAP["MALL_MANAGER"]

    def test_fm_manager_can_act_on_fm_review(self) -> None:
        assert "FM_REVIEW" in PERMISSION_MAP["FM_MANAGER"]

    def test_dd_engineer_can_act_on_rdd_review(self) -> None:
        assert "RDD_REVIEW" in PERMISSION_MAP["DD_ENGINEER"]

    def test_mall_manager_maps_to_two_stages(self) -> None:
        """MALL_MANAGER has two permitted stages: CREATE_SR and CREATE_HANDED_OVER_SR."""
        assert len(PERMISSION_MAP["MALL_MANAGER"]) == 2

    def test_non_mm_roles_map_to_exactly_one_stage(self) -> None:
        for role, stages in PERMISSION_MAP.items():
            if role == "MALL_MANAGER":
                continue
            assert len(stages) == 1, f"Role '{role}' should map to exactly one stage"

    def test_permission_map_stages_cover_all_workflow_stages(self) -> None:
        covered = {s for stages in PERMISSION_MAP.values() for s in stages}
        assert covered == set(WORKFLOW_STAGES)


# ── Helper utilities ──────────────────────────────────────────────────────────


class TestHelperUtilities:
    def test_get_stage_valid(self) -> None:
        stage = get_stage("CREATE_SR")
        assert stage is CREATE_SR_STAGE

    def test_get_stage_invalid_raises_key_error(self) -> None:
        with pytest.raises(KeyError, match="Unknown workflow stage"):
            get_stage("NONEXISTENT")

    def test_get_required_fields_create_sr(self) -> None:
        fields = get_required_fields("CREATE_SR")
        assert "title" in fields
        assert "lease_code" in fields

    def test_get_required_documents_fm_review(self) -> None:
        docs = get_required_documents("FM_REVIEW")
        assert "SR_HANDOVER_CHECKLIST" in docs

    def test_get_required_documents_create_sr_empty(self) -> None:
        docs = get_required_documents("CREATE_SR")
        assert docs == ()

    def test_get_missing_fields_all_present(self) -> None:
        collected = {f: "value" for f in FM_REVIEW_STAGE.required_fields}
        missing = get_missing_fields("FM_REVIEW", collected)
        assert missing == []

    def test_get_missing_fields_some_absent(self) -> None:
        missing = get_missing_fields("FM_REVIEW", {})
        assert set(missing) == set(FM_REVIEW_STAGE.required_fields)

    def test_get_missing_fields_partial(self) -> None:
        # expected_handover_date is auto-computed from unit_readiness_date; not user-required.
        # comments is optional — an empty string (user declined) satisfies the requirement.
        collected = {"unit_readiness_date": "2025-01-01", "comments": ""}
        missing = get_missing_fields("FM_REVIEW", collected)
        assert missing == []

    def test_get_missing_fields_integer_zero_is_not_missing(self) -> None:
        """0 is a valid value — must not appear in missing fields."""
        collected = {"unit_readiness_date": 0, "comments": ""}
        missing = get_missing_fields("FM_REVIEW", collected)
        assert missing == []

    def test_get_missing_fields_false_is_not_missing(self) -> None:
        """False is a valid value — must not appear in missing fields."""
        collected = {"unit_readiness_date": False, "comments": ""}
        missing = get_missing_fields("FM_REVIEW", collected)
        assert missing == []

    def test_get_missing_fields_empty_string_is_missing(self) -> None:
        collected = {"unit_readiness_date": "", "expected_handover_date": "2025-06-01"}
        missing = get_missing_fields("FM_REVIEW", collected)
        assert "unit_readiness_date" in missing

    def test_get_missing_fields_none_is_missing(self) -> None:
        collected = {"unit_readiness_date": None, "expected_handover_date": "2025-06-01"}
        missing = get_missing_fields("FM_REVIEW", collected)
        assert "unit_readiness_date" in missing

    def test_role_can_act_on_stage_valid(self) -> None:
        assert role_can_act_on_stage("MALL_MANAGER", "CREATE_SR") is True

    def test_role_cannot_act_on_wrong_stage(self) -> None:
        assert role_can_act_on_stage("MALL_MANAGER", "FM_REVIEW") is False

    def test_role_can_act_unknown_role(self) -> None:
        assert role_can_act_on_stage("UNKNOWN_ROLE", "CREATE_SR") is False


# ── LLM extraction schema ─────────────────────────────────────────────────────


class TestHandoverExtractionSchema:
    def test_default_summary_is_none(self) -> None:
        schema = HandoverExtractionSchema()
        assert schema.summary is None

    def test_default_fields_is_empty_dict(self) -> None:
        schema = HandoverExtractionSchema()
        assert schema.fields == {}

    def test_accepts_valid_fields(self) -> None:
        schema = HandoverExtractionSchema(
            summary="User wants to raise a handover SR",
            fields={"title": "Unit 101 Handover", "startDate": "2025-06-01"},
        )
        assert schema.summary == "User wants to raise a handover SR"
        assert schema.fields["title"] == "Unit 101 Handover"


# ── State TypedDict structural check ─────────────────────────────────────────


class TestServiceRequestGraphState:
    """Verify the graph state TypedDict declares all expected keys."""

    _EXPECTED_KEYS = {
        # Core identity
        "session_id", "user_id", "user_message", "attachments", "trace_id",
        # Routing
        "active_agent", "intent", "service_category", "sub_category", "workflow_stage", "status",
        # Conversation context
        "conversation_history",
        # Data collection
        "collected_data", "extracted_fields", "missing_fields", "active_field",
        "correction_pending_field",
        # Lease
        "lease_matches", "selected_lease",
        # Documents
        "documents",
        # Confirmation
        "confirmation_required", "confirmation_status",
        # Submission
        "backend_refs", "validation_errors",
        # Output
        "response_message", "response_ui", "response_decorations",
        # FAQ RAG citations
        "faq_sources",
        # Turn outcome classification (analytics)
        "answer_status",
        # UI-layer overrides (not persisted)
        "action_override", "corrected_fields",
        # Runtime-only services (not serialised)
        "trace_manager", "conversation_state_service", "auth_context",
        # MSP Platform integration (injected by MSP adapter; absent on SR turns)
        "language", "msp_context",
        # Page viewing context (per-turn, never persisted)
        "viewing_notification_id", "viewing_service_request_id", "viewing_notification",
        "notification_list_more", "active_notification_id",
    }

    def test_all_keys_declared(self) -> None:
        from app.agents.graph.state import ServiceRequestGraphState

        annotations = ServiceRequestGraphState.__annotations__
        assert set(annotations.keys()) == self._EXPECTED_KEYS

    def test_backward_compat_alias(self) -> None:
        from app.agents.graph.state import ServiceRequestGraphState, ServiceRequestState

        assert ServiceRequestState is ServiceRequestGraphState
