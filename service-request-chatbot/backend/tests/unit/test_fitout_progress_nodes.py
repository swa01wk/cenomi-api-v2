"""Unit tests for the Fitout Progress nodes (phase 2).

Coverage
--------
TestEntryRoleGuard        — create is offered only to the role the form offers it to
TestEntryPercentage       — a typed or tapped percentage resolves without the LLM
TestEntryConfirmation     — yes / a revised number / anything else
TestEntryCancel           — draft cancel clears; a recorded SR cannot be cancelled
TestEntryUnsupported      — reassign / attach / action-required / save-only
TestEntryStagePin         — CREATE_SR vs APPROVE_SR, and the stale-clear
TestConfirmationCard      — the review card's rows and actions
TestCreatePayload         — the POST body the platform documents
TestApprovePayload        — the PATCH body echoes the stored payload
TestSubmission            — guards, auto-approve wording, failure wording
TestEntryApproveReject    — the approver's decision chips and their text forms
TestRejectionCard         — a rejection is confirmed, and changes nothing
TestApprovalSubmission    — PATCH wording, and the approver guard
TestFirstTurnIsNotACorrection — a correction cue needs something to correct
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.graph.nodes.fitout_progress.fitout_progress_api_submission_node import (
    fitout_progress_api_submission_node,
)
from app.agents.graph.nodes.fitout_progress.fitout_progress_confirmation_node import (
    fitout_progress_confirmation_node,
)
from app.agents.graph.nodes.fitout_progress.fitout_progress_entry_node import (
    fitout_progress_entry_node,
)
from app.agents.graph.nodes.fitout_progress.fitout_progress_payload_builder_node import (
    build_approve_fitout_progress_payload,
    build_create_fitout_progress_payload,
    fitout_progress_payload_builder_node,
)
from app.agents.schemas.fitout_progress_schema import (
    PROGRESS_CONFIRMED_KEY,
    PROGRESS_FIELD,
)

AGENT = "rdd_fitout_progress_agent"

_LEASE: dict[str, Any] = {
    "lease_brand_mall": "t0105712 - Brand Under Armour - Jawharat Jeddah",
    "lease_code": "t0105712",
    "pms_tenant_code": "t0105712",
    "lease_id": 95404,
    "tenant_profile_id": 116,
    "property_id": 3041,
    "company_name": "Under Armour Trading Co.",
    "brand_name": "Brand Under Armour",
    "mall_name": "Jawharat Jeddah",
    "actual_fitout_start_date": "2026-07-15",
    "base_fitout_end_date": "2026-10-15",
}


def _state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "active_agent": AGENT,
        "sub_category": "FITOUT_PROGRESS",
        "workflow_stage": "CREATE_SR",
        "user_message": "",
        "collected_data": dict(_LEASE),
        "auth_context": SimpleNamespace(roles=["DD_ENGINEER"]),
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# TestEntryRoleGuard
# ---------------------------------------------------------------------------


class TestEntryRoleGuard:
    async def test_dd_engineer_may_create(self) -> None:
        out = await fitout_progress_entry_node(_state(workflow_stage=None))
        assert out["workflow_stage"] == "CREATE_SR"

    async def test_admin_may_create(self) -> None:
        out = await fitout_progress_entry_node(
            _state(workflow_stage=None, auth_context=SimpleNamespace(roles=["ADMIN"]))
        )
        assert out["workflow_stage"] == "CREATE_SR"

    async def test_mall_manager_is_refused(self) -> None:
        # The web form hides the sub-category from every role but DD_ENGINEER,
        # so the assistant must not offer what the form refuses.
        out = await fitout_progress_entry_node(
            _state(auth_context=SimpleNamespace(roles=["MALL_MANAGER"]))
        )
        assert out["active_agent"] is None
        assert "isn't available for your role" in out["response_message"]
        assert out["collected_data"] == {}

    async def test_unresolved_role_stays_permissive(self) -> None:
        out = await fitout_progress_entry_node(
            _state(workflow_stage=None, auth_context=SimpleNamespace(roles=[]))
        )
        assert out["workflow_stage"] == "CREATE_SR"

    async def test_approving_an_open_sr_is_not_role_gated_here(self) -> None:
        # Acting on an existing SR is governed by the platform's own operation
        # assignment; the submission node holds the approver guard.
        out = await fitout_progress_entry_node(
            _state(
                workflow_stage="APPROVE_SR",
                backend_refs={"sr_id": "8517"},
                auth_context=SimpleNamespace(roles=["MALL_MANAGER"]),
            )
        )
        assert "response_message" not in out


# ---------------------------------------------------------------------------
# TestEntryPercentage
# ---------------------------------------------------------------------------


class TestEntryPercentage:
    async def test_typed_number_resolves_without_extraction(self) -> None:
        out = await fitout_progress_entry_node(
            _state(user_message="82", active_field=PROGRESS_FIELD)
        )
        assert out["corrected_fields"] == {PROGRESS_FIELD: "82"}
        assert out["collected_data"][PROGRESS_CONFIRMED_KEY] is True
        assert out["status"] == "IN_PROGRESS"

    async def test_chip_tap_takes_the_same_path(self) -> None:
        # Value-only chips arrive as the plain number, indistinguishable from
        # typing it — which is the point.
        out = await fitout_progress_entry_node(
            _state(user_message="100", active_field=PROGRESS_FIELD)
        )
        assert out["corrected_fields"] == {PROGRESS_FIELD: "100"}

    async def test_percent_sign_and_words(self) -> None:
        for message, expected in (("82%", "82"), ("complete", "100"), ("82.5", "82.5")):
            out = await fitout_progress_entry_node(
                _state(user_message=message, active_field=PROGRESS_FIELD)
            )
            assert out["corrected_fields"] == {PROGRESS_FIELD: expected}

    async def test_out_of_range_is_left_to_validation(self) -> None:
        out = await fitout_progress_entry_node(
            _state(user_message="120", active_field=PROGRESS_FIELD)
        )
        assert "corrected_fields" not in out

    async def test_prose_falls_through_to_extraction(self) -> None:
        out = await fitout_progress_entry_node(
            _state(user_message="about eighty-ish", active_field=PROGRESS_FIELD)
        )
        assert "corrected_fields" not in out

    async def test_number_is_ignored_when_another_field_is_open(self) -> None:
        # "8517" while the lease is being asked for is not a percentage.
        out = await fitout_progress_entry_node(
            _state(user_message="8517", active_field="lease_brand_mall")
        )
        assert "corrected_fields" not in out


# ---------------------------------------------------------------------------
# TestEntryConfirmation
# ---------------------------------------------------------------------------


class TestEntryConfirmation:
    async def test_confirm_action(self) -> None:
        out = await fitout_progress_entry_node(
            _state(action_override="confirm_create_fitout_progress")
        )
        assert out == {"confirmation_status": "CONFIRMED", "status": "IN_PROGRESS"}

    async def test_confirm_by_text(self) -> None:
        out = await fitout_progress_entry_node(
            _state(confirmation_status="PENDING", user_message="yes")
        )
        assert out["confirmation_status"] == "CONFIRMED"

    async def test_a_number_on_the_card_is_a_correction(self) -> None:
        out = await fitout_progress_entry_node(
            _state(confirmation_status="PENDING", user_message="make it 90")
        )
        assert out["corrected_fields"] == {PROGRESS_FIELD: "90"}
        assert out["confirmation_status"] == "REJECTED"

    async def test_reject_by_text(self) -> None:
        out = await fitout_progress_entry_node(
            _state(confirmation_status="PENDING", user_message="that's wrong")
        )
        assert out["confirmation_status"] == "REJECTED"

    async def test_ambiguous_reply_asks_again(self) -> None:
        out = await fitout_progress_entry_node(
            _state(confirmation_status="PENDING", user_message="hmm maybe")
        )
        assert "different percentage" in out["response_message"]
        assert "confirmation_status" not in out

    async def test_cancel_action_on_the_card_reopens_the_question(self) -> None:
        out = await fitout_progress_entry_node(
            _state(confirmation_status="PENDING", action_override="cancel")
        )
        assert out["confirmation_status"] == "REJECTED"


# ---------------------------------------------------------------------------
# TestEntryCancel
# ---------------------------------------------------------------------------


class TestEntryCancel:
    async def test_cancel_before_submit_clears_the_draft(self) -> None:
        out = await fitout_progress_entry_node(_state(user_message="cancel"))
        assert out["collected_data"] == {}
        assert out["active_agent"] is None
        assert "Nothing was recorded" in out["response_message"]

    async def test_cancel_after_submit_is_explained_not_attempted(self) -> None:
        # There is no CANCELLED action on this workflow, and the percentage is
        # already on the lease — a PATCH would 400 and the reply would be a lie.
        with patch(
            "app.agents.services.service_request_api_service.get_service_request_api_service"
        ) as svc:
            out = await fitout_progress_entry_node(
                _state(user_message="cancel", backend_refs={"sr_id": "8517"})
            )
            svc.assert_not_called()
        assert "can't be cancelled" in out["response_message"]
        assert "8517" in out["response_message"]
        assert "collected_data" not in out

    async def test_start_over_clears_everything(self) -> None:
        out = await fitout_progress_entry_node(_state(user_message="start over"))
        assert out["collected_data"] == {}
        assert out["response_ui"]["reset_conversation"] is True


# ---------------------------------------------------------------------------
# TestEntryUnsupported
# ---------------------------------------------------------------------------


class TestEntryUnsupported:
    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("can you reassign this to the mall manager", "can't be reassigned"),
            ("let me attach the site photos", "don't carry attachments"),
            ("send it back and ask the tenant for more info", "no action-required step"),
            ("just save it without approving", "same step here"),
        ],
    )
    async def test_refused_locally(self, message: str, expected: str) -> None:
        # Refused without a round-trip: the workflow JSON already says these
        # actions are not in allowed_actions at level 1.
        out = await fitout_progress_entry_node(_state(user_message=message))
        assert expected in out["response_message"]
        assert out["status"] == "WAITING_FOR_USER"

    async def test_a_plain_percentage_is_not_refused(self) -> None:
        out = await fitout_progress_entry_node(
            _state(user_message="82", active_field=PROGRESS_FIELD)
        )
        assert "response_message" not in out


# ---------------------------------------------------------------------------
# TestEntryStagePin
# ---------------------------------------------------------------------------


class TestEntryStagePin:
    # Pinning a stage also clears what the previous flow left behind. A
    # persisted WAITING_FOR_USER short-circuits the turn to
    # response_generation, and a stale active_field makes the correction
    # detector read the request as a request to change something — either one
    # drops the message that started the request.
    _FRESH = {"status": "IN_PROGRESS", "active_field": None}

    async def test_create_on_a_fresh_turn(self) -> None:
        out = await fitout_progress_entry_node(_state(workflow_stage=None))
        assert out == {"workflow_stage": "CREATE_SR", **self._FRESH}

    async def test_approve_when_an_sr_is_open(self) -> None:
        out = await fitout_progress_entry_node(
            _state(workflow_stage=None, backend_refs={"sr_id": "8517"})
        )
        assert out == {"workflow_stage": "APPROVE_SR", **self._FRESH}

    async def test_approve_by_intent(self) -> None:
        out = await fitout_progress_entry_node(
            _state(workflow_stage=None, intent="APPROVE_FITOUT_PROGRESS")
        )
        assert out == {"workflow_stage": "APPROVE_SR", **self._FRESH}

    async def test_the_pin_clears_another_flows_open_question(self) -> None:
        out = await fitout_progress_entry_node(
            _state(
                workflow_stage=None,
                status="WAITING_FOR_USER",
                active_field="actual_fitout_start_date",
            )
        )
        assert out["status"] == "IN_PROGRESS"
        assert out["active_field"] is None

    async def test_stale_terminal_stage_is_cleared_in_the_same_turn(self) -> None:
        out = await fitout_progress_entry_node(
            _state(
                workflow_stage="SR_APPROVED",
                intent="CREATE_FITOUT_PROGRESS",
                backend_refs={"sr_id": "8517"},
            )
        )
        assert out["workflow_stage"] == "CREATE_SR"
        assert out["collected_data"] == {}
        # Released, not just dropped: the save step clears the finished SR and
        # its documents from the session draft.
        assert out["backend_refs"] == {"released_sr_id": "8517"}
        assert out["documents"] == []
        # IN_PROGRESS, not WAITING_FOR_USER: the new request must be processed
        # on this same turn rather than swallowing the message.
        assert out["status"] == "IN_PROGRESS"

    async def test_nothing_to_do_returns_empty(self) -> None:
        out = await fitout_progress_entry_node(_state())
        assert out == {}


# ---------------------------------------------------------------------------
# TestConfirmationCard
# ---------------------------------------------------------------------------


class TestConfirmationCard:
    async def test_card_rows_and_action(self) -> None:
        out = await fitout_progress_confirmation_node(
            _state(
                collected_data={
                    **_LEASE,
                    PROGRESS_FIELD: "82",
                    PROGRESS_CONFIRMED_KEY: True,
                }
            )
        )
        card = out["response_ui"]
        assert out["confirmation_status"] == "PENDING"
        assert card["type"] == "confirmation_card"
        assert card["confirmAction"] == "confirm_create_fitout_progress"
        keys = [row["key"] for row in card["fields"]]
        assert keys == [
            "lease_brand_mall",
            "lease_code",
            "fitout_start_date",
            "fitout_end_date",
            PROGRESS_FIELD,
        ]
        assert "82%" in card["message"]

    async def test_only_the_editable_platform_fields_are_editable(self) -> None:
        # action_rules.UPDATE.editable_fields is [percentage, comment]; every
        # other row would come back "not editable at level 1".
        out = await fitout_progress_confirmation_node(
            _state(
                collected_data={
                    **_LEASE,
                    PROGRESS_FIELD: "82",
                    PROGRESS_CONFIRMED_KEY: True,
                    "comment": "MEP done",
                }
            )
        )
        editable = {r["key"] for r in out["response_ui"]["fields"] if r["editable"]}
        assert editable == {PROGRESS_FIELD, "comment"}

    async def test_recorded_percentage_is_shown_as_context(self) -> None:
        out = await fitout_progress_confirmation_node(
            _state(
                collected_data={
                    **_LEASE,
                    PROGRESS_FIELD: "82",
                    PROGRESS_CONFIRMED_KEY: True,
                    "fitout_progress": "38",
                }
            )
        )
        rows = {r["key"]: r["value"] for r in out["response_ui"]["fields"]}
        assert rows["recorded_fitout_progress"] == "38%"

    async def test_no_card_until_the_percentage_is_answered(self) -> None:
        out = await fitout_progress_confirmation_node(_state(collected_data=dict(_LEASE)))
        assert out == {}

    async def test_no_card_on_the_form_pre_fill_alone(self) -> None:
        # The percentage is in the draft because the form computed it, not
        # because anyone answered — the question is still owed.
        out = await fitout_progress_confirmation_node(
            _state(collected_data={**_LEASE, PROGRESS_FIELD: "100"})
        )
        assert out == {}

    async def test_approve_stage_card_says_approve(self) -> None:
        out = await fitout_progress_confirmation_node(
            _state(
                workflow_stage="APPROVE_SR",
                collected_data={
                    **_LEASE,
                    PROGRESS_FIELD: "82",
                    PROGRESS_CONFIRMED_KEY: True,
                },
            )
        )
        card = out["response_ui"]
        assert card["confirmAction"] == "confirm_approve_fitout_progress"
        assert card["confirmLabel"] == "Approve"

    async def test_already_confirmed_is_left_alone(self) -> None:
        out = await fitout_progress_confirmation_node(
            _state(
                confirmation_status="CONFIRMED",
                collected_data={**_LEASE, PROGRESS_FIELD: "82"},
            )
        )
        assert out == {}


# ---------------------------------------------------------------------------
# TestCreatePayload
# ---------------------------------------------------------------------------


class TestCreatePayload:
    def test_body_matches_the_platform_reference(self) -> None:
        body = build_create_fitout_progress_payload({**_LEASE, PROGRESS_FIELD: "82"})
        assert body["service_category"] == "FIT_OUT_AND_HANDOVER"
        assert body["sub_category"] == "FITOUT_PROGRESS"
        assert body["title"] == "Fitout Progress - t0105712"
        assert body["tenant_profile_id"] == 116
        assert body["lease_id"] == 95404
        assert body["property_id"] == 3041
        assert body["service_request_id"] == ""
        assert body["payload"] == {
            "lease_brand_mall": "t0105712 - Brand Under Armour - Jawharat Jeddah",
            "lease_code": "t0105712",
            "company_name": "Under Armour Trading Co.",
            "brand_name": "Brand Under Armour",
            "mall_name": "Jawharat Jeddah",
            "fitout_start_date": "2026-07-15",
            "fitout_end_date": "2026-10-15",
            PROGRESS_FIELD: "82",
            "comment": "",
            "contract_id": 95404,
        }

    def test_percentage_travels_as_a_string(self) -> None:
        body = build_create_fitout_progress_payload({**_LEASE, PROGRESS_FIELD: 82})
        assert body["payload"][PROGRESS_FIELD] == "82"

    def test_decimal_percentage_survives(self) -> None:
        body = build_create_fitout_progress_payload({**_LEASE, PROGRESS_FIELD: "82.5"})
        assert body["payload"][PROGRESS_FIELD] == "82.5"

    def test_arabic_half_of_the_label_is_dropped(self) -> None:
        body = build_create_fitout_progress_payload(
            {
                **_LEASE,
                "lease_brand_mall": "t0105712 - Brand UA - Jawharat -- شركة أندر آرمور",
                PROGRESS_FIELD: "82",
            }
        )
        assert body["payload"]["lease_brand_mall"] == "t0105712 - Brand UA - Jawharat"

    def test_missing_tenant_profile_id_is_refused_locally(self) -> None:
        # The platform answers a 0 with "should provide a valid tenant profile
        # id"; refusing here turns that into a question about the lease.
        collected = {k: v for k, v in _LEASE.items() if k != "tenant_profile_id"}
        with pytest.raises(ValueError, match="tenant_profile_id"):
            build_create_fitout_progress_payload({**collected, PROGRESS_FIELD: "82"})

    def test_window_falls_back_to_base_dates(self) -> None:
        collected = {
            **{k: v for k, v in _LEASE.items() if k != "actual_fitout_start_date"},
            "base_fitout_start_date": "2026-07-01",
            PROGRESS_FIELD: "82",
        }
        body = build_create_fitout_progress_payload(collected)
        assert body["payload"]["fitout_start_date"] == "2026-07-01"

    def test_volunteered_comment_is_carried(self) -> None:
        body = build_create_fitout_progress_payload(
            {**_LEASE, PROGRESS_FIELD: "82", "comment": "MEP finished last week"}
        )
        assert body["payload"]["comment"] == "MEP finished last week"

    async def test_node_stores_the_payload(self) -> None:
        out = await fitout_progress_payload_builder_node(
            _state(collected_data={**_LEASE, PROGRESS_FIELD: "82"})
        )
        assert out["backend_refs"]["create_payload"]["payload"][PROGRESS_FIELD] == "82"

    async def test_node_fails_gracefully_without_a_lease(self) -> None:
        out = await fitout_progress_payload_builder_node(
            _state(collected_data={PROGRESS_FIELD: "82"})
        )
        assert out["status"] == "FAILED"
        assert "which lease is it" in out["response_message"]


# ---------------------------------------------------------------------------
# TestApprovePayload
# ---------------------------------------------------------------------------


class TestApprovePayload:
    _STORED: dict[str, Any] = {
        "lease_brand_mall": "t0105712 - Brand Under Armour - Jawharat Jeddah",
        "lease_code": "t0105712",
        "company_name": "Under Armour Trading Co.",
        "brand_name": "Brand Under Armour",
        "mall_name": "Jawharat Jeddah",
        "fitout_start_date": "2026-07-15",
        "fitout_end_date": "2026-10-15",
        PROGRESS_FIELD: "65",
        "comment": "",
        "contract_id": 95404,
    }

    def test_stored_payload_is_echoed_with_only_the_editable_keys_changed(self) -> None:
        # ValidateEditableFields compares key by key against the stored payload:
        # anything else differing is "field <key> is not editable at level 1".
        body = build_approve_fitout_progress_payload(
            {**_LEASE, PROGRESS_FIELD: "82"},
            sr_id="8517",
            stored_payload=self._STORED,
        )
        assert body["status"] == "APPROVED"
        assert body["service_request_id"] == "8517"
        changed = {
            k for k, v in body["payload"].items() if self._STORED.get(k) != v
        }
        assert changed == {PROGRESS_FIELD}

    def test_reject_sends_rejected(self) -> None:
        body = build_approve_fitout_progress_payload(
            {**_LEASE, PROGRESS_FIELD: "65"},
            sr_id="8517",
            stored_payload=self._STORED,
            approve=False,
            comment="Numbers look wrong",
        )
        assert body["status"] == "REJECTED"
        assert body["comment"] == "Numbers look wrong"

    def test_without_a_stored_payload_it_rebuilds(self) -> None:
        body = build_approve_fitout_progress_payload(
            {**_LEASE, PROGRESS_FIELD: "82"}, sr_id="8517"
        )
        assert body["payload"]["contract_id"] == 95404
        assert body["payload"][PROGRESS_FIELD] == "82"


# ---------------------------------------------------------------------------
# TestSubmission
# ---------------------------------------------------------------------------


def _api_result(**overrides: Any) -> SimpleNamespace:
    result = {
        "sr_id": "8517",
        "error": None,
        "status_code": 201,
        "correlation_id": "corr-1",
        "response_payload": {"data": {"service_request_id": "8517"}},
    }
    result.update(overrides)
    return SimpleNamespace(**result)


class TestSubmission:
    def _ready_state(self, **overrides: Any) -> dict[str, Any]:
        collected = {**_LEASE, PROGRESS_FIELD: "82"}
        defaults: dict[str, Any] = {
            "collected_data": collected,
            "confirmation_status": "CONFIRMED",
            "validation_errors": [],
            "backend_refs": {
                "create_payload": build_create_fitout_progress_payload(collected)
            },
        }
        defaults.update(overrides)
        return _state(**defaults)

    async def test_unconfirmed_is_blocked(self) -> None:
        out = await fitout_progress_api_submission_node(
            self._ready_state(confirmation_status="PENDING")
        )
        assert out["status"] == "FAILED"
        assert "hasn't been confirmed" in out["response_message"]

    async def test_blocking_validation_error_is_blocked(self) -> None:
        out = await fitout_progress_api_submission_node(
            self._ready_state(
                validation_errors=[{"field": PROGRESS_FIELD, "blocking": True}]
            )
        )
        assert out["status"] == "FAILED"
        assert "validation error" in out["response_message"]

    async def test_missing_payload_is_blocked(self) -> None:
        out = await fitout_progress_api_submission_node(
            self._ready_state(backend_refs={})
        )
        assert out["status"] == "FAILED"

    async def test_dd_create_reports_it_was_recorded_and_approved(self) -> None:
        # The platform auto-approves a create by the approver inside the create
        # transaction and writes the lease there, so promising a review step
        # would promise something that never happens.
        svc = AsyncMock()
        svc.create_service_request.return_value = _api_result()
        with patch(
            "app.agents.graph.nodes.fitout_progress.fitout_progress_api_submission_node"
            ".get_service_request_api_service",
            return_value=svc,
        ):
            out = await fitout_progress_api_submission_node(self._ready_state())
        assert out["workflow_stage"] == "SR_APPROVED"
        assert "8517" in out["response_message"]
        assert "82%" in out["response_message"]
        assert "lease now reads" in out["response_message"]

    async def test_non_approver_create_reports_it_is_pending(self) -> None:
        svc = AsyncMock()
        svc.create_service_request.return_value = _api_result()
        with patch(
            "app.agents.graph.nodes.fitout_progress.fitout_progress_api_submission_node"
            ".get_service_request_api_service",
            return_value=svc,
        ):
            out = await fitout_progress_api_submission_node(
                self._ready_state(auth_context=SimpleNamespace(roles=["MALL_MANAGER"]))
            )
        assert out["workflow_stage"] == "SR_CREATED"
        assert "for approval" in out["response_message"]

    async def test_platform_status_wins_over_the_role_inference(self) -> None:
        svc = AsyncMock()
        svc.create_service_request.return_value = _api_result(
            response_payload={"data": {"service_request_id": "8517", "status": "IN_PROCESS"}}
        )
        with patch(
            "app.agents.graph.nodes.fitout_progress.fitout_progress_api_submission_node"
            ".get_service_request_api_service",
            return_value=svc,
        ):
            out = await fitout_progress_api_submission_node(self._ready_state())
        assert out["workflow_stage"] == "SR_CREATED"

    async def test_api_failure_says_nothing_was_recorded(self) -> None:
        svc = AsyncMock()
        svc.create_service_request.return_value = _api_result(
            sr_id=None, error="502 Bad Gateway", status_code=502
        )
        with patch(
            "app.agents.graph.nodes.fitout_progress.fitout_progress_api_submission_node"
            ".get_service_request_api_service",
            return_value=svc,
        ):
            out = await fitout_progress_api_submission_node(self._ready_state())
        assert out["status"] == "FAILED"
        assert "nothing was" in out["response_message"]
        assert out["backend_refs"]["service_request_status"] == "FAILED"


# ---------------------------------------------------------------------------
# TestEntryApproveReject  (phase 3)
# ---------------------------------------------------------------------------


class TestEntryApproveReject:
    def _approve_state(self, **overrides: Any) -> dict[str, Any]:
        defaults: dict[str, Any] = {
            "workflow_stage": "APPROVE_SR",
            "collected_data": {**_LEASE, PROGRESS_FIELD: "65"},
            "backend_refs": {"sr_id": "8517"},
        }
        defaults.update(overrides)
        return _state(**defaults)

    async def test_approve_chip_answers_the_question_and_decides(self) -> None:
        # "Approve 65%" is offered instead of a card, so the tap has to do both
        # jobs — otherwise validation reports the percentage missing and the
        # submission node refuses the turn.
        out = await fitout_progress_entry_node(
            self._approve_state(action_override="confirm_approve_fitout_progress")
        )
        assert out["confirmation_status"] == "CONFIRMED"
        assert out["collected_data"][PROGRESS_CONFIRMED_KEY] is True

    async def test_reject_chip_arms_a_rejection_and_asks_to_confirm(self) -> None:
        out = await fitout_progress_entry_node(
            self._approve_state(action_override="reject_fitout_progress")
        )
        assert out["backend_refs"]["fitout_progress_action"] == "reject"
        # Not confirmed yet: rejecting is terminal, so it gets the same one-tap
        # confirmation an approval does.
        assert out["confirmation_status"] is None
        assert out["collected_data"][PROGRESS_CONFIRMED_KEY] is True

    async def test_reject_by_text_at_the_approve_stage(self) -> None:
        out = await fitout_progress_entry_node(
            self._approve_state(user_message="reject this request")
        )
        assert out["backend_refs"]["fitout_progress_action"] == "reject"

    async def test_reject_text_on_a_pending_card_is_a_correction_not_a_rejection(
        self,
    ) -> None:
        # While a card is up, "no" means "change it" — the rejection path must
        # not hijack the user's edit.
        out = await fitout_progress_entry_node(
            self._approve_state(confirmation_status="PENDING", user_message="no")
        )
        assert out["confirmation_status"] == "REJECTED"
        assert "backend_refs" not in out

    async def test_reject_text_is_ignored_on_the_create_stage(self) -> None:
        out = await fitout_progress_entry_node(
            _state(user_message="reject", workflow_stage="CREATE_SR")
        )
        assert "backend_refs" not in out

    async def test_confirming_the_rejection(self) -> None:
        out = await fitout_progress_entry_node(
            self._approve_state(
                action_override="confirm_reject_fitout_progress",
                backend_refs={"sr_id": "8517", "fitout_progress_action": "reject"},
            )
        )
        assert out["confirmation_status"] == "CONFIRMED"

    async def test_backing_out_of_a_rejection_disarms_it(self) -> None:
        # Otherwise the next confirm would reject a request the user came back
        # to approve.
        out = await fitout_progress_entry_node(
            self._approve_state(
                action_override="cancel",
                confirmation_status="PENDING",
                backend_refs={"sr_id": "8517", "fitout_progress_action": "reject"},
            )
        )
        assert out["confirmation_status"] == "REJECTED"
        assert "fitout_progress_action" not in out["backend_refs"]


# ---------------------------------------------------------------------------
# TestRejectionCard  (phase 3)
# ---------------------------------------------------------------------------


class TestRejectionCard:
    async def test_rejection_card_is_read_only_and_final(self) -> None:
        out = await fitout_progress_confirmation_node(
            _state(
                workflow_stage="APPROVE_SR",
                collected_data={
                    **_LEASE,
                    PROGRESS_FIELD: "65",
                    PROGRESS_CONFIRMED_KEY: True,
                },
                backend_refs={"sr_id": "8517", "fitout_progress_action": "reject"},
            )
        )
        card = out["response_ui"]
        assert card["confirmAction"] == "confirm_reject_fitout_progress"
        assert card["confirmLabel"] == "Reject"
        assert "Reject SR **8517**?" in card["message"]
        assert all(not row["editable"] for row in card["fields"])
        assert all("inputType" not in row for row in card["fields"])


# ---------------------------------------------------------------------------
# TestApprovalSubmission  (phase 3)
# ---------------------------------------------------------------------------


class TestApprovalSubmission:
    def _patch_state(self, *, approve: bool = True, percentage: str = "82") -> dict[str, Any]:
        stored = {
            "lease_brand_mall": _LEASE["lease_brand_mall"],
            "lease_code": "t0105712",
            "fitout_start_date": "2026-07-15",
            "fitout_end_date": "2026-10-15",
            PROGRESS_FIELD: "65",
            "comment": "",
            "contract_id": 95404,
        }
        collected = {**_LEASE, PROGRESS_FIELD: percentage, PROGRESS_CONFIRMED_KEY: True}
        payload = build_approve_fitout_progress_payload(
            collected,
            sr_id="8517",
            stored_payload=stored,
            approve=approve,
            comment="" if approve else "Rejected via assistant",
        )
        return _state(
            workflow_stage="APPROVE_SR",
            confirmation_status="CONFIRMED",
            validation_errors=[],
            collected_data=collected,
            backend_refs={"sr_id": "8517", "create_payload": payload},
        )

    async def _submit(self, state: dict[str, Any]) -> tuple[dict[str, Any], AsyncMock]:
        svc = AsyncMock()
        svc.patch_service_request.return_value = _api_result(status_code=200)
        with patch(
            "app.agents.graph.nodes.fitout_progress.fitout_progress_api_submission_node"
            ".get_service_request_api_service",
            return_value=svc,
        ):
            out = await fitout_progress_api_submission_node(state)
        return out, svc

    async def test_approved_says_the_lease_was_updated(self) -> None:
        out, svc = await self._submit(self._patch_state())
        svc.patch_service_request.assert_awaited_once()
        assert out["workflow_stage"] == "SR_APPROVED"
        assert "approved at **82%**" in out["response_message"]
        assert "lease now reads 82%" in out["response_message"]

    async def test_rejected_says_the_lease_was_not(self) -> None:
        # The write-back hook is registered for APPROVED only.
        out, _svc = await self._submit(self._patch_state(approve=False))
        assert out["workflow_stage"] == "SR_REJECTED"
        assert "rejected" in out["response_message"].lower()
        assert "not updated" in out["response_message"]

    async def test_non_approver_is_refused_before_any_patch(self) -> None:
        state = self._patch_state()
        state["auth_context"] = SimpleNamespace(roles=["MALL_MANAGER"])
        svc = AsyncMock()
        with patch(
            "app.agents.graph.nodes.fitout_progress.fitout_progress_api_submission_node"
            ".get_service_request_api_service",
            return_value=svc,
        ):
            out = await fitout_progress_api_submission_node(state)
        svc.patch_service_request.assert_not_awaited()
        assert "isn't available for your role" in out["response_message"]
        assert out["status"] == "WAITING_FOR_USER"


# ---------------------------------------------------------------------------
# TestAskingForMoreInfo  (phase 3)
# ---------------------------------------------------------------------------


class TestAskingForMoreInfo:
    async def test_send_it_back_is_explained_not_read_as_a_rejection(self) -> None:
        # "Send it back and ask them" is someone looking for an action-required
        # step, which this workflow has no equivalent of. Turning it into a
        # rejection would close a request they meant to keep open.
        out = await fitout_progress_entry_node(
            _state(
                workflow_stage="APPROVE_SR",
                backend_refs={"sr_id": "8517"},
                user_message="send it back and ask them for detail",
                collected_data={**_LEASE, PROGRESS_FIELD: "65"},
            )
        )
        assert "no action-required step" in out["response_message"]
        assert "backend_refs" not in out


# ---------------------------------------------------------------------------
# TestFirstTurnIsNotACorrection  (client report, 2026-09-29)
# ---------------------------------------------------------------------------


class TestFirstTurnIsNotACorrection:
    """A correction cue needs a conversation to correct."""

    def test_nothing_collected_is_not_a_conversation(self) -> None:
        from app.agents.services.field_correction_service import draft_has_content

        assert draft_has_content({}) is False
        assert draft_has_content(None) is False
        # Session bookkeeping is not something the user said.
        assert draft_has_content({"_progress_confirmed": True}) is False
        assert draft_has_content({"guided_tour": ""}) is False

    def test_a_collected_value_is(self) -> None:
        from app.agents.services.field_correction_service import draft_has_content

        assert draft_has_content({"lease_code": "t0112745"}) is True

    def test_the_parser_still_reads_the_sentence(self) -> None:
        # The precondition lives in field_extraction_node, not here: this
        # function answers "which field does this name?" and nothing else.
        from app.agents.services.field_correction_service import (
            detect_correction_intent,
        )

        assert detect_correction_intent(
            "can you update the mall", {}, active_field="startDate"
        ) == ("mall", False)
