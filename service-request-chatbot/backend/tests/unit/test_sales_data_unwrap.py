"""Unwrap live vs mock GET /sales-data envelopes."""

from app.agents.services.sales_data_api_client import unwrap_sales_data_rows


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
