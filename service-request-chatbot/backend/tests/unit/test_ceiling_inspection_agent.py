"""Unit tests — rdd_ceiling_inspection_agent (Open / Close Ceiling Inspection).

Coverage
--------
TestSubCategory        — the utterance picks Open vs Close; "ceiling inspection" alone does not
TestCreateFields       — required fields, end-on-or-after-start rule, create permission
TestStageMapping       — platform status / operations → chat stage (role-first)
TestEntryNode          — role refusal, "Open or Close?" ask, replay of the first message
TestPayload            — create body matches the platform form; review / ack bodies
TestReviewEntry        — level 1 upload → status → comment → confirm; level 2 draft gate
TestLifecycle          — create → L1 → L2 → REPORT_SUBMITTED → tenant ack, on the mock API
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.ceiling import (
    ceiling_ack_entry_node as ack_mod,
)
from app.agents.graph.nodes.ceiling import (
    ceiling_api_submission_node as create_mod,
)
from app.agents.graph.nodes.ceiling import (
    ceiling_review_submission_node as review_sub_mod,
)
from app.agents.graph.nodes.ceiling.ceiling_ack_entry_node import ceiling_ack_entry_node
from app.agents.graph.nodes.ceiling.ceiling_api_submission_node import (
    created_message,
    ceiling_api_submission_node,
)
from app.agents.graph.nodes.ceiling.ceiling_confirmation_node import (
    build_confirmation_card,
    ceiling_confirmation_node,
)
from app.agents.graph.nodes.ceiling.ceiling_entry_node import ceiling_entry_node
from app.agents.graph.nodes.ceiling.ceiling_payload_builder_node import (
    build_create_ceiling_payload,
)
from app.agents.graph.nodes.ceiling.ceiling_review_entry_node import (
    REVIEW_KEY,
    ceiling_review_entry_node,
)
from app.agents.graph.nodes.ceiling.ceiling_review_submission_node import (
    build_ceiling_review_payload,
    ceiling_review_submission_node,
)
from app.agents.graph.nodes.shared import sr_status_sync_node as sync_mod
from app.agents.graph.nodes.shared.sr_status_sync_node import sr_status_sync_node
from app.agents.schemas import ceiling_schema as cs
from app.agents.services.ceiling_form_state import ceiling_draft_fields
from app.agents.services.service_request_api_service import MockServiceRequestAPIService
from app.services.response_ui_finalize import finalize_response_ui
from app.types.chat import AuthContext

AGENT = cs.AGENT_NAME
OCI = cs.SUB_CATEGORY_OCI
CCI = cs.SUB_CATEGORY_CCI


def _auth(*roles: str) -> AuthContext:
    return AuthContext(subject_id="u-1", roles=frozenset(roles))


def _complete_create_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "lease_code": "t0105712",
        "lease_id": 95404,
        "lease_brand_mall": "T0105712 - Brand X - Mall Y",
        "mall": "Mall Y",
        "brand": "Brand X",
        "brand_id": 77,
        "tenant_profile_id": 501,
        "property_id": 9,
        "contract_id": 95404,
        "unit_codes": ["U-12"],
        "city": "Riyadh",
        "contracted_area": "120",
        "title": "Ceiling services inspection",
        "description": "New shopfront, service corridor B",
        "startDate": "2026-10-02",
        "endDate": "2026-10-03",
        "comments": "Access via service corridor B",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSubCategory:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Raise an OCI for lease t0105712", OCI),
            ("open ceiling inspection please", OCI),
            ("I need a CCI for lease t0105712", CCI),
            ("close ceiling", CCI),
            ("Closed ceiling inspection", CCI),
            ("Raise a ceiling inspection for lease t0105712", None),
            ("open ceiling or close ceiling?", None),
            ("raise a handover", None),
        ],
    )
    def test_detect(self, text: str, expected: str | None) -> None:
        assert cs.detect_sub_category(text) == expected

    def test_map_is_the_only_difference(self) -> None:
        assert cs.draft_report_doc_type(OCI) == "FITOUT_OCI_REPORT_DRAFT"
        assert cs.draft_report_doc_type(CCI) == "FITOUT_CCI_REPORT_DRAFT"
        assert cs.rejected_report_doc_type(OCI) == "SR_REJECTED_OCI_REPORT"
        assert cs.rejected_report_doc_type(CCI) == "SR_REJECTED_CCI_REPORT"


class TestCreateFields:
    def test_missing_in_question_order(self) -> None:
        assert cs.get_missing_fields(cs.STAGE_CREATE, OCI, {}) == [
            "lease_brand_mall",
            "title",
            "description",
            "startDate",
            "endDate",
            "comments",
        ]

    def test_nothing_missing_when_complete(self) -> None:
        assert cs.get_missing_fields(cs.STAGE_CREATE, OCI, _complete_create_data()) == []

    def test_review_stages_collect_nothing_through_the_pipeline(self) -> None:
        assert cs.get_missing_fields(cs.STAGE_L1_REVIEW, OCI, {}) == []

    def test_end_before_start_blocks(self) -> None:
        errors = cs.validate_date_order({"startDate": "2026-10-03", "endDate": "2026-10-02"})
        assert errors and errors[0]["field"] == "endDate" and errors[0]["blocking"]

    def test_same_day_is_allowed(self) -> None:
        assert cs.validate_date_order({"startDate": "2026-10-03", "endDate": "2026-10-03"}) == []

    @pytest.mark.parametrize(
        ("roles", "allowed"),
        [
            # Mall Manager, FM Manager and the RDD Project Manager create.
            (("MALL_MANAGER",), True),
            (("FM_MANAGER",), True),
            (("DD_ENGINEER",), True),
            # Tenants only acknowledge the submitted report.
            (("AUTHORIZED_SIGNATORY",), False),
            (("PRIMARY_TENANT_CONTACT",), False),
            (("COMPANY_ADMIN",), False),
            (("SHOP_MANAGER",), False),
            (("SUPER_ADMIN",), False),
            (("RDD_CONTRACTOR",), False),
            (("ADMIN",), False),
            (("SOME_ROLE", "CAN_RAISE_HANDOVER_SR"), False),  # permission alone is not enough
            ((), True),  # unresolved role stays permissive, like every entry guard
        ],
    )
    def test_can_create(self, roles: tuple[str, ...], allowed: bool) -> None:
        assert cs.can_create(_auth(*roles)) is allowed


class TestStageMapping:
    def test_level_1_is_dd_engineer(self) -> None:
        ops = [
            {"assigned_role": "DD_ENGINEER", "workflow_level": 1, "status": "IN_PROGRESS"},
            {"assigned_role": "FM_MANAGER", "workflow_level": 2, "status": "YET_TO_START"},
        ]
        assert cs.map_operations_to_stage(ops, "IN_PROCESS", None) == cs.STAGE_L1_REVIEW

    def test_level_2_is_fm_manager_by_role_id(self) -> None:
        ops = [
            {"assigned_role": 12, "workflow_level": 1, "status": "FINISHED"},
            {"assigned_role": 20, "workflow_level": 2, "status": "IN_PROGRESS"},
        ]
        assert cs.map_operations_to_stage(ops, "IN_PROCESS", None) == cs.STAGE_L2_REVIEW

    def test_reversed_workflow_row_still_maps_by_role(self) -> None:
        # The stale shared row puts FM_MANAGER on level 1 (PRD §2).
        ops = [{"assigned_role": "FM_MANAGER", "workflow_level": 1, "status": "IN_PROGRESS"}]
        assert cs.map_operations_to_stage(ops, "IN_PROCESS", None) == cs.STAGE_L2_REVIEW

    @pytest.mark.parametrize(
        ("status", "stage"),
        [
            ("REPORT_SUBMITTED", cs.STAGE_REPORT_SUBMITTED),
            ("CLOSED", cs.STAGE_COMPLETED),
            ("CANCELLED", cs.STAGE_CANCELLED),
        ],
    )
    def test_platform_status_wins(self, status: str, stage: str) -> None:
        assert cs.map_operations_to_stage([], status, None) == stage


# ---------------------------------------------------------------------------
# Entry node
# ---------------------------------------------------------------------------


def _entry_state(message: str, roles: tuple[str, ...] = ("MALL_MANAGER",), **kw: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "active_agent": AGENT,
        "intent": cs.INTENT_CREATE,
        "workflow_stage": None,
        "sub_category": None,
        "user_message": message,
        "auth_context": _auth(*roles),
        "backend_refs": {},
        "collected_data": {},
        "documents": [],
        "confirmation_status": None,
        "action_override": None,
    }
    state.update(kw)
    return state


class TestEntryNode:
    @pytest.mark.parametrize("role", ["MALL_MANAGER", "FM_MANAGER", "DD_ENGINEER"])
    async def test_creator_roles_can_raise(self, role: str) -> None:
        result = await ceiling_entry_node(_entry_state("raise an OCI", roles=(role,)))
        assert result["sub_category"] == OCI
        assert result["workflow_stage"] == cs.STAGE_CREATE

    @pytest.mark.parametrize(
        "role", ["AUTHORIZED_SIGNATORY", "PRIMARY_TENANT_CONTACT", "SUPER_ADMIN", "RDD_CONTRACTOR"]
    )
    async def test_other_roles_cannot_raise(self, role: str) -> None:
        result = await ceiling_entry_node(_entry_state("raise an OCI", roles=(role,)))
        assert result["status"] == "WAITING_FOR_USER"
        assert "Only a Mall Manager, FM Manager or RDD Project Manager" in result["response_message"]
        assert result["active_agent"] is None

    async def test_named_sub_category_resolves_and_seeds_inspector(self) -> None:
        result = await ceiling_entry_node(_entry_state("Raise an OCI for lease t0105712"))
        assert result["sub_category"] == OCI
        assert result["workflow_stage"] == cs.STAGE_CREATE
        assert result["collected_data"]["inspection_done_by"] == 12
        assert result["status"] == "IN_PROGRESS"

    async def test_plain_ceiling_inspection_asks_once(self) -> None:
        result = await ceiling_entry_node(
            _entry_state("Raise a ceiling inspection for lease t0105712")
        )
        assert result["status"] == "WAITING_FOR_USER"
        assert "Open Ceiling Inspection" in result["response_message"]
        actions = {
            o["action"] for o in result["response_decorations"]["suggestions"]["options"]
        }
        assert actions == {"select_ceiling_oci", "select_ceiling_cci"}

    async def test_supervisor_guess_is_ignored_on_first_turn(self) -> None:
        result = await ceiling_entry_node(
            _entry_state("Raise a ceiling inspection", sub_category=OCI)
        )
        assert result["sub_category"] == cs.SUB_CATEGORY_UNRESOLVED

    async def test_answer_replays_the_first_message(self) -> None:
        asked = await ceiling_entry_node(
            _entry_state("Raise a ceiling inspection for lease t0105712")
        )
        answered = await ceiling_entry_node(
            _entry_state(
                "Close Ceiling Inspection",
                workflow_stage=asked["workflow_stage"],
                sub_category=asked["sub_category"],
                collected_data=asked["collected_data"],
                action_override="select_ceiling_cci",
            )
        )
        assert answered["sub_category"] == CCI
        assert "t0105712" in answered["user_message"]
        assert "_ceiling_pending_message" not in answered["collected_data"]

    async def test_confirm_action(self) -> None:
        result = await ceiling_entry_node(
            _entry_state(
                "",
                workflow_stage=cs.STAGE_CREATE,
                sub_category=OCI,
                confirmation_status="PENDING",
                action_override="confirm_create_ceiling_inspection",
            )
        )
        assert result["confirmation_status"] == "CONFIRMED"


# ---------------------------------------------------------------------------
# Payloads and cards
# ---------------------------------------------------------------------------


class TestPayload:
    def test_create_body_matches_the_form(self) -> None:
        body = build_create_ceiling_payload(_complete_create_data(), CCI)
        assert body["service_category"] == "FIT_OUT_AND_HANDOVER"
        assert body["sub_category"] == CCI
        assert body["status"] == "IN_PROCESS"
        inner = body["payload"]
        assert inner["inspection_done_by"] == 12 and inner["inspectionDoneBy"] == 12
        assert inner["startDate"].endswith("Z") and inner["startDateLT"].startswith("02/10/2026")
        for handover_only in (
            "already_handovered",
            "unit_readiness_date",
            "expected_handover_date",
            "guideLineLink",
        ):
            assert handover_only not in inner

    def test_other_documents_ride_on_the_create(self) -> None:
        docs = [{"document_id": "d-1", "document_type_id": "FITOUT_INSPECTION_OTHER"}]
        inner = build_create_ceiling_payload(_complete_create_data(), OCI, docs)["payload"]
        assert inner["documents_ids"] == ["d-1"]
        assert inner["documents"][0]["document_type_id"] == "FITOUT_INSPECTION_OTHER"

    def test_unresolved_sub_category_refused(self) -> None:
        with pytest.raises(ValueError):
            build_create_ceiling_payload(_complete_create_data(), cs.SUB_CATEGORY_UNRESOLVED)

    def test_card_shows_inspector_read_only(self) -> None:
        card = build_confirmation_card(_complete_create_data(), OCI)
        rows = {row["key"]: row for row in card["fields"]}
        assert rows["sub_category"]["value"] == "Open Ceiling Inspection"
        assert rows["inspection_done_by"]["value"] == 12
        assert rows["inspection_done_by"]["editable"] is False
        assert card["confirmAction"] == "confirm_create_ceiling_inspection"

    async def test_card_waits_for_required_fields(self) -> None:
        state = _entry_state("", workflow_stage=cs.STAGE_CREATE, sub_category=OCI)
        state["collected_data"] = _complete_create_data(comments="")
        assert await ceiling_confirmation_node(state) == {}
        state["collected_data"] = _complete_create_data()
        assert (await ceiling_confirmation_node(state))["confirmation_status"] == "PENDING"

    def test_review_body_statuses(self) -> None:
        refs = {"sr_id": "SR-1", "create_payload": {"payload": {"title": "t"}, "lease_id": 1}}
        docs = [
            {"document_id": "draft", "document_type_id": "FITOUT_OCI_REPORT_DRAFT"},
            {"document_id": "rej", "document_type_id": "SR_REJECTED_OCI_REPORT"},
            {"document_id": "oth", "document_type_id": "FITOUT_INSPECTION_OTHER"},
        ]
        body = build_ceiling_review_payload(
            backend_refs=refs,
            documents=docs,
            sub_category=OCI,
            review={"action": "approve", "document_status": "APPROVED_WITH_CHANGES", "comment": "c"},
        )
        statuses = {e["id"]: e["document_status"] for e in body["payload"]["document_status_map"]}
        assert statuses == {"draft": "APPROVED_WITH_CHANGES", "rej": "DISAPPROVED", "oth": ""}
        assert body["status"] == "APPROVED" and body["comment"] == "c"

    def test_more_info_is_action_required(self) -> None:
        body = build_ceiling_review_payload(
            backend_refs={"sr_id": "SR-1"},
            documents=[],
            sub_category=CCI,
            review={"action": "more_info", "comment": "which zone?"},
        )
        assert body["status"] == "ACTION_REQUIRED"


# ---------------------------------------------------------------------------
# Review entry
# ---------------------------------------------------------------------------

_DRAFT_OCI = {"document_id": "draft-1", "document_type_id": "FITOUT_OCI_REPORT_DRAFT"}


def _review_state(stage: str, roles: tuple[str, ...], **kw: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "active_agent": AGENT,
        "workflow_stage": stage,
        "sub_category": OCI,
        "user_message": "",
        "user_id": "u-9",
        "session_id": "s-1",
        "auth_context": _auth(*roles),
        "backend_refs": {"sr_id": "SR-104233"},
        "collected_data": {},
        "documents": [],
        "confirmation_status": None,
        "action_override": None,
    }
    state.update(kw)
    return state


def _chips(result: dict[str, Any]) -> set[str]:
    options = ((result.get("response_decorations") or {}).get("suggestions") or {}).get("options") or []
    return {o["action"] for o in options}


class TestReviewEntry:
    async def test_other_roles_are_told_who_it_is_with(self) -> None:
        result = await ceiling_review_entry_node(_review_state(cs.STAGE_L1_REVIEW, ("MALL_MANAGER",)))
        assert "RDD Project Manager" in result["response_message"]
        assert "confirmation_status" not in result

    async def test_level_1_asks_for_the_report_first(self) -> None:
        result = await ceiling_review_entry_node(_review_state(cs.STAGE_L1_REVIEW, ("DD_ENGINEER",)))
        assert "Upload the Open Ceiling Inspection report" in result["response_message"]
        requirement = result["response_decorations"]["document_requirement"]
        docs = {row["key"]: row for row in requirement["documents"]}
        assert list(docs) == ["FITOUT_OCI_REPORT_DRAFT", "FITOUT_INSPECTION_OTHER"]
        assert docs["FITOUT_OCI_REPORT_DRAFT"]["label"] == "Open Ceiling Inspection Report"
        assert docs["FITOUT_OCI_REPORT_DRAFT"]["required"] is True
        assert docs["FITOUT_INSPECTION_OTHER"]["label"] == "Inspection Other"
        assert requirement["uploadContext"]["allowedDocumentTypes"] == [
            "FITOUT_OCI_REPORT_DRAFT",
            "FITOUT_INSPECTION_OTHER",
        ]
        assert "ceiling_more_info" in _chips(result)

    async def test_level_1_walkthrough(self) -> None:
        state = _review_state(cs.STAGE_L1_REVIEW, ("DD_ENGINEER",), documents=[_DRAFT_OCI])
        asked_status = await ceiling_review_entry_node(state)
        assert "ceiling_status_approved_with_changes" in _chips(asked_status)

        state["collected_data"] = asked_status["collected_data"]
        state["action_override"] = "ceiling_status_approved_with_changes"
        asked_comment = await ceiling_review_entry_node(state)
        assert "comment" in asked_comment["response_message"].lower()

        state["collected_data"] = asked_comment["collected_data"]
        state["action_override"] = None
        state["user_message"] = "Sprinkler spacing in zone 2"
        card = await ceiling_review_entry_node(state)
        assert card["response_ui"]["type"] == "confirmation_card"
        assert card["confirmation_status"] == "PENDING"
        fields = {row["key"]: row["value"] for row in card["response_ui"]["fields"]}
        assert fields["document"] == "Open Ceiling Inspection Report"

        state["collected_data"] = card["collected_data"]
        state["action_override"] = "confirm_ceiling_review"
        confirmed = await ceiling_review_entry_node(state)
        assert confirmed["confirmation_status"] == "CONFIRMED"
        review = confirmed["collected_data"][REVIEW_KEY]
        assert review["document_status"] == "APPROVED_WITH_CHANGES"
        assert review["comment"] == "Sprinkler spacing in zone 2"

    async def test_level_2_cannot_approve_without_the_report(self) -> None:
        result = await ceiling_review_entry_node(_review_state(cs.STAGE_L2_REVIEW, ("FM_MANAGER",)))
        assert "cannot be approved yet" in result["response_message"]
        assert result["response_ui"]["type"] == "message"
        assert _chips(result) == {"ceiling_more_info"}

    async def test_level_2_approves_once_the_report_is_on_file(self) -> None:
        result = await ceiling_review_entry_node(
            _review_state(cs.STAGE_L2_REVIEW, ("FM_MANAGER",), documents=[_DRAFT_OCI])
        )
        assert "report is on file" in result["response_message"]
        assert result["response_ui"]["type"] == "confirmation_card"

    async def test_ask_for_more_information(self) -> None:
        state = _review_state(cs.STAGE_L2_REVIEW, ("FM_MANAGER",), action_override="ceiling_more_info")
        asked = await ceiling_review_entry_node(state)
        assert "What information" in asked["response_message"]
        state.update(collected_data=asked["collected_data"], action_override=None, user_message="Which zone?")
        card = await ceiling_review_entry_node(state)
        actions = {r["key"]: r["value"] for r in card["response_ui"]["fields"]}
        assert actions["review_action"] == "Ask for more information"

    async def test_creator_cannot_ask_themselves_for_more_info(self) -> None:
        state = _review_state(
            cs.STAGE_L1_REVIEW,
            ("DD_ENGINEER",),
            backend_refs={"sr_id": "SR-1", "create_payload": {"payload": {"created_by": "u-9"}}},
        )
        result = await ceiling_review_entry_node(state)
        assert "ceiling_more_info" not in _chips(result)

    async def test_uploaded_report_keeps_the_card_with_the_status_chips(self) -> None:
        # The turn after an upload is a silent form sync; the client shows a
        # silent reply only when it is a card, so the chips ride on the card.
        result = await ceiling_review_entry_node(
            _review_state(cs.STAGE_L1_REVIEW, ("DD_ENGINEER",), documents=[_DRAFT_OCI])
        )
        rows = {r["key"]: r for r in result["response_decorations"]["document_requirement"]["documents"]}
        assert rows["FITOUT_OCI_REPORT_DRAFT"]["status"] == "uploaded"
        assert _chips(result) == {
            "ceiling_status_approved",
            "ceiling_status_approved_with_changes",
            "ceiling_more_info",
        }
        ui = finalize_response_ui(
            result["response_ui"],
            result["response_decorations"],
            draft_preview=None,
            response_message=result["response_message"],
        )
        assert ui["type"] == "document_requirement"
        assert "approved, or approved with changes" in ui["message"]
        assert {o["action"] for o in ui["suggestions"]["options"]} >= {
            "ceiling_status_approved",
            "ceiling_status_approved_with_changes",
        }

    @pytest.mark.parametrize(
        ("stage", "roles"),
        [
            (cs.STAGE_L1_REVIEW, ("MALL_MANAGER",)),
            (cs.STAGE_L1_REVIEW, ("FM_MANAGER",)),
            (cs.STAGE_L1_REVIEW, ("PRIMARY_TENANT_CONTACT",)),
            (cs.STAGE_L1_REVIEW, ()),
            (cs.STAGE_L2_REVIEW, ("MALL_MANAGER",)),
            (cs.STAGE_L2_REVIEW, ("DD_ENGINEER",)),
            (cs.STAGE_L2_REVIEW, ()),
        ],
    )
    async def test_only_the_level_role_gets_approve(self, stage: str, roles: tuple[str, ...]) -> None:
        result = await ceiling_review_entry_node(
            _review_state(stage, roles, documents=[_DRAFT_OCI])
        )
        assert "nothing for you to action" in result["response_message"]
        decorations = result["response_decorations"]
        assert "document_requirement" not in decorations
        assert not _chips(result) & {
            "ceiling_status_approved",
            "ceiling_status_approved_with_changes",
            "ceiling_approve",
            "ceiling_more_info",
        }
        assert "confirmation_status" not in result

    async def test_admin_override_reviews_level_1(self) -> None:
        result = await ceiling_review_entry_node(
            _review_state(cs.STAGE_L1_REVIEW, ("ADMIN",), documents=[_DRAFT_OCI])
        )
        assert "ceiling_status_approved" in _chips(result)


# ---------------------------------------------------------------------------
# Lifecycle on the in-memory platform mock
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_api(monkeypatch: pytest.MonkeyPatch) -> MockServiceRequestAPIService:
    api = MockServiceRequestAPIService(isolated=True)
    for module in (create_mod, review_sub_mod, ack_mod, sync_mod):
        monkeypatch.setattr(module, "get_service_request_api_service", lambda: api)
    return api


async def _open(sr_id: str, roles: tuple[str, ...], documents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """What a reviewer's session looks like after status sync opens the SR."""
    state: dict[str, Any] = {
        "backend_refs": {"sr_id": sr_id},
        "workflow_stage": None,
        "collected_data": {},
        "documents": documents or [],
        "auth_context": _auth(*roles),
        "user_id": "reviewer",
        "session_id": "s-2",
        "user_message": "",
        "action_override": None,
        "confirmation_status": None,
    }
    state.update(await sr_status_sync_node(state))
    return state


