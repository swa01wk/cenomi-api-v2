"""Sales Data working data survives between turns; one-shot upload ids do not."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agents.services.conversation_state_service import ConversationStateService


class _Repo:
    def __init__(self) -> None:
        self.draft = None

    async def get_by_session(self, session_id):
        return self.draft

    async def create(self, **kwargs):
        self.draft = SimpleNamespace(
            sr_id=None, service_request_status=None, **{
                k: kwargs[k] for k in (
                    "service_category", "sub_category", "workflow_stage",
                    "collected_data", "missing_fields", "documents",
                )
            },
        )


@pytest.mark.asyncio
async def test_csv_rows_survive_to_the_next_turn() -> None:
    service = ConversationStateService.__new__(ConversationStateService)
    service._draft_repo = _Repo()
    sid = uuid4()
    await service.save_checkpoint(sid, {
        "service_category": "SALES_DATA",
        "sub_category": "BULK_UPLOAD",
        "workflow_stage": "CSV_CORRECTION",
        "collected_data": {},
        "backend_refs": {
            "csv_rows_all": [{"lease_code": "t0112235"}],
            "csv_filename": "sales.csv",
            "csv_session_id": "upload-1",   # one-shot trigger — must not persist
        },
    })
    loaded = await service.load(sid)
    refs = loaded["backend_refs"]
    assert refs["csv_rows_all"] == [{"lease_code": "t0112235"}]
    assert refs["csv_filename"] == "sales.csv"
    assert "csv_session_id" not in refs
    assert "_sales_refs" not in loaded["collected_data"]


@pytest.mark.asyncio
async def test_non_sales_sessions_are_unaffected() -> None:
    service = ConversationStateService.__new__(ConversationStateService)
    service._draft_repo = _Repo()
    sid = uuid4()
    await service.save_checkpoint(sid, {
        "service_category": "FIT_OUT_AND_HANDOVER",
        "sub_category": "HANDOVER",
        "workflow_stage": "CREATE_SR",
        "collected_data": {"lease_code": "t1"},
        "backend_refs": {"csv_rows_all": [{"x": 1}]},
    })
    loaded = await service.load(sid)
    assert "backend_refs" not in loaded
