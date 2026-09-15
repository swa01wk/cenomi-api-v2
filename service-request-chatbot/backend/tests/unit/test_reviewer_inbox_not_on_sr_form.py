"""Unit tests — a reviewer's SR inbox stays off a Service Request form.

An RDD Project Manager opened the Fitout Start Confirmation form and asked for
one. The assistant answered correctly ("please share the lease code for the
tenant fit-out request") and published five chips underneath it — which were
not leases at all but that reviewer's own pending *handover* SRs
(``Brand Al Majed — Hamraa Mall (321005)``, all IN_PROCESS HANDOVER). Tapping
one opened that handover, so the session moved to a handover review and asked
for the handover report's guideline link.

The inbox is attached before the graph runs, gated on the session having no
active agent — which is true of the FIRST turn of every new session, exactly
when the user is telling the assistant what they came to do. It never showed
for a Mall Manager on Trading Confirmation because only FM / Operations /
DD Engineer / Admin have a handover inbox at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.services.review_decorations_service import build_inbox_decorations
from app.services.chat_orchestration_service import _is_on_a_service_request_form
from app.types.chat import AuthContext


class TestBeingOnAServiceRequestForm:
    @pytest.mark.parametrize(
        "subcategory",
        [
            "Fitout Start Confirmation",
            "Fitout End Confirmation",
            "Trading Confirmation",
            "Handover",
        ],
    )
    def test_a_named_sr_page_counts(self, subcategory: str) -> None:
        assert (
            _is_on_a_service_request_form(
                {"help_category": "Fitout & Handover", "help_subcategory": subcategory}
            )
            is True
        )

    @pytest.mark.parametrize(
        "context",
        [
            None,
            {},
            {"help_subcategory": ""},
            {"help_subcategory": "   "},
            # The dashboard: a category with no SR type behind it.
            {"help_category": "Fitout & Handover", "current_url_pattern": "/dashboard"},
        ],
    )
    def test_anywhere_else_does_not(self, context: Any) -> None:
        assert _is_on_a_service_request_form(context) is False

    def test_a_non_dict_context_is_ignored(self) -> None:
        assert _is_on_a_service_request_form("Fitout Start Confirmation") is False


class TestWhoHasAnInboxAtAll:
    """Why a Mall Manager on Trading Confirmation never saw this."""

    @pytest.mark.parametrize("role", ["DD_ENGINEER", "FM_MANAGER", "OPERATIONS", "ADMIN"])
    def test_a_handover_reviewer_has_one(self, role: str) -> None:
        auth = AuthContext(subject_id="u-1", roles=frozenset({role}))
        assert build_inbox_decorations(auth) != {}

    def test_a_mall_manager_does_not(self) -> None:
        auth = AuthContext(subject_id="u-1", roles=frozenset({"MALL_MANAGER"}))
        assert build_inbox_decorations(auth) == {}

    def test_the_chips_are_handover_srs_not_leases(self) -> None:
        """What the reviewer actually saw: their pending handover queue."""
        auth = AuthContext(subject_id="u-1", roles=frozenset({"DD_ENGINEER"}))
        spec = build_inbox_decorations(auth)["suggestions"]
        assert spec["resource"] == "service_requests"
        assert spec["filters"]["sub_category"] == "HANDOVER"