class TestLifecycle:
    async def test_create_to_tenant_acknowledgement(self, mock_api: MockServiceRequestAPIService) -> None:
        # ── create ──
        create_state: dict[str, Any] = {
            "confirmation_status": "CONFIRMED",
            "validation_errors": [],
            "sub_category": OCI,
            "backend_refs": {
                "create_payload": build_create_ceiling_payload(_complete_create_data(), OCI)
            },
        }
        created = await ceiling_api_submission_node(create_state)
        sr_id = created["backend_refs"]["sr_id"]
        assert created["workflow_stage"] == cs.STAGE_SR_CREATED
        assert "RDD Project Manager" in created["response_message"]
        assert mock_api.created_requests[0]["payload"]["status"] == "IN_PROCESS"

        # ── level 1: RDD Project Manager uploads, approves ──
        l1 = await _open(sr_id, ("DD_ENGINEER",), documents=[_DRAFT_OCI])
        assert l1["workflow_stage"] == cs.STAGE_L1_REVIEW
        assert l1["active_agent"] == AGENT and l1["sub_category"] == OCI
        l1["collected_data"][REVIEW_KEY] = {
            "stage": cs.STAGE_L1_REVIEW,
            "action": "approve",
            "document_status": "APPROVED",
        }
        l1["confirmation_status"] = "CONFIRMED"
        sent = await ceiling_review_submission_node(l1)
        assert "FM Manager" in sent["response_message"]

        # ── level 2: FM Manager sees the report on the SR, approves ──
        l2 = await _open(sr_id, ("FM_MANAGER",))
        assert l2["workflow_stage"] == cs.STAGE_L2_REVIEW
        assert cs.has_draft_report(l2["documents"], OCI)
        l2["collected_data"][REVIEW_KEY] = {"stage": cs.STAGE_L2_REVIEW, "action": "approve"}
        l2["confirmation_status"] = "CONFIRMED"
        submitted = await ceiling_review_submission_node(l2)
        assert "tenant can now acknowledge" in submitted["response_message"]

        # ── tenant acknowledges ──
        tenant = await _open(sr_id, ("AUTHORIZED_SIGNATORY",))
        assert tenant["workflow_stage"] == cs.STAGE_REPORT_SUBMITTED
        prompt = await ceiling_ack_entry_node(tenant)
        assert prompt["confirmation_status"] == "PENDING"
        tenant["action_override"] = "acknowledge_ceiling_report"
        done = await ceiling_ack_entry_node(tenant)
        assert done["workflow_stage"] == cs.STAGE_COMPLETED
        assert "is complete" in done["response_message"]

        final = await _open(sr_id, ("AUTHORIZED_SIGNATORY",))
        assert final["workflow_stage"] == cs.STAGE_COMPLETED

    async def test_fm_manager_waits_on_the_tenant(self, mock_api: MockServiceRequestAPIService) -> None:
        body = build_create_ceiling_payload(_complete_create_data(), CCI)
        created = await mock_api.create_service_request(body)
        await mock_api.patch_service_request(created.sr_id, {"status": "APPROVED"})
        await mock_api.patch_service_request(created.sr_id, {"status": "APPROVED"})
        fm = await _open(created.sr_id, ("FM_MANAGER",))
        result = await ceiling_ack_entry_node(fm)
        assert "waiting for the tenant" in result["response_message"]

    async def test_admin_cannot_acknowledge(self, mock_api: MockServiceRequestAPIService) -> None:
        body = build_create_ceiling_payload(_complete_create_data(), CCI)
        created = await mock_api.create_service_request(body)
        await mock_api.patch_service_request(created.sr_id, {"status": "APPROVED"})
        await mock_api.patch_service_request(created.sr_id, {"status": "APPROVED"})
        admin = await _open(created.sr_id, ("ADMIN",))
        admin["action_override"] = "acknowledge_ceiling_report"
        result = await ceiling_ack_entry_node(admin)
        assert "waiting for the tenant" in result["response_message"]
        assert result.get("workflow_stage") != cs.STAGE_COMPLETED


