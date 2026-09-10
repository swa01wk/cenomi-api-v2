"""When the repeat guard fires, the user must still get a real question.

Reported live on a Fitout End Confirmation (SR on lease t0112177). Two form
edits in a row landed on ``fitout_complete_done``, and the second turn came
back as:

    Thanks — that's been updated. Please provide the fitout complete done.

The collecting node had already written the right question — "Has the fitout
been completed? Please reply yes or no — if no, you can also share revised
planned dates." — with its Yes / No chips attached. The guard that rejects an
LLM reply for re-asking the previous question then threw that away and built a
sentence out of the field name instead.

Two things were wrong:

* the three milestone fields were missing from ``FIELD_LABELS``, so
  ``_mentions_field`` fell back to the raw key and demanded the word "done",
  which none of the real questions contain — a correct reply was judged a
  repeat; and
* the guard's replacement ignored the question sitting in the UI payload.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.graph.nodes.shared.response_generation_node import (
    _mentions_field,
    response_generation_node,
)

_QUESTION = (
    "Has the fitout been completed? Please reply yes or no — "
    "if no, you can also share revised planned dates."
)


def _gateway(message: str) -> MagicMock:
    gateway = MagicMock()
    gateway.complete_json = AsyncMock(return_value=({"message": message}, 1, 1, 1))
    return gateway


class TestMilestoneQuestionsAreRecognised:
    """A reply that asks the milestone question must not read as a repeat."""

    @pytest.mark.parametrize(
        ("field", "question"),
        [
            ("fitout_start_done", "Has the fitout started?"),
            ("fitout_complete_done", "Has the fitout been completed?"),
            ("trading_done", "Has trading started?"),
            (
                "planned_fitout_complete_date",
                "Please share the revised planned fitout completion date.",
            ),
            ("delay_reason", "What is the reason for the delay?"),
        ],
    )
    def test_the_field_is_recognised_in_its_own_question(
        self, field: str, question: str
    ) -> None:
        assert _mentions_field(question, field) is True

    def test_a_sibling_milestone_question_is_not_mistaken_for_this_one(self) -> None:
        # "started" must not satisfy "completed" — that would let the guard
        # pass a reply that really did re-ask the wrong milestone.
        assert _mentions_field("Has the fitout started?", "fitout_complete_done") is False

    def test_an_unrelated_question_is_not_recognised(self) -> None:
        assert (
            _mentions_field("Please share the unit readiness date.", "fitout_complete_done")
            is False
        )


class TestTheGuardFallsBackToTheNodesQuestion:
    @pytest.mark.asyncio
    async def test_the_field_name_sentence_never_reaches_the_user(self) -> None:
        repeat = "Could you confirm the milestone once more?"
        with patch(
            "app.agents.graph.nodes.shared.response_generation_node.get_default_gateway",
            return_value=_gateway(repeat),
        ):
            result = await response_generation_node(  # type: ignore[arg-type]
                {
                    "response_ui": {
                        "type": "text_question",
                        "field": "fitout_complete_done",
                        "message": _QUESTION,
                    },
                    "response_message": _QUESTION,
                    "collected_data": {},
                    "conversation_history": [
                        {"role": "assistant", "content": repeat},
                        {"role": "user", "content": ""},
                    ],
                }
            )

        assert "fitout complete done" not in result["response_message"]
        assert result["response_message"] == _QUESTION
        assert result["response_ui"]["message"] == _QUESTION

    @pytest.mark.asyncio
    async def test_the_label_sentence_is_still_used_when_there_is_no_question(
        self,
    ) -> None:
        # No UI message to fall back on: the label sentence is the last resort,
        # and it must now read as English rather than as a field name.
        repeat = "Could you confirm that once more?"
        with patch(
            "app.agents.graph.nodes.shared.response_generation_node.get_default_gateway",
            return_value=_gateway(repeat),
        ):
            result = await response_generation_node(  # type: ignore[arg-type]
                {
                    "response_ui": {
                        "type": "text_question",
                        "field": "fitout_complete_done",
                    },
                    "response_message": "hint",
                    "collected_data": {},
                    "conversation_history": [
                        {"role": "assistant", "content": repeat},
                        {"role": "user", "content": ""},
                    ],
                }
            )

        assert result["response_message"] == (
            "Thanks — that's been updated. "
            "Please provide the fitout completed (yes or no)."
        )
