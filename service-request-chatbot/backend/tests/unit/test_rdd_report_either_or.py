"""Unit tests — the RDD upload card offers one report of the either-or pair.

The Handover Meeting Report and the Rejected Fitout and Handover Report are
alternatives: a handover is either reported or rejected. Once one was uploaded
the other stayed on the card, and since the chat's type dropdown is built from
the rows still missing, a reviewer who had filed one could attach the other
beside it.
"""

from __future__ import annotations

from typing import Any

from app.agents.services.review_decorations_service import (
    _document_requirement_block,
)

MEETING = "DR_SR_HANDOVER_REPORT"
REJECTED = "SR_REJECTED_HANDOVER_REPORT"
OTHER = "SR_HANDOVER_OTHER"


def _doc(doc_type: str, role: str | None = "DD_ENGINEER") -> dict[str, Any]:
    doc: dict[str, Any] = {"document_id": f"id-{doc_type}", "document_type_id": doc_type}
    if role:
        doc["role"] = role
    return doc


def _card(documents: list[dict[str, Any]]) -> dict[str, Any]:
    block = _document_requirement_block(
        stage="RDD_REVIEW",
        documents=documents,
        session_id="sess-rdd",
        sr_id="321026",
        collected={"guideLineLink": "https://guidelines.example.com"},
    )
    assert block is not None
    return block


def _keys(block: dict[str, Any]) -> list[str]:
    return [row["key"] for row in block["documents"]]


def _allowed(block: dict[str, Any]) -> list[str]:
    return list(block["uploadContext"]["allowedDocumentTypes"])


class TestBeforeEitherReport:
    def test_all_three_are_offered(self) -> None:
        block = _card([])
        assert _keys(block) == [MEETING, REJECTED, OTHER]
        assert _allowed(block) == [MEETING, REJECTED, OTHER]


class TestOnceOneReportIsUploaded:
    def test_a_meeting_report_takes_the_rejected_one_off_the_card(self) -> None:
        block = _card([_doc(MEETING)])
        assert _keys(block) == [MEETING, OTHER]
        assert _allowed(block) == [MEETING, OTHER]

    def test_a_rejected_report_takes_the_meeting_one_off_the_card(self) -> None:
        block = _card([_doc(REJECTED)])
        assert _keys(block) == [REJECTED, OTHER]
        assert _allowed(block) == [REJECTED, OTHER]

    def test_the_uploaded_report_still_shows_as_uploaded(self) -> None:
        rows = {row["key"]: row for row in _card([_doc(MEETING)])["documents"]}
        assert rows[MEETING]["status"] == "uploaded"
        assert rows[OTHER]["status"] == "missing"

    def test_other_stays_available_alongside_either(self) -> None:
        assert OTHER in _keys(_card([_doc(MEETING)]))
        assert OTHER in _keys(_card([_doc(REJECTED)]))

    def test_a_chat_upload_with_no_role_yet_counts(self) -> None:
        """A just-uploaded row is not stamped with a role until the next sync."""
        assert _keys(_card([_doc(REJECTED, role=None)])) == [REJECTED, OTHER]


class TestWhatDoesNotCount:
    def test_an_other_document_hides_neither_report(self) -> None:
        assert _keys(_card([_doc(OTHER)])) == [MEETING, REJECTED, OTHER]

    def test_another_roles_upload_does_not_close_the_choice(self) -> None:
        """Only DD_ENGINEER's own uploads count on the RDD card."""
        assert _keys(_card([_doc(MEETING, role="MALL_MANAGER")])) == [
            MEETING,
            REJECTED,
            OTHER,
        ]

    def test_a_card_holding_both_hides_neither(self) -> None:
        """Never hide a row that has a file on it."""
        assert _keys(_card([_doc(MEETING), _doc(REJECTED)])) == [
            MEETING,
            REJECTED,
            OTHER,
        ]


class TestDeletingTheReport:
    def test_both_come_back_once_it_is_gone(self) -> None:
        """Recomputed from the documents every turn, so a delete reopens it."""
        assert _keys(_card([_doc(MEETING)])) == [MEETING, OTHER]
        assert _keys(_card([])) == [MEETING, REJECTED, OTHER]


# ---------------------------------------------------------------------------
# The rebuilt card has to survive the turn that answers the upload
# ---------------------------------------------------------------------------


class TestTheRebuiltCardReachesTheClient:
    """Seen live: documents already held the Meeting Report, yet the turn
    carried chips only — so the client kept the card it had, the Rejected
    report stayed on it, and both could be uploaded."""

    def test_a_question_with_its_own_chips_still_carries_the_card(self) -> None:
        from app.agents.graph.nodes.shared.missing_field_node import (
            _carry_sr_decorations,
        )

        card = _card([_doc(MEETING)])
        own = {"suggestions": {"kind": "labeled_options", "options": []}}
        carried = _carry_sr_decorations(own, {"document_requirement": card})

        assert carried["suggestions"] is own["suggestions"]
        assert _keys(carried["document_requirement"]) == [MEETING, OTHER]

    def test_the_question_keeps_its_own_card_when_it_has_one(self) -> None:
        from app.agents.graph.nodes.shared.missing_field_node import (
            _carry_sr_decorations,
        )

        own = {"document_requirement": _card([])}
        carried = _carry_sr_decorations(own, {"document_requirement": _card([_doc(MEETING)])})
        assert _keys(carried["document_requirement"]) == [MEETING, REJECTED, OTHER]

    def test_nothing_inherited_changes_nothing(self) -> None:
        from app.agents.graph.nodes.shared.missing_field_node import (
            _carry_sr_decorations,
        )

        own = {"suggestions": {"kind": "labeled_options", "options": []}}
        assert _carry_sr_decorations(own, None) == own
        assert _carry_sr_decorations(own, {"promote_preview": True}) == own

    def test_the_card_rides_the_question_to_the_client(self) -> None:
        """End to end: filtered rows reach the client as refresh_card."""
        from app.services.response_ui_finalize import finalize_response_ui
        from app.agents.graph.nodes.shared.missing_field_node import (
            _carry_sr_decorations,
        )

        deco = _carry_sr_decorations(
            {"suggestions": {"kind": "labeled_options", "options": []}},
            {"document_requirement": _card([_doc(MEETING)])},
        )
        ui = finalize_response_ui(
            {"type": "text_question", "field": "actual_handover_date"},
            deco,
            draft_preview=None,
            response_message="What is the actual handover date?",
        )
        refreshed = ui["refresh_card"]
        assert [d["key"] for d in refreshed["documents"]] == [MEETING, OTHER]
        assert REJECTED not in refreshed["uploadContext"]["allowedDocumentTypes"]