class TestOutOfCredits:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Raise an OCI for lease t0105712", True),
            ("close ceiling inspection", True),
            ("raise a handover for t0105712", False),
            ("upload sales data", False),
        ],
    )
    def test_mentions_ceiling_inspection(self, text: str, expected: bool) -> None:
        assert cs.mentions_ceiling_inspection(text) is expected

    def test_only_quota_errors_count(self) -> None:
        assert cs.is_out_of_credits_error(
            "Error code: 429 - {'error': {'type': 'insufficient_quota'}}"
        )
        assert not cs.is_out_of_credits_error("connection timeout")
        assert not cs.is_out_of_credits_error(None)

    async def test_supervisor_says_out_of_credits_for_a_ceiling_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.agents.graph.nodes import supervisor_node as sup

        class _BrokeGateway:
            model = "fake"
            provider_name = "fake"

            async def complete_json(self, **_: Any) -> Any:
                raise RuntimeError(
                    "Error code: 429 - {'error': {'code': 'credit_balance_exhausted', "
                    "'type': 'insufficient_quota'}}"
                )

        monkeypatch.setattr(sup, "get_default_gateway", lambda: _BrokeGateway())
        state = {
            "user_message": "Raise an OCI for lease t0105712",
            "auth_context": _auth("AUTHORIZED_SIGNATORY"),
            "backend_refs": {},
        }
        result = await sup.supervisor_node(state)
        assert result["response_message"] == cs.OUT_OF_CREDITS_MESSAGE
        assert result["response_ui"]["verbatim"] is True

        state["user_message"] = "upload sales data"
        other = await sup.supervisor_node(state)
        assert other.get("response_message") != cs.OUT_OF_CREDITS_MESSAGE


