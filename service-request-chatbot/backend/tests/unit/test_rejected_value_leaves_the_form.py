"""A value the assistant rejects must not stay in the form.

Reported on the RDD flow: entering ``abcd@gmail.com`` as the guideline link
was correctly refused — the assistant asked for a URL starting with http:// or
https:// — but the address appeared in the form anyway. The user saw the AI
reject a value and the form display it at the same time.

``validation_node`` already dropped malformed dates for exactly this reason;
the URL check was simply not in the set. Both mean "this is not a value of
this kind", so the field is cleared and re-asked.

Required / ordering / enum failures are deliberately left alone: those are
about what a value *means*, not what it *is*, and the collection flow handles
them without throwing the user's input away.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.shared.validation_node import validation_node


async def _kept(collected: dict[str, Any], field: str) -> Any:
    state: dict[str, Any] = {
        "workflow_stage": "RDD_REVIEW",
        "active_agent": "rdd_handover_agent",
        "sub_category": "HANDOVER",
        "collected_data": dict(collected),
        "documents": [],
    }
    result = await validation_node(state)  # type: ignore[arg-type]
    merged = result.get("collected_data", state["collected_data"])
    return merged.get(field, "(cleared)")


def _rdd(**over: Any) -> dict[str, Any]:
    draft: dict[str, Any] = {
        "lease_code": "t0110565",
        "document_status": "APPROVED",
        "actual_handover_date": "2026-09-10",
        "fitout_start_date": "2026-09-12",
        "fitout_end_date": "2026-09-20",
    }
    draft.update(over)
    return draft


class TestAMalformedValueIsCleared:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "link",
        ["abcd@gmail.com", "www.example.com", "ftp://files.example/doc", "just words"],
    )
    async def test_a_link_that_is_not_a_url_never_reaches_the_form(
        self, link: str
    ) -> None:
        assert await _kept(_rdd(guideLineLink=link), "guideLineLink") == "(cleared)"

    @pytest.mark.asyncio
    async def test_a_malformed_date_is_still_cleared(self) -> None:
        # The behaviour the URL check was added alongside.
        kept = await _kept(_rdd(actual_handover_date="t0110565"), "actual_handover_date")
        assert kept == "(cleared)"


class TestAValidValueSurvives:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "link",
        [
            "https://guidelines.example/handover.pdf",
            "http://intranet.local/doc",
            "HTTPS://Guidelines.example/x",
        ],
    )
    async def test_a_real_url_is_kept(self, link: str) -> None:
        assert await _kept(_rdd(guideLineLink=link), "guideLineLink") == link

    @pytest.mark.asyncio
    async def test_a_merely_missing_field_is_not_cleared_from_others(self) -> None:
        # A required-field failure must not throw away what the user did give.
        kept = await _kept(
            _rdd(guideLineLink="https://ok.example", document_status=""),
            "guideLineLink",
        )
        assert kept == "https://ok.example"
