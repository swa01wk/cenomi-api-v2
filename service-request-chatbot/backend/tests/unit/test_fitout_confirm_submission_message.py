"""Unit tests — the create success message for fitout confirmation SRs.

All three fitout confirmations (FOS / END / TRADING) are single-level workflows
whose only approver is the role that normally raises them: DD_ENGINEER for FOS
and END, MALL_MANAGER / OPERATIONS for TRADING. When that role creates the SR
the platform approves it in the same transaction — Path B1 in the frontend spec
``fitout-confirmation-service-requests.md``.

The node used to promise "It will be routed to the approver for review" on every
create, which describes a step that never happens for the common case and left
users waiting for an approval that had already occurred.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.agents.graph.nodes.fitout_confirm.fitout_confirm_api_submission_node import (
    _created_sr_status,
    _was_auto_approved,
)


def _result(payload: dict[str, Any] | None) -> MagicMock:
    result = MagicMock()
    result.response_payload = payload
    return result


def _auth(*roles: str) -> MagicMock:
    auth = MagicMock()
    auth.roles = list(roles)
    return auth


class TestCreatedSrStatus:
    def test_reads_top_level_status(self) -> None:
        assert _created_sr_status(_result({"status": "approved"})) == "APPROVED"

    def test_reads_nested_data_status(self) -> None:
        result = _result({"data": {"status": "IN_PROGRESS"}})
        assert _created_sr_status(result) == "IN_PROGRESS"

    def test_reads_current_sr_status_alias(self) -> None:
        assert _created_sr_status(_result({"current_sr_status": "FINISHED"})) == (
            "FINISHED"
        )

    def test_missing_body_is_empty(self) -> None:
        assert _created_sr_status(_result(None)) == ""
        assert _created_sr_status(_result({})) == ""


class TestAutoApproveDetection:
    """The platform's own status is authoritative; role is only a fallback."""

    @pytest.mark.parametrize("status", ["APPROVED", "FINISHED", "COMPLETED", "DONE"])
    def test_terminal_status_means_no_further_review(self, status: str) -> None:
        assert (
            _was_auto_approved(
                _result({"status": status}), {}, "FIT_OUT_CONFIRM_FOS"
            )
            is True
        )

    @pytest.mark.parametrize("status", ["IN_PROGRESS", "IN_PROCESS", "PENDING"])
    def test_non_terminal_status_still_routes(self, status: str) -> None:
        assert (
            _was_auto_approved(
                _result({"status": status}),
                # Even for the approver's own sub-category: a real status from
                # the platform must beat the role inference.
                {"auth_context": _auth("DD_ENGINEER")},
                "FIT_OUT_CONFIRM_FOS",
            )
            is False
        )

    @pytest.mark.parametrize(
        ("role", "sub_category", "expected"),
        [
            # The approver raising their own type → approved in the same call.
            ("DD_ENGINEER", "FIT_OUT_CONFIRM_FOS", True),
            ("DD_ENGINEER", "FIT_OUT_CONFIRM_END", True),
            ("MALL_MANAGER", "FIT_OUT_CONFIRM_TRADING", True),
            ("OPERATIONS", "FIT_OUT_CONFIRM_TRADING", True),
            # Someone who cannot approve that type → genuinely routed.
            ("MALL_MANAGER", "FIT_OUT_CONFIRM_FOS", False),
            ("DD_ENGINEER", "FIT_OUT_CONFIRM_TRADING", False),
        ],
    )
    def test_role_fallback_when_response_has_no_status(
        self, role: str, sub_category: str, expected: bool
    ) -> None:
        assert (
            _was_auto_approved(_result({}), {"auth_context": _auth(role)}, sub_category)
            is expected
        )

    def test_unauthenticated_falls_back_to_routed(self) -> None:
        """Without a role we cannot claim approval — the cautious wording wins."""
        assert _was_auto_approved(_result({}), {}, "FIT_OUT_CONFIRM_FOS") is False