# ---------------------------------------------------------------------------
# Creators: Mall Manager, FM Manager, RDD PM — RDD PM brings the report
# ---------------------------------------------------------------------------


def _create_ready_state(roles: tuple[str, ...], **kw: Any) -> dict[str, Any]:
    state = _entry_state("", roles=roles, workflow_stage=cs.STAGE_CREATE, sub_category=OCI)
    state["collected_data"] = _complete_create_data()
    state.update(kw)
    return state


class TestRddPmReportAtCreate:
    @pytest.mark.parametrize("role", ["MALL_MANAGER", "FM_MANAGER"])
    async def test_other_creators_go_straight_to_the_card(self, role: str) -> None:
        result = await ceiling_confirmation_node(_create_ready_state((role,)))
        assert result["confirmation_status"] == "PENDING"
        assert cs.DRAFT_REPORT_STATUS_KEY not in result.get("collected_data", {})

    async def test_rddpm_is_asked_for_the_report_first(self) -> None:
        result = await ceiling_confirmation_node(_create_ready_state(("DD_ENGINEER",)))
        assert result["status"] == "WAITING_FOR_USER"
        assert result["confirmation_status"] is None
        assert "upload the Open Ceiling Inspection report" in result["response_message"]
        requirement = result["response_decorations"]["document_requirement"]
        docs = {row["key"]: row for row in requirement["documents"]}
        assert list(docs) == ["FITOUT_OCI_REPORT_DRAFT", "FITOUT_INSPECTION_OTHER"]
        assert docs["FITOUT_OCI_REPORT_DRAFT"]["label"] == "Open Ceiling Inspection Report"
        assert docs["FITOUT_OCI_REPORT_DRAFT"]["required"] is True
        assert docs["FITOUT_INSPECTION_OTHER"]["label"] == "Inspection Other"
        # Create: no SR yet, the upload rides on the chat session.
        assert requirement["uploadContext"]["sr_id"] == ""

    async def test_rddpm_is_then_asked_for_the_status(self) -> None:
        result = await ceiling_confirmation_node(
            _create_ready_state(("DD_ENGINEER",), documents=[_DRAFT_OCI])
        )
        assert result["status"] == "WAITING_FOR_USER"
        assert "approved, or approved with changes" in result["response_message"]
        actions = [
            o["action"] for o in result["response_decorations"]["suggestions"]["options"]
        ]
        assert actions == ["ceiling_status_approved", "ceiling_status_approved_with_changes"]
        # The card stays on screen with the status chips (silent post-upload turn).
        rows = {
            r["key"]: r for r in result["response_decorations"]["document_requirement"]["documents"]
        }
        assert rows["FITOUT_OCI_REPORT_DRAFT"]["status"] == "uploaded"

    @pytest.mark.parametrize(
        ("action", "message", "expected"),
        [
            ("ceiling_status_approved_with_changes", "", "APPROVED_WITH_CHANGES"),
            ("ceiling_status_approved", "", "APPROVED"),
            (None, "It is approved with changes", "APPROVED_WITH_CHANGES"),
        ],
    )
    async def test_status_answer_leads_to_the_card(
        self, action: str | None, message: str, expected: str
    ) -> None:
        state = _create_ready_state(
            ("DD_ENGINEER",), documents=[_DRAFT_OCI], action_override=action, user_message=message
        )
        result = await ceiling_confirmation_node(state)
        assert result["confirmation_status"] == "PENDING"
        assert result["collected_data"][cs.DRAFT_REPORT_STATUS_KEY] == expected
        rows = {row["key"]: row for row in result["response_ui"]["fields"]}
        assert rows[cs.DRAFT_REPORT_STATUS_KEY]["label"] == "Open Ceiling Inspection Report"

    def test_payload_carries_the_report_status(self) -> None:
        data = _complete_create_data(**{cs.DRAFT_REPORT_STATUS_KEY: "APPROVED_WITH_CHANGES"})
        other = {"document_id": "o-1", "document_type_id": "FITOUT_INSPECTION_OTHER"}
        inner = build_create_ceiling_payload(data, OCI, [_DRAFT_OCI, other])["payload"]
        assert inner["documents_ids"] == ["draft-1", "o-1"]
        assert inner["document_status_map"] == [
            {"id": "draft-1", "document_status": "APPROVED_WITH_CHANGES"},
            {"id": "o-1", "document_status": ""},
        ]

    def test_payload_without_a_report_sends_no_statuses(self) -> None:
        inner = build_create_ceiling_payload(_complete_create_data(), OCI, [_DRAFT_OCI])["payload"]
        assert inner["document_status_map"] == []


