"""Unit tests for human-readable tracing labels.

The agent-health view is read by people, so no identifier may reach it raw.
These tests pin that every value production actually records has a label, and
that unmapped values still degrade to something readable.
"""

from __future__ import annotations

import pytest

from app.observability.display_labels import (
    node_label,
    prompt_label,
    stage_label,
    tool_label,
)

# Every distinct value present in the live tracing tables.
LIVE_NODES = [
    "api_submission", "confirmation", "faq", "faq_llm_call", "field_extraction",
    "field_extraction_llm_call", "fitout_confirm_api_submission",
    "fitout_confirm_confirmation", "fitout_confirm_entry",
    "fitout_confirm_payload_builder", "fm_api_submission", "fm_payload_builder",
    "fm_review_entry", "handover_entry", "lease_api_call", "lease_lookup",
    "list_notifications", "load_session", "merge_state", "missing_field",
    "notification", "open_notification", "payload_builder", "preview",
    "rdd_api_submission", "rdd_payload_builder", "rdd_review_entry", "registry",
    "response_generation", "response_generation_llm_call", "save_state",
    "service_request_api.create_service_request",
    "service_request_api.fm.patch_approve",
    "service_request_api.fm.patch_save_progress",
    "service_request_api.rdd.submit_report", "sr_status_sync", "supervisor",
    "supervisor_llm_call", "validation",
]
LIVE_TOOLS = [
    "lease_tenant_api", "service_request_api.create_service_request",
    "service_request_api.fm.patch_approve",
    "service_request_api.fm.patch_save_progress",
    "service_request_api.rdd.submit_report",
]
LIVE_PROMPTS = [
    "faq_prompt", "handover_extraction_prompt", "response_generation_prompt",
    "supervisor_prompt",
]
LIVE_STAGES = [
    "APPROVE_SR", "CREATE_HANDED_OVER_SR", "CREATE_SR", "FM_REVIEW",
    "HANDED_OVER_COMPLETED", "RDD_REVIEW", "SR_APPROVED", "SR_COMPLETED",
    "SR_CREATED",
]


def _is_readable(label: str) -> bool:
    """A label a person can read: no snake_case, no dotted identifier."""
    return "_" not in label and "." not in label and label[:1].isupper()


@pytest.mark.parametrize("name", LIVE_NODES)
def test_every_live_node_reads_as_english(name: str) -> None:
    assert _is_readable(node_label(name)), name


@pytest.mark.parametrize("name", LIVE_TOOLS)
def test_every_live_tool_reads_as_english(name: str) -> None:
    assert _is_readable(tool_label(name)), name


@pytest.mark.parametrize("name", LIVE_PROMPTS)
def test_every_live_prompt_reads_as_english(name: str) -> None:
    assert _is_readable(prompt_label(name)), name


@pytest.mark.parametrize("name", LIVE_STAGES)
def test_every_live_stage_reads_as_english(name: str) -> None:
    assert _is_readable(stage_label(name)), name


def test_labels_say_what_the_step_does() -> None:
    assert node_label("faq") == "Answering from the knowledge base"
    assert node_label("supervisor") == "Understanding the request"
    assert tool_label("lease_tenant_api") == "Lease lookup"
    assert prompt_label("handover_extraction_prompt") == (
        "Reading details from the message"
    )
    assert stage_label("CREATE_SR") == "Collecting details"


def test_an_unmapped_identifier_still_reads_as_english() -> None:
    # A step added later must degrade, not leak its identifier.
    assert node_label("some_new_node") == "Some New Node"
    assert prompt_label("brand_new_prompt") == "Brand New Prompt"
    assert stage_label("SOME_NEW_STAGE") == "Some New Stage"


def test_a_dotted_api_name_keeps_only_its_meaningful_tail() -> None:
    assert tool_label("service_request_api.foo.bar_baz") == "Bar Baz"


def test_missing_values_do_not_render_as_empty() -> None:
    assert node_label(None) == "Unknown step"
    assert tool_label(None) == "Unknown service"
    assert prompt_label(None) == "Unnamed prompt"
    assert stage_label(None) == "Not started"
