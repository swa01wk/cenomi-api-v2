"""Pending-month reminder helpers and gate."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.graph.nodes.sales_data.pending_months import (
    extract_lease_code,
    format_other_months_reminder,
    format_pending_list_message,
    format_pending_list_summary,
    other_pending_months,
    pending_period_items,
)


def _row(lease: str, start: str, end: str, status: str = "PENDING") -> dict[str, Any]:
    return {
        "lease_code": lease,
        "sales_period_start": start,
        "sales_period_end": end,
        "status": status,
        "brand_name": "Noon Fashion",
        "property_name": "Riyadh Park",
    }


def test_extract_lease_code() -> None:
    assert extract_lease_code("upload LC-1001 August") == "LC-1001"
    assert extract_lease_code("lease LC 1001") == "LC-1001"
    assert extract_lease_code("no lease here") is None


def test_pending_period_items_skips_need_reupload() -> None:
    items = pending_period_items([
        _row("LC-1001", "2026-08-01", "2026-08-31"),
        _row("LC-1001", "2026-07-01", "2026-07-31"),
        _row("LC-1003", "2026-08-01", "2026-08-31", "NEED_REUPLOAD"),
    ])
    assert {(i["lease_code"], i["year_month"]) for i in items} == {
        ("LC-1001", "2026-08"),
        ("LC-1001", "2026-07"),
    }


def test_other_pending_months_same_lease_only() -> None:
    items = pending_period_items([
        _row("LC-1001", "2026-08-01", "2026-08-31"),
        _row("LC-1001", "2026-07-01", "2026-07-31"),
        _row("LC-1002", "2026-08-01", "2026-08-31"),
    ])
    others = other_pending_months(items, "LC-1001", "2026-08")
    assert [o["year_month"] for o in others] == ["2026-07"]


def test_reminder_copy_allows_same_csv() -> None:
    others = [
        {"label": "July 2026", "year_month": "2026-07"},
        {"label": "June 2026", "year_month": "2026-06"},
        {"label": "May 2026", "year_month": "2026-05"},
        {"label": "April 2026", "year_month": "2026-04"},
        {"label": "March 2026", "year_month": "2026-03"},
        {"label": "February 2026", "year_month": "2026-02"},
    ]
    message = format_other_months_reminder("LC-1001", "August 2026", others)
    assert "same CSV" in message
    assert "February 2026" in message
    assert "July 2026, June 2026" in message


def test_pending_list_copy_allows_multi_month_file() -> None:
    items = pending_period_items([
        _row("LC-1001", "2026-08-01", "2026-08-31"),
        _row("LC-1001", "2026-07-01", "2026-07-31"),
    ])
    message = format_pending_list_message(items)
    assert "several pending months" in message
    assert "July 2026" in message
    assert "LC-1001" in message


def test_pending_list_summary_omits_lease_and_month_enumeration() -> None:
    message = format_pending_list_summary()
    assert "pending sales data" in message
    assert "several months" in message
    assert "July 2026" not in message
    assert "LC-1001" not in message


def _state(**overrides: Any) -> dict[str, Any]:
    return {
        "session_id": "sess-pm-1",
        "user_id": "user-1",
        "user_message": "",
        "intent": "UPLOAD_SALES_DATA",
        "action_override": None,
        "workflow_stage": "PENDING_MONTHS_CHECK",
        "backend_refs": {},
        "collected_data": {},
        "corrected_fields": {},
        **overrides,
    }


@pytest.mark.asyncio
async def test_gate_sends_every_pending_lease_and_month() -> None:
    """Nothing is hidden from the pending list.

    This used to cap display at 5 leases and at the 2 most recent distinct
    months found anywhere in the dataset, to avoid a wall of chips for a
    tenant with years of backlog. But that made the rest unreachable from
    chat — a month outside the window could only be uploaded by typing its
    lease code from memory. The full set is sent now and the card pages
    through it instead.
    """
    from app.agents.graph.nodes.sales_data.pending_months_gate_node import (
        pending_months_gate_node,
    )

    # 7 leases, one with years of backlog alongside recent months.
    rows = [
        _row("LC-1001", "2016-01-01", "2016-01-31"),
        _row("LC-1001", "2016-02-01", "2016-02-28"),
        _row("LC-1001", "2026-07-01", "2026-07-31"),
        _row("LC-1001", "2026-08-01", "2026-08-31"),
    ]
    for i in range(2, 8):  # LC-1002..LC-1007
        rows.append(_row(f"LC-100{i}", "2026-08-01", "2026-08-31"))

    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=rows)
    with (
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await pending_months_gate_node(
            _state(user_message="I want to upload my sales data")
        )

    ui = result["response_ui"]
    assert ui["total_lease_count"] == 7
    assert len(ui["leases"]) == 7, "every lease must be sent, not just the first 5"
    assert ui["total_month_count"] == 10
    assert ui["more_lease_count"] == 0
    assert ui["older_lease_count"] == 0

    lc1001 = next(lease for lease in ui["leases"] if lease["lease_code"] == "LC-1001")
    # The 2016 backlog is kept, in chronological order.
    assert [m["label"] for m in lc1001["months"]] == [
        "January 2016",
        "February 2016",
        "July 2026",
        "August 2026",
    ]


@pytest.mark.asyncio
async def test_gate_keeps_leases_whose_backlog_is_entirely_old() -> None:
    """A lease with only old pending months must still be listed — it is
    genuinely overdue, so hiding it was the worst case of the old cap."""
    from app.agents.graph.nodes.sales_data.pending_months_gate_node import (
        pending_months_gate_node,
    )

    rows = [
        _row("LC-2001", "2023-12-01", "2023-12-31"),  # entirely old backlog
        _row("LC-2002", "2026-08-01", "2026-08-31"),
        _row("LC-2002", "2026-09-01", "2026-09-30"),
    ]

    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=rows)
    with (
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await pending_months_gate_node(
            _state(user_message="I want to upload my sales data")
        )

    ui = result["response_ui"]
    lease_codes = [lease["lease_code"] for lease in ui["leases"]]
    assert lease_codes == ["LC-2001", "LC-2002"]
    assert ui["total_lease_count"] == 2
    assert ui["older_lease_count"] == 0


@pytest.mark.asyncio
async def test_gate_lists_pending_when_no_month_named() -> None:
    from app.agents.graph.nodes.sales_data.pending_months_gate_node import (
        pending_months_gate_node,
    )

    rows = [
        _row("LC-1001", "2026-08-01", "2026-08-31"),
        _row("LC-1001", "2026-07-01", "2026-07-31"),
        _row("LC-1002", "2026-08-01", "2026-08-31"),
    ]
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=rows)
    with (
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await pending_months_gate_node(
            _state(user_message="I want to upload my sales data")
        )
    assert result["workflow_stage"] == "AWAITING_PENDING_MONTHS_CHOICE"
    assert result["response_ui"]["type"] == "pending_months_list"
    # The enumeration itself is shown once, as chips (response_ui.leases) —
    # the bubble (response_message) only carries the intro/trailing prose,
    # not a duplicate of the same list PendingMonthsListCard already shows.
    assert "July 2026" not in result["response_message"]
    lc1001_months = next(
        lease["months"]
        for lease in result["response_ui"]["leases"]
        if lease["lease_code"] == "LC-1001"
    )
    assert "July 2026" in [month["label"] for month in lc1001_months]
    assert result["response_ui"]["show_file_upload"] is True


@pytest.mark.asyncio
async def test_gate_reminds_other_months_on_same_lease() -> None:
    from app.agents.graph.nodes.sales_data.pending_months_gate_node import (
        pending_months_gate_node,
    )

    rows = [
        _row("LC-1001", "2026-08-01", "2026-08-31"),
        _row("LC-1001", "2026-07-01", "2026-07-31"),
        _row("LC-1002", "2026-08-01", "2026-08-31"),
    ]
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=rows)
    with (
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await pending_months_gate_node(
            _state(user_message="I want to upload August 2026 for LC-1001")
        )
    assert result["workflow_stage"] == "AWAITING_OTHER_MONTHS_REMINDER"
    assert result["response_ui"]["type"] == "other_months_reminder"
    assert result["response_ui"]["asked_label"] == "August 2026"
    assert result["response_ui"]["other_months"][0]["label"] == "July 2026"
    assert result["response_ui"]["show_file_upload"] is False
    assert "same CSV" in result["response_message"]


@pytest.mark.asyncio
async def test_gate_proceed_opens_upload_prompt() -> None:
    from app.agents.graph.nodes.sales_data.pending_months_gate_node import (
        pending_months_gate_node,
    )

    items = pending_period_items([
        _row("LC-1001", "2026-08-01", "2026-08-31"),
        _row("LC-1001", "2026-07-01", "2026-07-31"),
    ])
    result = await pending_months_gate_node(
        _state(
            user_message="Proceed with August.",
            action_override="proceed_other_months",
            workflow_stage="AWAITING_OTHER_MONTHS_REMINDER",
            collected_data={
                "_pending_items": items,
                "_upload_lease": "LC-1001",
                "_asked_period": {
                    "label": "August 2026",
                    "year_month": "2026-08",
                    "start": "2026-08-01",
                    "end": "2026-08-31",
                },
            },
            backend_refs={
                "asked_period": {
                    "label": "August 2026",
                    "year_month": "2026-08",
                    "start": "2026-08-01",
                    "end": "2026-08-31",
                }
            },
        )
    )
    assert result["workflow_stage"] == "UPLOAD_INTENT"
    assert result["response_ui"]["type"] == "sales_data_upload_prompt"
    assert "August 2026" in result["response_ui"]["message"]


@pytest.mark.asyncio
async def test_gate_switch_month_skips_second_reminder() -> None:
    from app.agents.graph.nodes.sales_data.pending_months_gate_node import (
        pending_months_gate_node,
    )

    items = pending_period_items([
        _row("LC-1001", "2026-08-01", "2026-08-31"),
        _row("LC-1001", "2026-07-01", "2026-07-31"),
    ])
    result = await pending_months_gate_node(
        _state(
            user_message="Switch to July 2026.",
            action_override="switch_pending_month",
            workflow_stage="AWAITING_OTHER_MONTHS_REMINDER",
            corrected_fields={"year_month": "2026-07"},
            collected_data={
                "_pending_items": items,
                "_upload_lease": "LC-1001",
                "_asked_period": {
                    "label": "August 2026",
                    "year_month": "2026-08",
                    "start": "2026-08-01",
                    "end": "2026-08-31",
                },
            },
        )
    )
    assert result["workflow_stage"] == "UPLOAD_INTENT"
    assert result["response_ui"]["type"] == "sales_data_upload_prompt"
    assert "July 2026" in result["response_ui"]["message"]


@pytest.mark.asyncio
async def test_gate_skips_reminder_when_lease_has_only_that_month() -> None:
    from app.agents.graph.nodes.sales_data.pending_months_gate_node import (
        pending_months_gate_node,
    )

    rows = [_row("LC-1002", "2026-08-01", "2026-08-31")]
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=rows)
    with (
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await pending_months_gate_node(
            _state(user_message="Upload August 2026 for LC-1002")
        )
    assert result["workflow_stage"] == "UPLOAD_INTENT"
    assert result["response_ui"]["type"] == "sales_data_upload_prompt"


@pytest.mark.asyncio
async def test_gate_lists_when_asked_month_matches_multiple_leases() -> None:
    from app.agents.graph.nodes.sales_data.pending_months_gate_node import (
        pending_months_gate_node,
    )

    rows = [
        _row("LC-1001", "2026-08-01", "2026-08-31"),
        _row("LC-1001", "2026-07-01", "2026-07-31"),
        _row("LC-1002", "2026-08-01", "2026-08-31"),
    ]
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=rows)
    with (
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await pending_months_gate_node(
            _state(user_message="I want to upload August 2026")
        )
    assert result["workflow_stage"] == "AWAITING_PENDING_MONTHS_CHOICE"
    assert result["response_ui"]["type"] == "pending_months_list"


@pytest.mark.asyncio
async def test_gate_notification_lease_reminds_other_months() -> None:
    from app.agents.graph.nodes.sales_data.pending_months_gate_node import (
        pending_months_gate_node,
    )

    rows = [
        _row("LC-1001", "2026-08-01", "2026-08-31"),
        _row("LC-1001", "2026-07-01", "2026-07-31"),
        _row("LC-1002", "2026-08-01", "2026-08-31"),
    ]
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=rows)
    with (
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await pending_months_gate_node(
            _state(
                user_message="I want to upload August 2026",
                backend_refs={
                    "notification_context": {
                        "lease_code": "LC-1001",
                        "period_hint": "August 2026",
                    }
                },
            )
        )
    assert result["workflow_stage"] == "AWAITING_OTHER_MONTHS_REMINDER"
    assert result["response_ui"]["lease_code"] == "LC-1001"
    assert result["response_ui"]["asked_label"] == "August 2026"


@pytest.mark.asyncio
async def test_gate_notification_no_period_scopes_list_to_lease() -> None:
    """Regression guard: opening a notification with no period_hint used to
    dump every lease's pending months. It must scope to the lease named in
    notification_context when that lease has MULTIPLE pending months."""
    from app.agents.graph.nodes.sales_data.pending_months_gate_node import (
        pending_months_gate_node,
    )

    rows = [
        _row("LC-1001", "2026-08-01", "2026-08-31"),
        _row("LC-1001", "2026-07-01", "2026-07-31"),
        _row("LC-1002", "2026-08-01", "2026-08-31"),
        _row("LC-1003", "2026-08-01", "2026-08-31"),
    ]
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=rows)
    with (
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await pending_months_gate_node(
            _state(
                user_message="",
                backend_refs={
                    "notification_context": {"lease_code": "LC-1001", "period_hint": ""}
                },
            )
        )
    assert result["workflow_stage"] == "AWAITING_PENDING_MONTHS_CHOICE"
    assert result["response_ui"]["type"] == "pending_months_list"
    leases_shown = {entry["lease_code"] for entry in result["response_ui"]["leases"]}
    assert leases_shown == {"LC-1001"}
    assert "LC-1002" not in result["response_message"]
    assert "LC-1003" not in result["response_message"]


@pytest.mark.asyncio
async def test_gate_notification_no_period_single_month_goes_to_upload() -> None:
    """Lease from the notification has exactly one pending month — skip the
    list and go straight to the upload prompt for it."""
    from app.agents.graph.nodes.sales_data.pending_months_gate_node import (
        pending_months_gate_node,
    )

    rows = [
        _row("LC-1001", "2026-08-01", "2026-08-31"),
        _row("LC-1002", "2026-08-01", "2026-08-31"),
    ]
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=rows)
    with (
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await pending_months_gate_node(
            _state(
                user_message="",
                backend_refs={
                    "notification_context": {"lease_code": "LC-1001", "period_hint": ""}
                },
            )
        )
    assert result["workflow_stage"] == "UPLOAD_INTENT"
    assert result["response_ui"]["type"] == "sales_data_upload_prompt"
    assert result["response_ui"]["lease_code"] == "LC-1001"
    assert "August 2026" in result["response_ui"]["message"]


@pytest.mark.asyncio
async def test_gate_notification_already_submitted_does_not_dump_other_leases() -> None:
    """THE REPORTED BUG: tenant opens the same notification a second time
    after already submitting it. LC-1001 no longer has a PENDING row, but
    other leases under the same profile still do. Must say LC-1001 is done
    instead of silently showing every other lease's pending data as if it
    belonged to this notification."""
    from app.agents.graph.nodes.sales_data.pending_months_gate_node import (
        pending_months_gate_node,
    )

    # LC-1001's June row has already moved off PENDING — only sibling leases
    # remain pending, mirroring the multi-lease company profile in the bug
    # report (t0106028, t0106032, ... all still PENDING for other leases).
    rows = [
        _row("LC-1001", "2026-06-01", "2026-06-30", status="SUBMITTED"),
        _row("LC-1002", "2026-06-01", "2026-06-30"),
        _row("LC-1003", "2026-06-01", "2026-06-30"),
    ]
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=rows)
    with (
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await pending_months_gate_node(
            _state(
                user_message="",
                backend_refs={
                    "notification_context": {"lease_code": "LC-1001", "period_hint": ""}
                },
            )
        )
    assert "LC-1001" in result["response_message"]
    assert "already been submitted" in result["response_message"]
    assert "pending sales data" in result["response_message"]
    # Other leases are still surfaced (useful) via the card's chips — the
    # bubble no longer duplicates the same enumeration in prose.
    assert "LC-1002" not in result["response_message"]
    assert "LC-1003" not in result["response_message"]
    other_leases = {lease["lease_code"] for lease in result["response_ui"]["leases"]}
    assert other_leases == {"LC-1002", "LC-1003"}


@pytest.mark.asyncio
async def test_gate_notification_already_submitted_no_other_pending() -> None:
    """Same as above but the tenant has nothing else pending anywhere."""
    from app.agents.graph.nodes.sales_data.pending_months_gate_node import (
        pending_months_gate_node,
    )

    rows = [_row("LC-1001", "2026-06-01", "2026-06-30", status="SUBMITTED")]
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=rows)
    with (
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await pending_months_gate_node(
            _state(
                user_message="",
                backend_refs={
                    "notification_context": {"lease_code": "LC-1001", "period_hint": ""}
                },
            )
        )
    assert "LC-1001" in result["response_message"]
    assert "already been submitted" in result["response_message"]
    assert "no other pending" in result["response_message"].lower()
    assert result["response_ui"]["type"] == "message"


@pytest.mark.asyncio
async def test_gate_asked_month_not_pending_uses_summary_not_enumeration() -> None:
    from app.agents.graph.nodes.sales_data.pending_months_gate_node import (
        pending_months_gate_node,
    )

    rows = [
        _row("LC-1001", "2026-08-01", "2026-08-31"),
        _row("LC-1001", "2026-07-01", "2026-07-31"),
    ]
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=rows)
    with (
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await pending_months_gate_node(
            _state(user_message="Upload June 2026 for LC-1001")
        )
    assert result["workflow_stage"] == "AWAITING_PENDING_MONTHS_CHOICE"
    assert "June 2026" in result["response_message"]
    assert "is not pending" in result["response_message"]
    assert "listed below" in result["response_message"]
    assert "July 2026" not in result["response_message"]
    lc1001_months = next(
        lease["months"]
        for lease in result["response_ui"]["leases"]
        if lease["lease_code"] == "LC-1001"
    )
    assert {month["label"] for month in lc1001_months} == {"July 2026", "August 2026"}


@pytest.mark.asyncio
async def test_gate_list_pick_month_reminds_other_months() -> None:
    from app.agents.graph.nodes.sales_data.pending_months_gate_node import (
        pending_months_gate_node,
    )

    items = pending_period_items([
        _row("LC-1001", "2026-08-01", "2026-08-31"),
        _row("LC-1001", "2026-07-01", "2026-07-31"),
        _row("LC-1002", "2026-08-01", "2026-08-31"),
    ])
    result = await pending_months_gate_node(
        _state(
            user_message="LC-1001 August 2026",
            workflow_stage="AWAITING_PENDING_MONTHS_CHOICE",
            collected_data={"_pending_items": items},
        )
    )
    assert result["workflow_stage"] == "AWAITING_OTHER_MONTHS_REMINDER"
    assert result["response_ui"]["asked_label"] == "August 2026"
    assert result["response_ui"]["other_months"][0]["label"] == "July 2026"


@pytest.mark.asyncio
async def test_gate_refetches_so_a_ui_submission_is_picked_up() -> None:
    """The listing page beside the chat can submit a month at any time and
    nothing tells the agent when it does. The gate must re-read rather than
    reuse its cached snapshot, or it goes on offering months that were
    already submitted — and tapping one fails."""
    from app.agents.graph.nodes.sales_data.pending_months_gate_node import (
        pending_months_gate_node,
    )

    # Cached from the previous turn: two pending months.
    stale_cache = [
        {
            "lease_code": "LC-3001",
            "year_month": "2026-08",
            "label": "August 2026",
            "sales_period_start": "2026-08-01",
            "sales_period_end": "2026-08-31",
            "brand_name": "",
            "property_name": "",
        },
        {
            "lease_code": "LC-3001",
            "year_month": "2026-09",
            "label": "September 2026",
            "sales_period_start": "2026-09-01",
            "sales_period_end": "2026-09-30",
            "brand_name": "",
            "property_name": "",
        },
    ]
    # Server truth now: August was submitted via the listing page.
    client = AsyncMock()
    client.get_sales_data = AsyncMock(
        return_value=[_row("LC-3001", "2026-09-01", "2026-09-30")]
    )

    state = _state(user_message="LC-3001")
    state["workflow_stage"] = "AWAITING_PENDING_MONTHS_CHOICE"
    state["collected_data"] = {"_pending_items": stale_cache}
    state["backend_refs"] = {"pending_items": stale_cache}

    with (
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.get_current_bearer_token",
            return_value="tok",
        ),
        patch(
            "app.agents.graph.nodes.sales_data.pending_months_gate_node.SalesDataApiClient",
            return_value=client,
        ),
    ):
        result = await pending_months_gate_node(state)

    # The server was re-read rather than the stale cache being reused.
    client.get_sales_data.assert_awaited()

    # Only September is left, so the gate goes straight to its upload prompt
    # instead of listing a month that has already been submitted.
    items = result["collected_data"]["_pending_items"]
    assert [i["year_month"] for i in items] == ["2026-09"]
    assert result["collected_data"]["_asked_period"]["year_month"] == "2026-09"
