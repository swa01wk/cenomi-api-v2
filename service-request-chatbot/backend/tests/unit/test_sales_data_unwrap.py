"""Unwrap live vs mock GET /sales-data envelopes."""

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.services.sales_data_api_client import (
    SalesDataApiClient,
    unwrap_file_upload_response,
    unwrap_sales_data_rows,
)


def test_unwrap_mock_array_under_data() -> None:
    rows = unwrap_sales_data_rows({
        "data": [{"lease_code": "LC-2002", "document_id": "doc-sup-2002"}],
        "total": 1,
    })
    assert rows[0]["document_id"] == "doc-sup-2002"


def test_unwrap_live_list_envelope() -> None:
    rows = unwrap_sales_data_rows({
        "success": True,
        "data": {
            "list": [{"lease_code": "LC-2001", "document_id": "abc"}],
            "total_count": 1,
            "current_page": 1,
            "limit": 10,
        },
    })
    assert len(rows) == 1
    assert rows[0]["lease_code"] == "LC-2001"


def test_unwrap_bare_list() -> None:
    assert unwrap_sales_data_rows([{"lease_code": "LC-1"}])[0]["lease_code"] == "LC-1"


def test_unwrap_empty() -> None:
    assert unwrap_sales_data_rows(None) == []
    assert unwrap_sales_data_rows({}) == []


def test_unwrap_file_upload_live_envelope() -> None:
    body = {
        "success": True,
        "data": {
            "document_id": "abc-uuid",
            "file_path": "common-file-upload/sales-data/1/x.pdf",
            "document_type_id": "SD_SUP_DOC",
            "appian_document_id": 0,
        },
    }
    flat = unwrap_file_upload_response(body)
    assert flat["document_id"] == "abc-uuid"
    assert flat["file_path"].endswith("x.pdf")
    assert flat["success"] is True


def test_unwrap_file_upload_already_flat() -> None:
    assert unwrap_file_upload_response({"document_id": "doc-1"})["document_id"] == "doc-1"


def test_unwrap_file_upload_empty() -> None:
    assert unwrap_file_upload_response(None) == {}
    assert unwrap_file_upload_response("nope") == {}


@pytest.mark.asyncio
async def test_upload_file_sends_raw_body_and_unwraps_envelope() -> None:
    captured: dict = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "success": True,
                "data": {
                    "document_id": "abc-uuid",
                    "file_path": "path/x.pdf",
                    "document_type_id": "SD_SUP_DOC",
                    "appian_document_id": 0,
                },
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def put(self, url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return _Resp()

    with patch(
        "app.agents.services.sales_data_api_client.httpx.AsyncClient",
        return_value=_Client(),
    ):
        client = SalesDataApiClient(bearer_token="tok")
        result = await client.upload_file(
            b"%PDF",
            "pos.pdf",
            "application/pdf",
            document_type_id="SD_SUP_DOC",
            lease_id=6,
            lease_code="t0106037",
        )

    assert result["document_id"] == "abc-uuid"
    assert "files" not in captured["kwargs"]
    assert captured["kwargs"]["content"] == b"%PDF"
    assert captured["kwargs"]["params"]["lease_code"] == "t0106037"
    assert captured["kwargs"]["params"]["document_type_id"] == "SD_SUP_DOC"
    assert captured["kwargs"]["headers"]["Authorization"] == "Bearer tok"


def test_unwrap_mock_array_under_data() -> None:
    rows = unwrap_sales_data_rows({
        "data": [{"lease_code": "LC-2002", "document_id": "doc-sup-2002"}],
        "total": 1,
    })
    assert rows[0]["document_id"] == "doc-sup-2002"


def test_unwrap_live_list_envelope() -> None:
    rows = unwrap_sales_data_rows({
        "success": True,
        "data": {
            "list": [{"lease_code": "LC-2001", "document_id": "abc"}],
            "total_count": 1,
            "current_page": 1,
            "limit": 10,
        },
    })
    assert len(rows) == 1
    assert rows[0]["lease_code"] == "LC-2001"


def test_unwrap_bare_list() -> None:
    assert unwrap_sales_data_rows([{"lease_code": "LC-1"}])[0]["lease_code"] == "LC-1"


def test_unwrap_empty() -> None:
    assert unwrap_sales_data_rows(None) == []
    assert unwrap_sales_data_rows({}) == []
