"""Unit tests — DELETE /upload hydrates lease_id when the draft lacks it.

Covers the FM/RDD failure mode where document delete previously called the
platform with an empty lease_id because draft.collected_data had only
lease_code / contract_id (or nothing until platform GET).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.agents.services.platform_api_client import ServiceRequestGetResult
from app.api.routes.upload import delete_uploaded_document
from app.types.chat import AuthContext


def _auth(*roles: str) -> AuthContext:
    return AuthContext(
        subject_id="u1",
        roles=frozenset(roles or ("FM_MANAGER",)),
        bearer_token="tok",
    )


def _draft(
    *,
    session_id,
    collected_data: dict | None = None,
    documents: list | None = None,
    sr_id: str | None = "11711",
):
    return SimpleNamespace(
        id=uuid4(),
        session_id=session_id,
        collected_data=collected_data or {},
        documents=documents
        or [{"document_id": "doc-1", "document_type_id": "SR_HANDOVER_CHECKLIST"}],
        sr_id=sr_id,
        workflow_stage="FM_REVIEW",
    )


@pytest.mark.asyncio
async def test_delete_uses_explicit_lease_id_query_param() -> None:
    """Caller-provided lease_id is forwarded; no draft hydrate needed."""
    db = AsyncMock()
    platform = AsyncMock()
    platform.delete_document = AsyncMock(return_value={"success": True})
    repo = MagicMock()
    repo.get_by_session = AsyncMock(return_value=None)
    repo.update = AsyncMock()

    with (
        patch("app.core.config.settings") as mock_settings,
        patch(
            "app.agents.services.platform_api_client.get_platform_client",
            return_value=platform,
        ),
        patch("app.api.routes.upload.set_current_auth_context", return_value="tok"),
        patch("app.api.routes.upload.reset_current_auth_context"),
        patch("app.api.routes.upload.ServiceRequestDraftRepository", return_value=repo),
        patch("app.api.routes.upload._permission_service") as mock_perm,
    ):
        mock_settings.service_request_api_base_url = "https://platform.example"
        mock_settings.service_request_use_mock = False
        mock_perm.check = MagicMock(return_value=None)

        result = await delete_uploaded_document(
            db=db,
            document_id="doc-1",
            session_id=None,
            sr_id="11711",
            lease_id="9001",
            auth=_auth(),
        )

    assert result["document_id"] == "doc-1"
    platform.delete_document.assert_awaited_once()
    kwargs = platform.delete_document.await_args.kwargs
    assert kwargs["lease_id"] == "9001"
    assert kwargs["type_id"] == "11711"


@pytest.mark.asyncio
async def test_delete_reads_lease_id_from_draft_collected_data() -> None:
    session_id = uuid4()
    draft = _draft(
        session_id=session_id,
        collected_data={"lease_id": "9001", "lease_code": "t0033829"},
    )
    db = AsyncMock()
    platform = AsyncMock()
    platform.delete_document = AsyncMock(return_value={"success": True})
    repo = MagicMock()
    repo.get_by_session = AsyncMock(return_value=draft)
    repo.update = AsyncMock(return_value=draft)

    with (
        patch("app.core.config.settings") as mock_settings,
        patch(
            "app.agents.services.platform_api_client.get_platform_client",
            return_value=platform,
        ),
        patch("app.api.routes.upload.set_current_auth_context", return_value="tok"),
        patch("app.api.routes.upload.reset_current_auth_context"),
        patch("app.api.routes.upload.ServiceRequestDraftRepository", return_value=repo),
        patch("app.api.routes.upload._permission_service") as mock_perm,
    ):
        mock_settings.service_request_api_base_url = "https://platform.example"
        mock_settings.service_request_use_mock = False
        mock_perm.check = MagicMock(return_value=None)

        await delete_uploaded_document(
            db=db,
            document_id="doc-1",
            session_id=str(session_id),
            sr_id=None,
            lease_id=None,
            auth=_auth(),
        )

    kwargs = platform.delete_document.await_args.kwargs
    assert kwargs["lease_id"] == "9001"
    assert kwargs["type_id"] == "11711"


@pytest.mark.asyncio
async def test_delete_falls_back_to_contract_id_from_draft() -> None:
    session_id = uuid4()
    draft = _draft(
        session_id=session_id,
        collected_data={"contract_id": "88210", "lease_code": "t0028641"},
    )
    db = AsyncMock()
    platform = AsyncMock()
    platform.delete_document = AsyncMock(return_value={"success": True})
    repo = MagicMock()
    repo.get_by_session = AsyncMock(return_value=draft)
    repo.update = AsyncMock(return_value=draft)

    with (
        patch("app.core.config.settings") as mock_settings,
        patch(
            "app.agents.services.platform_api_client.get_platform_client",
            return_value=platform,
        ),
        patch("app.api.routes.upload.set_current_auth_context", return_value="tok"),
        patch("app.api.routes.upload.reset_current_auth_context"),
        patch("app.api.routes.upload.ServiceRequestDraftRepository", return_value=repo),
        patch("app.api.routes.upload._permission_service") as mock_perm,
    ):
        mock_settings.service_request_api_base_url = "https://platform.example"
        mock_settings.service_request_use_mock = False
        mock_perm.check = MagicMock(return_value=None)

        await delete_uploaded_document(
            db=db,
            document_id="doc-1",
            session_id=str(session_id),
            sr_id="11711",
            lease_id=None,
            auth=_auth(),
        )

    kwargs = platform.delete_document.await_args.kwargs
    assert kwargs["lease_id"] == "88210"


@pytest.mark.asyncio
async def test_delete_hydrates_lease_id_from_platform_when_draft_empty() -> None:
    """When draft has no lease_id/contract_id, hydrate via GET SR then delete."""
    session_id = uuid4()
    draft = _draft(session_id=session_id, collected_data={"lease_code": "t0033829"})
    db = AsyncMock()
    platform = AsyncMock()
    platform.delete_document = AsyncMock(return_value={"success": True})
    repo = MagicMock()
    repo.get_by_session = AsyncMock(return_value=draft)
    repo.update = AsyncMock(return_value=draft)

    mock_get = ServiceRequestGetResult(
        success=True,
        sr_id="11711",
        status="IN_PROCESS",
        service_request_operations=[],
        payload={
            "lease": "t0033829",
            "contract_id": 88210,
            "brand_id": 42,
            "property_id": 7,
        },
        endpoint="http://localhost/api/v1/service-requests/11711",
        status_code=200,
        latency_ms=10,
    )

    with (
        patch("app.core.config.settings") as mock_settings,
        patch(
            "app.agents.services.platform_api_client.get_platform_client",
            return_value=platform,
        ),
        patch("app.api.routes.upload.set_current_auth_context", return_value="tok"),
        patch("app.api.routes.upload.reset_current_auth_context"),
        patch("app.api.routes.upload.ServiceRequestDraftRepository", return_value=repo),
        patch("app.api.routes.upload._permission_service") as mock_perm,
        patch("app.api.routes.upload.get_service_request_api_service") as mock_sr,
        patch(
            "app.agents.services.lease_lookup_service.get_lease_lookup_service"
        ) as mock_lease,
    ):
        mock_settings.service_request_api_base_url = "https://platform.example"
        mock_settings.service_request_use_mock = False
        mock_perm.check = MagicMock(return_value=None)
        mock_sr.return_value = AsyncMock(
            get_service_request=AsyncMock(return_value=mock_get)
        )

        await delete_uploaded_document(
            db=db,
            document_id="doc-1",
            session_id=str(session_id),
            sr_id="11711",
            lease_id=None,
            auth=_auth(),
        )

    mock_lease.assert_not_called()
    kwargs = platform.delete_document.await_args.kwargs
    assert kwargs["lease_id"] == "88210"
    # Hydrated lease_id persisted back onto the draft for subsequent deletes.
    hydrate_updates = [
        call.args[1]
        for call in repo.update.await_args_list
        if isinstance(call.args[1], dict) and "collected_data" in call.args[1]
    ]
    assert hydrate_updates, "expected collected_data hydrate persist"
    assert hydrate_updates[0]["collected_data"]["lease_id"] == 88210


@pytest.mark.asyncio
async def test_delete_raises_when_platform_delete_fails() -> None:
    db = AsyncMock()
    platform = AsyncMock()
    platform.delete_document = AsyncMock(
        return_value={"success": False, "error": "lease required"}
    )
    repo = MagicMock()
    repo.get_by_session = AsyncMock(return_value=None)
    repo.update = AsyncMock()

    with (
        patch("app.core.config.settings") as mock_settings,
        patch(
            "app.agents.services.platform_api_client.get_platform_client",
            return_value=platform,
        ),
        patch("app.api.routes.upload.set_current_auth_context", return_value="tok"),
        patch("app.api.routes.upload.reset_current_auth_context"),
        patch("app.api.routes.upload.ServiceRequestDraftRepository", return_value=repo),
        patch("app.api.routes.upload._permission_service") as mock_perm,
    ):
        mock_settings.service_request_api_base_url = "https://platform.example"
        mock_settings.service_request_use_mock = False
        mock_perm.check = MagicMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await delete_uploaded_document(
                db=db,
                document_id="doc-1",
                session_id=None,
                sr_id="11711",
                lease_id="9001",
                auth=_auth(),
            )

    assert exc_info.value.status_code == 502
    assert "Platform delete failed" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_delete_removes_document_from_draft() -> None:
    session_id = uuid4()
    draft = _draft(
        session_id=session_id,
        collected_data={"lease_id": "9001"},
        documents=[
            {"document_id": "doc-1", "document_type_id": "SR_HANDOVER_CHECKLIST"},
            {"document_id": "doc-2", "document_type_id": "SR_HANDOVER_SITE_SURVEY"},
        ],
    )
    db = AsyncMock()
    platform = AsyncMock()
    platform.delete_document = AsyncMock(return_value={"success": True})
    repo = MagicMock()
    repo.get_by_session = AsyncMock(return_value=draft)
    repo.update = AsyncMock(return_value=draft)

    with (
        patch("app.core.config.settings") as mock_settings,
        patch(
            "app.agents.services.platform_api_client.get_platform_client",
            return_value=platform,
        ),
        patch("app.api.routes.upload.set_current_auth_context", return_value="tok"),
        patch("app.api.routes.upload.reset_current_auth_context"),
        patch("app.api.routes.upload.ServiceRequestDraftRepository", return_value=repo),
        patch("app.api.routes.upload._permission_service") as mock_perm,
    ):
        mock_settings.service_request_api_base_url = "https://platform.example"
        mock_settings.service_request_use_mock = False
        mock_perm.check = MagicMock(return_value=None)

        await delete_uploaded_document(
            db=db,
            document_id="doc-1",
            session_id=str(session_id),
            sr_id="11711",
            lease_id=None,
            auth=_auth(),
        )

    # Final draft update should keep only doc-2.
    assert repo.update.await_count >= 1
    last_update = repo.update.await_args_list[-1].args[1]
    assert last_update["documents"] == [
        {"document_id": "doc-2", "document_type_id": "SR_HANDOVER_SITE_SURVEY"}
    ]
