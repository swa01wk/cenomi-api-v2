"""Unit tests — an SR released for a new request is not restored next turn.

Seen on SR 321027: "create handover" from its details page released the SR and
started a new request, but the very next message — the lease answer — came back
with that SR's preview ("here's the service request … status Action Required").

The session draft restores its sr_id on every turn and save only ever wrote an
sr_id, never cleared one. So the release lasted one turn: the draft still held
321027, load put it back, and the turn synced and previewed the old SR.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.agents.services.conversation_state_service import ConversationStateService


class _FakeDraftRepo:
    """Just enough of ServiceRequestDraftRepository for save/load."""

    def __init__(self, draft: SimpleNamespace | None) -> None:
        self.draft = draft
        self.updates: list[dict[str, Any]] = []

    async def get_by_session(self, session_id: Any) -> SimpleNamespace | None:
        return self.draft

    async def update(self, draft_id: Any, updates: dict[str, Any]) -> SimpleNamespace:
        self.updates.append(dict(updates))
        for key, value in updates.items():
            setattr(self.draft, key, value)
        return self.draft

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.draft = SimpleNamespace(id=uuid4(), service_request_status=None, **kwargs)
        return self.draft


def _draft_on(sr_id: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        service_category="FIT_OUT_AND_HANDOVER",
        sub_category="HANDOVER",
        workflow_stage="FM_REVIEW",
        collected_data={"lease_code": "t0108592"},
        missing_fields=[],
        documents=[],
        ready_to_submit=False,
        sr_id=sr_id,
        service_request_status="ACTION_REQUIRED" if sr_id else None,
    )


def _service(repo: _FakeDraftRepo) -> ConversationStateService:
    service = ConversationStateService(session=None)  # type: ignore[arg-type]
    service._draft_repo = repo  # type: ignore[assignment]
    return service


def _create_turn_state(backend_refs: dict[str, Any]) -> dict[str, Any]:
    return {
        "service_category": "FIT_OUT_AND_HANDOVER",
        "sub_category": "HANDOVER",
        "workflow_stage": "CREATE_SR",
        "collected_data": {},
        "missing_fields": ["lease_code"],
        "documents": [],
        "backend_refs": backend_refs,
    }


@pytest.mark.asyncio
async def test_a_released_sr_is_cleared_from_the_draft() -> None:
    repo = _FakeDraftRepo(_draft_on("321027"))
    await _service(repo).save_checkpoint(
        uuid4(),
        _create_turn_state({"user_role": "MALL_MANAGER", "released_sr_id": "321027"}),
    )
    assert repo.updates[-1]["sr_id"] is None
    assert repo.updates[-1]["service_request_status"] is None


@pytest.mark.asyncio
async def test_the_next_turn_does_not_get_the_old_sr_back() -> None:
    """The actual bug: save, then load, must not restore the released SR."""
    repo = _FakeDraftRepo(_draft_on("321027"))
    service = _service(repo)
    await service.save_checkpoint(
        uuid4(),
        _create_turn_state({"user_role": "MALL_MANAGER", "released_sr_id": "321027"}),
    )
    loaded = await service.load(uuid4())
    assert "sr_id" not in (loaded.get("backend_refs") or {})
    assert loaded["workflow_stage"] == "CREATE_SR"


@pytest.mark.asyncio
async def test_without_the_signal_an_sr_id_is_never_cleared() -> None:
    """Every other flow keeps the old rule: sr_id is written, not wiped."""
    repo = _FakeDraftRepo(_draft_on("321027"))
    await _service(repo).save_checkpoint(uuid4(), _create_turn_state({}))
    assert "sr_id" not in repo.updates[-1]
    assert repo.draft is not None and repo.draft.sr_id == "321027"


@pytest.mark.asyncio
async def test_an_sr_id_on_the_turn_is_still_written() -> None:
    repo = _FakeDraftRepo(_draft_on(None))
    await _service(repo).save_checkpoint(uuid4(), _create_turn_state({"sr_id": "321050"}))
    assert repo.updates[-1]["sr_id"] == "321050"


# ---------------------------------------------------------------------------
# Documents — reported on SR 321094: a second "create handover" in the same
# chat showed the first SR's upload, and deleting it there deleted it from
# SR 321094, because it was the same platform document id.
# ---------------------------------------------------------------------------

_OLD_DOC = {
    "document_id": "c026fb66-5839-47b3-b8b5-162d7368838e",
    "document_type_id": "SR_HANDOVER_OTHER",
    "filename": "ChatGPT Image.png",
}


def _submitted_draft_with_upload() -> SimpleNamespace:
    draft = _draft_on("321094")
    draft.workflow_stage = "SR_CREATED"
    draft.documents = [dict(_OLD_DOC)]
    return draft


@pytest.mark.asyncio
async def test_a_released_sr_takes_its_documents_with_it() -> None:
    repo = _FakeDraftRepo(_submitted_draft_with_upload())
    await _service(repo).save_checkpoint(
        uuid4(), _create_turn_state({"released_sr_id": "321094"})
    )
    assert repo.updates[-1]["documents"] == []


@pytest.mark.asyncio
async def test_the_new_flow_does_not_load_the_old_upload() -> None:
    repo = _FakeDraftRepo(_submitted_draft_with_upload())
    service = _service(repo)
    await service.save_checkpoint(
        uuid4(), _create_turn_state({"released_sr_id": "321094"})
    )
    loaded = await service.load(uuid4())
    assert loaded["documents"] == []


@pytest.mark.asyncio
async def test_uploads_of_the_new_request_are_kept_on_release() -> None:
    new_doc = {"document_id": "new-1", "document_type_id": "SR_HANDOVER_OTHER"}
    repo = _FakeDraftRepo(_submitted_draft_with_upload())
    state = _create_turn_state({"released_sr_id": "321094"})
    state["documents"] = [new_doc]
    await _service(repo).save_checkpoint(uuid4(), state)
    assert repo.updates[-1]["documents"] == [new_doc]


@pytest.mark.asyncio
async def test_without_the_signal_documents_are_still_merged() -> None:
    """An ordinary turn must not drop an upload the turn's state never saw."""
    repo = _FakeDraftRepo(_submitted_draft_with_upload())
    await _service(repo).save_checkpoint(uuid4(), _create_turn_state({"sr_id": "321094"}))
    assert [d["document_id"] for d in repo.updates[-1]["documents"]] == [
        _OLD_DOC["document_id"]
    ]


