"""Unit tests — the combined RDD review question (HANDOVER_SHORT_FLOW_ENABLED).

RDD review used to ask four questions in a row: the Handover Meeting Report
outcome, then the actual handover date, then each end of the fit-out window.
They are now one question, with quick picks carrying the policy dates.

Test groups
-----------
TestFlagOff       — Default behaviour is the field-per-turn flow.
TestGroupAsk      — One question, its wording, and where it may fire.
TestNarrowing     — Whatever the user leaves out is asked again, alone.
TestSuggestions   — Dates derived from the SR's expected handover date.
TestChips         — One tap answers the whole question.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.agents.graph.nodes.handover.missing_field_node import missing_field_node
from app.agents.schemas.handover_schema import RDD_REVIEW_FIELD_GROUP
from app.agents.services import rdd_suggestions_service as rdd


@pytest.fixture()
def short_flow_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "handover_short_flow_enabled", True)


@pytest.fixture()
def no_platform_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """No lease database: suggestions degrade, the question still works."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "cenomi_database_url", None)


def _rdd_ready(**extra: Any) -> dict[str, Any]:
    """An RDD review with the guideline link and the meeting report in place."""
    base: dict[str, Any] = {
        "lease_code": "t0112136",
        "lease_id": 274251,
        "brand": "Nike",
        "mall": "Riyadh Park",
        "guideLineLink": "https://cenomi.example/guidelines",
        "unit_readiness_date": "2026-09-03",
        "expected_handover_date": "2026-09-10",
    }
    base.update(extra)
    return base


def _state(collected: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": "sess-rdd",
        "user_id": "user-1",
        "active_agent": "rdd_handover_agent",
        "workflow_stage": "RDD_REVIEW",
        "collected_data": collected,
        "documents": [{"document_type_id": "DR_SR_HANDOVER_REPORT"}],
    }


def _labels(result: dict[str, Any]) -> list[str]:
    return [
        o["label"]
        for o in result.get("response_decorations", {})
        .get("suggestions", {})
        .get("options", [])
    ]


class TestFlagOff:
    @pytest.mark.asyncio
    async def test_asks_the_outcome_on_its_own(self) -> None:
        result = await missing_field_node(_state(_rdd_ready()))
        assert result["response_ui"]["field"] == "document_status"
        assert "fields" not in result["response_ui"]


class TestGroupAsk:
    @pytest.mark.asyncio
    async def test_one_question_covers_outcome_and_dates(
        self, short_flow_on: None, no_platform_db: None
    ) -> None:
        result = await missing_field_node(_state(_rdd_ready()))

        message = result["response_message"]
        assert "Report outcome and milestone dates" in message
        assert "Outcome" in message and "Fit-out start" in message
        # Trading is derived, so it is explained rather than asked.
        assert "trading date is worked out" in message
        assert result["response_ui"]["fields"] == list(RDD_REVIEW_FIELD_GROUP)
        assert result["active_field"] == "document_status"

    @pytest.mark.asyncio
    async def test_the_guideline_link_is_still_asked_first(
        self, short_flow_on: None
    ) -> None:
        collected = _rdd_ready()
        collected.pop("guideLineLink")
        result = await missing_field_node(_state(collected))
        assert result["response_ui"]["field"] == "guideLineLink"

    @pytest.mark.asyncio
    async def test_the_upload_comes_before_the_outcome(
        self, short_flow_on: None
    ) -> None:
        """No meeting report yet, so its outcome cannot be asked about.

        The upload card itself needs a session and sr_id to build, which this
        bare state has not got; what matters here is that the group question
        does not jump the queue.
        """
        state = _state(_rdd_ready())
        state["documents"] = []
        result = await missing_field_node(state)
        assert "Report outcome and milestone dates" not in (
            result.get("response_message") or ""
        )
        assert result["response_ui"].get("fields") is None