class TestCreatedMessage:
    def _state(self, roles: tuple[str, ...], **collected: Any) -> dict[str, Any]:
        return {"auth_context": _auth(*roles), "collected_data": collected}

    def test_rddpm_level_auto_approved(self) -> None:
        msg = created_message("SR-1", "Open Ceiling Inspection", cs.STAGE_L2_REVIEW, self._state(("DD_ENGINEER",)))
        assert "approved automatically" in msg and "FM Manager" in msg

    def test_rddpm_fallback_when_the_sr_cannot_be_read(self) -> None:
        state = self._state(("DD_ENGINEER",), **{cs.DRAFT_REPORT_STATUS_KEY: "APPROVED"})
        assert "approved automatically" in created_message("SR-1", "Open Ceiling Inspection", None, state)

    def test_rddpm_level_still_open(self) -> None:
        msg = created_message("SR-1", "Open Ceiling Inspection", cs.STAGE_L1_REVIEW, self._state(("DD_ENGINEER",)))
        assert "Level 1 is still open" in msg

    def test_fm_creator(self) -> None:
        msg = created_message("SR-1", "Close Ceiling Inspection", cs.STAGE_L1_REVIEW, self._state(("FM_MANAGER",)))
        assert "RDD Project Manager" in msg and "approved automatically once they approve" in msg

    def test_mall_manager_creator(self) -> None:
        msg = created_message("SR-1", "Close Ceiling Inspection", cs.STAGE_L1_REVIEW, self._state(("MALL_MANAGER",)))
        assert msg.endswith("It is with the RDD Project Manager for review.")


class TestPlatformDraftFields:
    def _rows(self, sub_category: str | None) -> dict[str, Any]:
        state = {"collected_data": _complete_create_data(), "sub_category": sub_category}
        return {row["key"]: row["value"] for row in ceiling_draft_fields(state)}

    def test_form_field_names_and_date_pairs(self) -> None:
        rows = self._rows(OCI)
        for key in ("title", "description", "comments", "startDate", "endDate", "startDateLT", "endDateLT"):
            assert key in rows
        assert rows["lease_brand_mall"] == "T0105712 - Brand X - Mall Y"
        assert rows["lease_id"] == 95404
        # TenantSelect holds the tenant_profile_id.
        assert rows["company_name"] == "501"

    def test_sub_category_only_once_resolved(self) -> None:
        assert self._rows(CCI)["sub_category"] == CCI
        assert "sub_category" not in self._rows(cs.SUB_CATEGORY_UNRESOLVED)
        assert "sub_category" not in self._rows(None)