# ---------------------------------------------------------------------------
# Every "new request after a finished one" path sends the signal
# ---------------------------------------------------------------------------


def _finished(intent: str, stage: str, agent: str, message: str) -> dict[str, Any]:
    return {
        "active_agent": agent,
        "intent": intent,
        "workflow_stage": stage,
        "user_message": message,
        "collected_data": {"lease_code": "t0107456"},
        "documents": [dict(_OLD_DOC)],
        "backend_refs": {"sr_id": "321094"},
        "auth_context": SimpleNamespace(roles=["MALL_MANAGER", "DD_ENGINEER"]),
    }


@pytest.mark.asyncio
async def test_handover_stale_clear_releases_the_sr() -> None:
    from app.agents.graph.nodes.handover.handover_entry_node import handover_entry_node

    out = await handover_entry_node(
        _finished(
            "CREATE_RDD_SERVICE_REQUEST",
            "SR_CREATED",
            "rdd_handover_agent",
            "I need a handover request for Brand Link, lease t0107555",
        )
    )
    assert out["backend_refs"] == {"released_sr_id": "321094"}
    assert out["documents"] == []


@pytest.mark.asyncio
async def test_fitout_progress_stale_clear_releases_the_sr() -> None:
    from app.agents.graph.nodes.fitout_progress.fitout_progress_entry_node import (
        fitout_progress_entry_node,
    )

    out = await fitout_progress_entry_node(
        _finished(
            "CREATE_FITOUT_PROGRESS",
            "SR_CREATED",
            "rdd_fitout_progress_agent",
            "update fitout progress t0107555 40%",
        )
    )
    assert out["backend_refs"] == {"released_sr_id": "321094"}
    assert out["documents"] == []