class TestNarrowing:
    @pytest.mark.asyncio
    async def test_outcome_given_asks_only_for_the_dates(
        self, short_flow_on: None, no_platform_db: None
    ) -> None:
        result = await missing_field_node(
            _state(_rdd_ready(document_status="APPROVED"))
        )
        assert result["response_ui"]["fields"] == [
            "actual_handover_date",
            "fitout_start_date",
            "fitout_end_date",
        ]
        assert "**Approved**" in result["response_message"]

    @pytest.mark.asyncio
    async def test_one_date_left_asks_for_that_date_alone(
        self, short_flow_on: None, no_platform_db: None
    ) -> None:
        result = await missing_field_node(
            _state(
                _rdd_ready(
                    document_status="APPROVED_WITH_CHANGES",
                    actual_handover_date="2026-09-20",
                    fitout_start_date="2026-09-27",
                )
            )
        )
        assert result["response_ui"]["fields"] == ["fitout_end_date"]
        assert "When did fit-out actually finish?" in result["response_message"]
        assert "20 Sep 2026" in result["response_message"]


class TestSuggestions:
    @pytest.mark.asyncio
    async def test_nothing_to_suggest_without_an_expected_handover_date(self) -> None:
        assert await rdd.suggest_rdd_dates({"lease_id": 274251}) == {}

    @pytest.mark.asyncio
    async def test_the_handover_date_is_the_expected_one(
        self, no_platform_db: None
    ) -> None:
        """Anchored on this SR's own number, not on the lease start date."""
        suggested = await rdd.suggest_rdd_dates(
            {"expected_handover_date": "2026-09-10", "lease_id": 274251}
        )
        assert suggested == {"actual_handover_date": "2026-09-10"}

    @pytest.mark.asyncio
    async def test_a_lease_lookup_failure_still_offers_the_handover_date(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Boom:
            def __init__(self, *_a: Any, **_k: Any) -> None:
                pass

            async def fetch_all(self, *_a: Any, **_k: Any) -> list[dict]:
                raise RuntimeError("platform database unreachable")

            async def dispose(self) -> None:
                return None

        import app.indexer.readers.pg_reader as pg

        monkeypatch.setattr(pg, "CenomiPgReader", _Boom)
        suggested = await rdd.suggest_rdd_dates(
            {"expected_handover_date": "2026-09-10", "lease_id": 274251}
        )
        assert suggested == {"actual_handover_date": "2026-09-10"}


class TestChips:
    @pytest.mark.asyncio
    async def test_each_chip_carries_an_outcome_and_the_dates(self) -> None:
        async def _suggest(_collected: dict[str, Any]) -> dict[str, str]:
            return {
                "actual_handover_date": "2026-09-10",
                "fitout_start_date": "2026-09-30",
                "fitout_end_date": "2026-11-10",
            }

        import app.agents.services.rdd_suggestions_service as service

        original = service.suggest_rdd_dates
        service.suggest_rdd_dates = _suggest  # type: ignore[assignment]
        try:
            decorations = await service.build_rdd_decorations(
                {}, set(RDD_REVIEW_FIELD_GROUP)
            )
        finally:
            service.suggest_rdd_dates = original  # type: ignore[assignment]

        options = decorations["suggestions"]["options"]
        assert [o["label"] for o in options] == [
            "Approve · handover 10 Sep, fit-out 30 Sep → 10 Nov",
            "Approve with changes · handover 10 Sep, fit-out 30 Sep → 10 Nov",
        ]
        assert options[0]["value"] == (
            "Approved. handover 10/09/2026, fitout start 30/09/2026, "
            "fitout end 10/11/2026"
        )

    @pytest.mark.asyncio
    async def test_outcome_only_keeps_the_direct_set_chips(
        self, no_platform_db: None
    ) -> None:
        """Nothing to pack, so a tap resolves without an extraction round-trip."""
        decorations = await rdd.build_rdd_decorations({}, {"document_status"})
        options = decorations["suggestions"]["options"]
        assert [o["action"] for o in options] == [
            "select_rdd_status_approved",
            "select_rdd_status_approved_with_changes",
        ]

    @pytest.mark.asyncio
    async def test_no_chips_when_there_is_nothing_to_offer(
        self, no_platform_db: None
    ) -> None:
        assert await rdd.build_rdd_decorations({}, {"fitout_end_date"}) == {}

class TestWindowSanity:
    """A suggested window that ends before it starts is worse than none."""

    @staticmethod
    async def _suggest(monkeypatch: pytest.MonkeyPatch, row: dict[str, Any]) -> dict:
        class _Reader:
            def __init__(self, *_a: Any, **_k: Any) -> None:
                pass

            async def fetch_all(self, *_a: Any, **_k: Any) -> list[dict]:
                return [row]

            async def dispose(self) -> None:
                return None

        import app.indexer.readers.pg_reader as pg

        monkeypatch.setattr(pg, "CenomiPgReader", _Reader)
        from app.core.config import get_settings

        monkeypatch.setattr(
            get_settings(), "cenomi_database_url", "postgresql+asyncpg://x/y"
        )
        return await rdd.suggest_rdd_dates(
            {"expected_handover_date": "2026-09-17", "lease_id": 1}
        )

    @pytest.mark.asyncio
    async def test_an_end_that_predates_the_start_closes_on_the_start(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A lease's planned end can sit before the computed fit-out start."""
        suggested = await self._suggest(
            monkeypatch,
            {
                "fitout_start_date": date(2026, 10, 7),
                "fitout_end_date": date(2026, 9, 23),
                "free_period": 0,
            },
        )
        assert suggested["fitout_start_date"] == "2026-10-07"
        assert suggested["fitout_end_date"] == "2026-10-07"

    @pytest.mark.asyncio
    async def test_no_agreed_period_gives_a_same_day_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing to derive an end from — offer the start, not a blank."""
        suggested = await self._suggest(
            monkeypatch,
            {
                "fitout_start_date": date(2026, 10, 7),
                "fitout_end_date": None,
                "free_period": 0,
            },
        )
        assert suggested["fitout_end_date"] == "2026-10-07"

    @pytest.mark.asyncio
    async def test_a_same_day_window_passes_the_rdd_ordering_rule(self) -> None:
        """earlier <= later, so the reviewer can accept it and widen later."""
        from app.agents.services.validation_service import validate_rdd_date_order

        errors = [
            r
            for r in validate_rdd_date_order(
                {
                    "actual_handover_date": "2026-09-17",
                    "fitout_start_date": "2026-10-07",
                    "fitout_end_date": "2026-10-07",
                    "trading_date": "2026-10-15",
                }
            )
            if r["status"] == "FAILED"
        ]
        assert errors == []

    @pytest.mark.asyncio
    async def test_keeps_a_window_that_makes_sense(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        suggested = await self._suggest(
            monkeypatch,
            {
                "fitout_start_date": date(2026, 10, 7),
                "fitout_end_date": date(2026, 11, 20),
                "free_period": 61,
            },
        )
        assert suggested["fitout_end_date"] == "2026-11-20"


def _card_after_meeting_report() -> dict[str, Any]:
    """The card sr_status_sync builds once the Meeting Report is uploaded."""
    return {
        "documents": [
            {"key": "DR_SR_HANDOVER_REPORT", "status": "uploaded"},
            {"key": "SR_HANDOVER_OTHER", "status": "missing"},
        ],
        "uploadContext": {
            "allowedDocumentTypes": ["DR_SR_HANDOVER_REPORT", "SR_HANDOVER_OTHER"],
        },
    }


class TestTheUploadCardSurvives:
    """Live: the rebuilt card (Rejected already gone) was wiped by the very
    question that answers the upload, so the client kept offering Rejected."""

    @pytest.mark.asyncio
    async def test_combined_question_carries_the_card(
        self, short_flow_on: None, no_platform_db: None
    ) -> None:
        state = _state(_rdd_ready())
        state["response_decorations"] = {
            "document_requirement": _card_after_meeting_report(),
            "suggestions": {"kind": "labeled_options", "options": []},
        }
        result = await missing_field_node(state)

        assert "Report outcome and milestone dates" in result["response_message"]
        card = result["response_decorations"]["document_requirement"]
        assert [d["key"] for d in card["documents"]] == [
            "DR_SR_HANDOVER_REPORT",
            "SR_HANDOVER_OTHER",
        ]

    @pytest.mark.asyncio
    async def test_single_outcome_question_carries_the_card(self) -> None:
        state = _state(_rdd_ready())
        state["response_decorations"] = {
            "document_requirement": _card_after_meeting_report(),
        }
        result = await missing_field_node(state)

        assert result["response_ui"]["field"] == "document_status"
        # Either the node carried it, or it set no decorations and the card
        # stays in state untouched — both reach the client.
        decorations = result.get("response_decorations")
        if decorations is not None:
            assert "document_requirement" in decorations
