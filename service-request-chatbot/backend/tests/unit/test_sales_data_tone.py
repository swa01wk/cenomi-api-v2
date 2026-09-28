"""Sales Data Agent replies: one professional voice, never LLM-rewritten."""

from __future__ import annotations

import glob
import re
from unittest.mock import patch

import pytest

_CASUAL = re.compile(
    r"good news|great news|great —|awesome|no problem|got it|just tell me|"
    r"you're all set|yay|woohoo",
    re.I,
)
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿ℹ]")


def test_sales_data_nodes_contain_no_casual_phrases_or_emoji() -> None:
    offenders = []
    for path in glob.glob("app/agents/graph/nodes/sales_data/*.py"):
        for no, line in enumerate(open(path, encoding="utf-8"), 1):
            code = line.split("#", 1)[0]
            if (_CASUAL.search(code) or _EMOJI.search(code)) and '"' in code:
                offenders.append(f"{path}:{no}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


@pytest.mark.asyncio
async def test_approval_notification_reply_is_professional() -> None:
    from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
        sales_data_entry_node,
    )

    result = await sales_data_entry_node({
        "intent": "ACKNOWLEDGE_APPROVAL",
        "user_message": "",
        "backend_refs": {
            "notification_context": {"lease_code": "t0057323", "period_hint": "August 2026"},
        },
        "collected_data": {},
        "corrected_fields": {},
    })
    message = result["response_message"]
    assert message.startswith("Your sales data for **t0057323**")
    assert "No further action is required." in message
    assert result["response_ui"]["verbatim"] is True


@pytest.mark.asyncio
async def test_sales_agent_reply_skips_llm_rewrite() -> None:
    from app.agents.graph.nodes.shared.response_generation_node import (
        response_generation_node,
    )

    with patch(
        "app.agents.graph.nodes.shared.response_generation_node.get_llm_gateway",
        side_effect=AssertionError("LLM must not be called"),
        create=True,
    ):
        result = await response_generation_node({
            "active_agent": "sales_data_agent",
            "response_message": "Lease **t0057323** noted. Which month is this sales data for?",
            "response_ui": {"type": "text_prompt"},
            "status": "IN_PROGRESS",
        })
    assert result["response_message"].startswith("Lease **t0057323** noted.")
    assert result["status"] == "WAITING_FOR_USER"


@pytest.mark.parametrize(
    "message, blocked",
    [
        ("i want to create handover sr", True),
        ("raise a work permit", True),
        ("what's the status of my service request", False),
        ("check my handover sr", False),
        ("submit sales for aug", False),
    ],
)
def test_sr_creation_request_from_sales_only_role_gets_a_clear_answer(message, blocked) -> None:
    from app.agents.graph.nodes.supervisor_node import (
        _requested_sr_kind,
        _sr_create_not_permitted_patch,
    )

    kind = _requested_sr_kind(message)
    assert bool(kind) is blocked
    if kind:
        reply = _sr_create_not_permitted_patch(kind)["response_message"]
        assert reply.startswith("Creating ")
        assert "sales data action" not in reply


@pytest.mark.asyncio
async def test_sales_agent_never_repeats_the_identical_reply() -> None:
    from app.agents.graph.nodes.shared.response_generation_node import (
        response_generation_node,
    )

    prompt = "Please attach the corrected sales data file for lease t0106013."
    result = await response_generation_node({
        "active_agent": "sales_data_agent",
        "user_message": "no, i want to upload sales data",
        "response_message": prompt,
        "response_ui": {"type": "supporting_doc_prompt"},
        "status": "IN_PROGRESS",
        "conversation_history": [
            {"role": "user", "content": "i want to upload again"},
            {"role": "assistant", "content": prompt},
        ],
    })
    assert result["response_message"] != prompt
    assert "upload sales data" in result["response_message"]
    # The card (and its attach control) is kept, only the text changes.
    assert result["response_ui"]["type"] == "supporting_doc_prompt"
