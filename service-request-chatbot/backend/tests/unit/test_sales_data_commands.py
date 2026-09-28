"""Typed commands mean the same thing at every step of the Sales Data flow."""

from __future__ import annotations

import pytest

from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
    _resolve_command,
    sales_data_entry_node,
)

CSV_STAGES = [
    "UPLOAD_INTENT", "CSV_CORRECTION", "AWAITING_TABLE_CONFIRM",
    "MANUAL_ENTRY", "AWAITING_PENDING_MONTHS_CHOICE", "AWAITING_OTHER_MONTHS_REMINDER",
]
DOC_STAGES = ["DOC_SELECT", "AWAITING_DOC_UPLOAD"]
START_STAGES = [None, "SALES_DATA_COMPLETE", "STATUS_LOOKUP", "STALE_NOTIFICATION"]
ALL_STAGES = CSV_STAGES + DOC_STAGES + START_STAGES


def _state(message: str, stage: str | None, intent: str = "UPLOAD_SALES_DATA") -> dict:
    return {
        "user_message": message,
        "workflow_stage": stage,
        "intent": intent,
        "action_override": None,
        "collected_data": {"_pending_items": [{"lease_code": "t0057323", "year_month": "2026-08"}]},
        "backend_refs": {"csv_rows_all": [{"lease_code": "t0088828"}], "csv_filename": "x.csv"},
        "corrected_fields": {},
    }


@pytest.mark.parametrize("stage", CSV_STAGES)
@pytest.mark.parametrize("message", ["reupload", "i want to upload again", "i'll upload a corrected file", "upload a new csv"])
@pytest.mark.asyncio
async def test_reupload_mid_csv_flow_asks_for_the_corrected_csv(stage, message) -> None:
    result = await sales_data_entry_node(_state(message, stage))
    assert result["workflow_stage"] == "UPLOAD_INTENT"
    assert result["response_message"].startswith("Please attach the corrected CSV file")
    assert "csv_rows_all" not in result["backend_refs"]


@pytest.mark.parametrize("stage", START_STAGES)
@pytest.mark.parametrize("message", ["i want to upload again", "upload again", "reupload"])
@pytest.mark.asyncio
async def test_reupload_after_a_finished_or_cancelled_flow_starts_fresh(stage, message) -> None:
    # Reported: after "Sales data submission cancelled", "i want to upload
    # again" opened a supporting-document request for another lease.
    result = await sales_data_entry_node(_state(message, stage, intent="UPLOAD_SUPPORTING_DOCUMENT"))
    assert result["workflow_stage"] == "PENDING_MONTHS_CHECK"
    assert result["intent"] == "UPLOAD_SALES_DATA"


@pytest.mark.parametrize("stage", DOC_STAGES)
@pytest.mark.parametrize("message", ["no, i want to upload sales data", "upload the csv instead"])
@pytest.mark.asyncio
async def test_switching_from_document_to_sales_data_leaves_the_doc_flow(stage, message) -> None:
    # Reported: the doc prompt repeated for "no, i want to upload sales data".
    result = await sales_data_entry_node(_state(message, stage, intent="UPLOAD_SUPPORTING_DOCUMENT"))
    assert result["workflow_stage"] in ("UPLOAD_INTENT",)
    assert result["intent"] == "UPLOAD_SALES_DATA"


@pytest.mark.parametrize("stage", DOC_STAGES)
@pytest.mark.parametrize("message", ["upload again", "i'll upload a different file"])
def test_reupload_inside_a_document_step_means_the_document(stage, message) -> None:
    assert _resolve_command(message, stage) == "doc_upload"


@pytest.mark.parametrize("message", ["upload the audited sales pdf", "reupload supporting document"])
def test_explicit_document_words_always_mean_the_document(message) -> None:
    for stage in ALL_STAGES:
        assert _resolve_command(message, stage) == "doc_upload"


@pytest.mark.parametrize("stage", CSV_STAGES + DOC_STAGES + START_STAGES)
@pytest.mark.parametrize("message", ["cancel", "stop", "never mind", "no, cancel that", "forget it"])
@pytest.mark.asyncio
async def test_typed_cancel_works_everywhere(stage, message) -> None:
    result = await sales_data_entry_node(_state(message, stage))
    assert result["response_message"] == "Sales data submission cancelled. What would you like to do next?"
    assert result["workflow_stage"] is None and result["collected_data"] == {}


@pytest.mark.parametrize("stage", ALL_STAGES)
@pytest.mark.asyncio
async def test_help_is_the_same_everywhere(stage) -> None:
    result = await sales_data_entry_node(_state("help", stage))
    assert result["response_message"].startswith("I can help you with your sales data:")
    assert result["response_ui"]["verbatim"] is True


@pytest.mark.parametrize("stage", ALL_STAGES)
@pytest.mark.asyncio
async def test_start_over_restarts_cleanly(stage) -> None:
    result = await sales_data_entry_node(_state("let's start over", stage))
    assert result["workflow_stage"] == "PENDING_MONTHS_CHECK"
    assert "csv_rows_all" not in result["backend_refs"]


@pytest.mark.parametrize(
    "message, stage",
    [
        ("t0112235 is 5000", "CSV_CORRECTION"),            # the actual correction
        ("t0057323 - aug 2026 - 12280SAR", "AWAITING_PENDING_MONTHS_CHOICE"),
        ("yes", "AWAITING_PERIOD_MISMATCH_CONFIRM"),
        ("different file", "AWAITING_PERIOD_MISMATCH_CONFIRM"),
        ("cancel", "AWAITING_MANUAL_CONFIRM"),              # confirm card owns its cancel
        ("august 2026", "AWAITING_PENDING_MONTHS_CHOICE"),
    ],
)
def test_normal_answers_are_not_treated_as_commands(message, stage) -> None:
    assert _resolve_command(message, stage) is None


def _with_roles(state: dict, *roles: str) -> dict:
    from app.types.chat import AuthContext

    return {**state, "auth_context": AuthContext(subject_id="u1", roles=frozenset(roles))}


@pytest.mark.parametrize("message", ["let's start over", "upload again", "reupload", "upload the audited sales pdf"])
@pytest.mark.parametrize("stage", ["ADMIN_REVIEW", "ADMIN_CONFIRM", None])
@pytest.mark.asyncio
async def test_admin_is_never_switched_into_a_tenant_upload_flow(message, stage) -> None:
    result = await sales_data_entry_node(
        _with_roles(_state(message, stage, intent="REVIEW_SALES_DATA"), "SALES_DATA_TEAM")
    )
    assert result.get("workflow_stage") not in ("PENDING_MONTHS_CHECK", "UPLOAD_INTENT")
    assert result.get("intent") not in ("UPLOAD_SALES_DATA", "UPLOAD_SUPPORTING_DOCUMENT")


@pytest.mark.asyncio
async def test_tenant_commands_still_work_with_real_roles() -> None:
    result = await sales_data_entry_node(
        _with_roles(_state("reupload", "CSV_CORRECTION"), "SHOP_MANAGER", "TENANT")
    )
    assert result["workflow_stage"] == "UPLOAD_INTENT"


@pytest.mark.parametrize("message", ["help", "cancel"])
@pytest.mark.asyncio
async def test_help_and_cancel_work_for_admins_too(message) -> None:
    result = await sales_data_entry_node(
        _with_roles(_state(message, "ADMIN_REVIEW", intent="REVIEW_SALES_DATA"), "SALES_DATA_TEAM")
    )
    assert result["response_ui"]["verbatim"] is True
