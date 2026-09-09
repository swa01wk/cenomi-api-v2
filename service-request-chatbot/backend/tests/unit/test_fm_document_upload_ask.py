"""Unit tests — FM/RDD document upload ask turns."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.handover.confirmation_node import confirmation_node
from app.agents.graph.nodes.handover.fm_review_entry_node import fm_review_entry_node
from app.agents.graph.nodes.handover.missing_field_node import missing_field_node


def _fm_state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": "sess-fm-docs",
        "workflow_stage": "FM_REVIEW",
        "active_agent": "rdd_handover_agent",
        "collected_data": {"unit_readiness_date": "2026-08-20", "comments": "none"},
        "documents": [],
        "validation_errors": [],
        "backend_refs": {
            "sr_id": "11727",
            "user_role": "FM_MANAGER",
        },
        "action_override": None,
        "confirmation_status": None,
        "user_message": "",
    }
    base.update(overrides)
    return base


def _doc_count_error() -> dict[str, Any]:
    return {
        "field": "_documents",
        "validation_type": "document_count",
        "status": "FAILED",
        "blocking": True,
        "message": "At least one FM document must be uploaded before saving or approving.",
    }


@pytest.mark.asyncio
async def test_missing_field_asks_fm_upload_when_docs_blocking() -> None:
    result = await missing_field_node(
        _fm_state(validation_errors=[_doc_count_error()])
    )
    assert result["status"] == "WAITING_FOR_USER"
    assert "upload" in (result.get("response_message") or "").lower()
    deco = result.get("response_decorations") or {}
    docs = (deco.get("document_requirement") or {}).get("documents") or []
    keys = {d["key"] for d in docs}
    assert keys == {
        "SR_HANDOVER_CHECKLIST",
        "SR_HANDOVER_SITE_SURVEY",
        "SR_COP_CHECKLIST_OTHER",  # Fix 2: SR_COP_CHECKLIST_OTHER replaced SR_HANDOVER_OTHER
    }
    assert result.get("response_ui", {}).get("type") != "confirmation_card"


@pytest.mark.asyncio
async def test_missing_field_in_progress_when_fields_done_and_no_doc_error() -> None:
    result = await missing_field_node(_fm_state(validation_errors=[]))
    assert result["status"] == "IN_PROGRESS"
    assert not result.get("response_message")


@pytest.mark.asyncio
async def test_approve_without_docs_is_allowed() -> None:
    """FM documents are optional, so approve must not wait for one.

    This used to return the upload card and drop the action, which left a
    reviewer with nothing to attach unable to approve at all.
    """
    result = await fm_review_entry_node(
        _fm_state(action_override="approve_fm_review")
    )
    assert result["backend_refs"]["fm_action"] == "approve"
    assert result.get("status") != "WAITING_FOR_USER"


@pytest.mark.asyncio
async def test_approve_with_docs_sets_fm_action() -> None:
    result = await fm_review_entry_node(
        _fm_state(
            action_override="approve_fm_review",
            documents=[
                {
                    "document_id": "d1",
                    "document_type_id": "SR_HANDOVER_CHECKLIST",
                }
            ],
        )
    )
    assert result["backend_refs"]["fm_action"] == "approve"
    assert result.get("status") != "WAITING_FOR_USER"


@pytest.mark.asyncio
async def test_confirm_approve_without_docs_is_allowed() -> None:
    result = await fm_review_entry_node(
        _fm_state(action_override="confirm_fm_approve")
    )
    assert result["backend_refs"]["fm_action"] == "approve"
    assert result["confirmation_status"] == "CONFIRMED"


@pytest.mark.asyncio
async def test_confirmation_node_lets_fm_approve_without_docs() -> None:
    """A staged approve reaches the confirmation card with no documents attached."""
    result = await confirmation_node(
        _fm_state(
            backend_refs={
                "sr_id": "11727",
                "user_role": "FM_MANAGER",
                "fm_action": "approve",
            }
        )
    )
    assert result.get("response_ui", {}).get("type") == "confirmation_card"


@pytest.mark.asyncio
async def test_confirmation_node_shows_upload_when_rdd_doc_uploaded_not_fm() -> None:
    """After fields are complete, non-FM docs must NOT satisfy FM upload requirement.

    Fix 2: SR_HANDOVER_OTHER is now RDD-only. An uploaded SR_HANDOVER_OTHER doc must
    not skip the FM upload card — FM still requires Checklist / Site Survey / COP Checklist.
    """
    result = await confirmation_node(
        _fm_state(
            documents=[
                {
                    "document_id": "rdd-other",
                    "document_type_id": "SR_HANDOVER_OTHER",  # RDD doc, not FM
                }
            ],
            backend_refs={
                "sr_id": "11727",
                "user_role": "FM_MANAGER",
            },
        )
    )
    assert result["status"] == "WAITING_FOR_USER"
    assert result.get("response_ui", {}).get("type") != "confirmation_card"
    deco = result.get("response_decorations") or {}
    docs = (deco.get("document_requirement") or {}).get("documents") or []
    keys = {d["key"] for d in docs}
    assert keys == {
        "SR_HANDOVER_CHECKLIST",
        "SR_HANDOVER_SITE_SURVEY",
        "SR_COP_CHECKLIST_OTHER",  # Fix 2
    }
    # None of the FM docs are uploaded — all should be 'missing'
    assert all(d["status"] == "missing" for d in docs)
    assert result.get("confirmation_status") is None


@pytest.mark.asyncio
async def test_confirmation_node_allows_fm_approve_with_docs() -> None:
    result = await confirmation_node(
        _fm_state(
            documents=[
                {
                    "document_id": "d1",
                    "document_type_id": "SR_HANDOVER_CHECKLIST",
                }
            ],
            backend_refs={
                "sr_id": "11727",
                "user_role": "FM_MANAGER",
                "fm_action": "approve",
            },
        )
    )
    assert result.get("confirmation_status") == "PENDING"
    assert result["response_ui"]["type"] == "confirmation_card"
