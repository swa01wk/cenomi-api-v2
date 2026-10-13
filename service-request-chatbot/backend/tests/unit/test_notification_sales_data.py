"""Unit tests — sales-data notification classification and list-row shape."""

from __future__ import annotations

from app.agents.services.notification_inbox_service import NotificationItem
from app.agents.services.notification_payload import row_payload, serialize_groups
from app.agents.services.notification_inbox_service import (
    NotificationGroup,
    NotificationInboxResult,
)
from datetime import date

from app.agents.services.notification_sales_data import (
    build_sales_data_notification_context,
    extract_period_date,
    extract_sales_period_hint,
    is_sales_data_notification_ref,
    sales_data_intent_for_ref,
)


def test_extract_period_hint_from_pending_copy() -> None:
    hint = extract_sales_period_hint(
        "Please submit sales data as of month August for Brand@Mall."
    )
    assert hint == "month August"


def test_extract_period_hint_with_year() -> None:
    hint = extract_sales_period_hint(
        "Please submit sales data as of August 2026 for Brand@Mall."
    )
    assert hint == "month August 2026"


def test_extract_period_date_month_name_only() -> None:
    # cenomi-api's real, monthly-frequency admin notification (no year at
    # all — getYearMonthDate() only ever names the month for these leases).
    result = extract_period_date(
        "In response to your request, Tenant has uploaded supporting "
        "documents for t0106037-Brand Ipekyol-Juri Mall corresponding to "
        "month May"
    )
    today = date.today()
    year = today.year if 5 <= today.month else today.year - 1
    assert result == f"{year:04d}-05"


def test_extract_period_date_year_only() -> None:
    assert (
        extract_period_date("Please review the sales data for X corresponding to year 2026")
        == "2026"
    )


def test_extract_period_date_bare_date() -> None:
    assert (
        extract_period_date("Please review the sales data for X corresponding to 5/9/2026")
        == "2026-09"
    )


def test_extract_period_date_no_match() -> None:
    assert extract_period_date("Please submit sales data as of month August for X.") is None
    assert extract_period_date(None) is None


def test_is_sales_data_ref_by_sub_category() -> None:
    assert is_sales_data_notification_ref(
        {"id": "LC-1001", "sub_category": "SALES_DATA"}
    )
    assert is_sales_data_notification_ref(
        {"id": "LC-1001", "sub_category": "SALES_DATA_PENDING"}
    )
    assert not is_sales_data_notification_ref(
        {"id": "11711", "sub_category": "HANDOVER"}
    )


def test_intent_mapping() -> None:
    assert (
        sales_data_intent_for_ref({"sub_category": "SALES_DATA_PENDING"})
        == "UPLOAD_SALES_DATA"
    )
    assert (
        sales_data_intent_for_ref(
            {"key": "SALES_DATA_NEED_UPLOAD_SUPPORTING_DOCUMENT"}
        )
        == "UPLOAD_SUPPORTING_DOCUMENT"
    )
    assert (
        sales_data_intent_for_ref({"sub_category": "SALES_DATA_SUBMITTED"})
        == "REVIEW_SALES_DATA"
    )


def test_build_context_parses_message_when_hint_missing() -> None:
    ctx = build_sales_data_notification_context(
        {
            "id": "LC-1001",
            "message": "Please submit sales data as of month August for X.",
        }
    )
    assert ctx == {
        "lease_code": "LC-1001",
        "period_hint": "month August",
        "source": "notification",
        "message": "Please submit sales data as of month August for X.",
    }


def test_sales_data_row_payload_uses_lease_code_not_sr_id() -> None:
    item = NotificationItem(
        notification_id="ntf-sd-001",
        sr_id="LC-1001",
        sub_category="SALES_DATA",
        message="Please submit sales data as of month August for Brand@Mall.",
        notification_type="SALES_DATA",
        template_key="SALES_DATA_PENDING",
        lease_id="1",
        action_required=True,
    )
    row = row_payload(item)
    assert row["kind"] == "sales_data"
    assert row["sr_id"] is None
    assert row["lease_code"] == "LC-1001"
    assert row["key"] == "SALES_DATA_PENDING"
    assert row["sub_category"] == "SALES_DATA"


def test_serialize_sales_data_group_service_category() -> None:
    item = NotificationItem(
        notification_id="ntf-sd-001",
        sr_id="LC-1001",
        sub_category="SALES_DATA",
        message="Please submit sales data as of month August for Brand@Mall.",
        notification_type="SALES_DATA",
        template_key="SALES_DATA_PENDING",
    )
    result = NotificationInboxResult(
        success=True,
        groups=[NotificationGroup("SALES_DATA", 1, [item], page=1, limit=10)],
    )
    groups = serialize_groups(result)
    assert groups[0]["service_category"] == "SALES_DATA"
    assert groups[0]["label"] == "Sales Data"
    assert groups[0]["notifications"][0]["kind"] == "sales_data"
