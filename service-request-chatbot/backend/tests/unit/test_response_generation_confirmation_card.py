"""Unit tests — a confirmation card's own wording must reach the user.

Reported live on RDD review of SR 320966. The user changed the fitout end date
from 24/08/2026 to 25/08/2026 and the backend did exactly the right thing — the
observability trace shows ``merge_state`` writing
``fitout_end_date: '2026-08-24' -> '2026-08-25'`` and ``trading_date`` being
recomputed — but the sentence above the card read:

    "The fitout end date is currently set to 24/08/2026. Please confirm the new
     fitout end date you'd like to set."

quoting the value that had just been replaced and re-asking a question already
answered, while the card below it showed the new one. It reads as a loop even
though nothing was lost.

Cause: the confirmation nodes author their card text and set
``response_message = None``. With no hint to rewrite, response_generation's LLM
improvised from conversation history, where the old value still appeared.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.graph.nodes.shared.response_generation_node import (
    response_generation_node,
)

_CARD_TEXT = "All required details have been collected. Please review and confirm."


def _card(**overrides: Any) -> dict[str, Any]:
    card: dict[str, Any] = {
        "type": "confirmation_card",
        "stage": "RDD_REVIEW",
        "message": _CARD_TEXT,
        "fields": [
            {"key": "fitout_end_date", "label": "Fitout End Date", "value": "25/08/2026"}
        ],
    }
    card.update(overrides)
    return card


def _gateway(message: str) -> MagicMock:
    gateway = MagicMock()
    gateway.complete_json = AsyncMock(return_value=({"message": message}, 1, 1, 1))
    return gateway


class TestConfirmationCardKeepsItsWording:
    @pytest.mark.asyncio
    async def test_llm_is_not_consulted_for_a_confirmation_card(self) -> None:
        gateway = _gateway("anything at all")
        with patch(
            "app.agents.graph.nodes.shared.response_generation_node.get_default_gateway",
            return_value=gateway,
        ):
            result = await response_generation_node(  # type: ignore[arg-type]
                {
                    "response_ui": _card(),
                    "response_message": None,
                    "collected_data": {"fitout_end_date": "2026-08-25"},
                    "conversation_history": [],
                }
            )

        gateway.complete_json.assert_not_called()
        assert result["response_message"] == _CARD_TEXT
        assert result["response_ui"]["message"] == _CARD_TEXT

    @pytest.mark.asyncio
    async def test_the_reported_stale_sentence_cannot_reach_the_user(self) -> None:
        stale = (
            "The fitout end date is currently set to 24/08/2026. "
            "Please confirm the new fitout end date you'd like to set."
        )
        with patch(
            "app.agents.graph.nodes.shared.response_generation_node.get_default_gateway",
            return_value=_gateway(stale),
        ):
            result = await response_generation_node(  # type: ignore[arg-type]
                {
                    "response_ui": _card(),
                    "response_message": None,
                    "collected_data": {"fitout_end_date": "2026-08-25"},
                    "conversation_history": [
                        {
                            "role": "assistant",
                            "content": "Current Fitout End Date: 24/08/2026. "
                            "What would you like to change it to?",
                        },
                        {"role": "user", "content": "25/08/2026"},
                    ],
                }
            )

        assert result["response_message"] == _CARD_TEXT
        assert "24/08/2026" not in result["response_message"]

    @pytest.mark.asyncio
    async def test_waiting_status_is_still_set(self) -> None:
        with patch(
            "app.agents.graph.nodes.shared.response_generation_node.get_default_gateway",
            return_value=_gateway("x"),
        ):
            result = await response_generation_node(  # type: ignore[arg-type]
                {"response_ui": _card(), "response_message": None}
            )
        assert result["status"] == "WAITING_FOR_USER"

    @pytest.mark.asyncio
    async def test_a_node_supplied_hint_still_gets_rewritten(self) -> None:
        """When a node DOES provide response_message it wants the LLM's polish —
        only the message-less card short-circuits."""
        gateway = _gateway("Great — everything is collected, please review.")
        with patch(
            "app.agents.graph.nodes.shared.response_generation_node.get_default_gateway",
            return_value=gateway,
        ):
            result = await response_generation_node(  # type: ignore[arg-type]
                {
                    "response_ui": _card(),
                    "response_message": "Tell the user the draft is ready.",
                    "collected_data": {},
                    "conversation_history": [],
                }
            )

        gateway.complete_json.assert_called_once()
        assert result["response_message"] == "Great — everything is collected, please review."

    @pytest.mark.asyncio
    async def test_other_card_types_still_get_the_rewrite(self) -> None:
        """The upload gate's rewrite was a deliberate earlier fix — untouched."""
        gateway = _gateway("Please attach the handover report when you can.")
        upload_gate = {
            "type": "document_requirement",
            "message": "Upload the Handover Meeting Report.",
            "documents": [],
        }
        with patch(
            "app.agents.graph.nodes.shared.response_generation_node.get_default_gateway",
            return_value=gateway,
        ):
            result = await response_generation_node(  # type: ignore[arg-type]
                {
                    "response_ui": upload_gate,
                    "response_message": None,
                    "collected_data": {},
                    "conversation_history": [],
                }
            )

        gateway.complete_json.assert_called_once()
        assert result["response_message"] == "Please attach the handover report when you can."
