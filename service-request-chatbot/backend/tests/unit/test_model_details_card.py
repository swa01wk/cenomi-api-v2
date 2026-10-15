"""Unit tests — the response LLM chooses when a reply is a details_card.

Reported live: "Show me the list of worker details" got an LLM sentence that
listed every stored worker field, including the Iqama upload's document id
(a UUID). The model now sees documents only as ``doc_N`` handles and may
return an optional ``card``; the backend resolves the handles to real ids.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.graph.nodes.shared.response_generation_node import (
    _details_card,
    response_generation_node,
)
from app.agents.prompts.response_generation_prompt import (
    build_document_refs,
    build_response_generation_context,
)

_DOC_ID = "72876515-162b-4309-89c5-56fca7cb80aa"
_OTHER_DOC_ID = "0b1c2d3e-4f50-4617-8899-aabbccddeeff"

_COLD_WORKERS = [
    {
        "full_name": "Kishan Sah",
        "designation": "Electrician",
        "company_name": "Gulf Facility Services",
        "phone": "847874897398",
        "country_code": "966",
        "iqama_number": "4232879237",
        "document_id": _DOC_ID,
    }
]

_DOCUMENTS = [
    {
        "document_id": _DOC_ID,
        "document_type_id": "WP_COLD_WORKER_IQAMA_0",
        "file_name": "Document2 (2).pdf",
    }
]


async def _run(state: dict[str, Any], parsed: dict[str, Any]) -> tuple[dict, MagicMock]:
    gateway = MagicMock()
    gateway.complete_json = AsyncMock(return_value=(parsed, 1, 1, 1))
    with patch(
        "app.agents.graph.nodes.shared.response_generation_node.get_default_gateway",
        return_value=gateway,
    ):
        result = await response_generation_node(  # type: ignore[arg-type]
            {
                "user_message": "",
                "response_ui": None,
                "response_message": None,
                "collected_data": {},
                "conversation_history": [],
                **state,
            }
        )
    return result, gateway


class TestModelChosenCard:
    @pytest.mark.asyncio
    async def test_worker_card_resolves_the_document_handle(self) -> None:
        result, gateway = await _run(
            {
                "user_message": "Show me the list of worker details",
                "collected_data": {"workers": _COLD_WORKERS},
                "documents": _DOCUMENTS,
            },
            {
                "message": "Here are the workers on this request.",
                "card": {
                    "title": "Workers (1)",
                    "fields": [
                        {"label": "Worker", "value": "Kishan Sah"},
                        {"label": "Iqama Number", "value": "4232879237"},
                    ],
                    "files": [{"label": "Iqama — Kishan Sah", "ref": "doc_1"}],
                },
            },
        )

        ui = result["response_ui"]
        assert ui["type"] == "details_card"
        assert ui["title"] == "Workers (1)"
        assert ui["message"] == "Here are the workers on this request."
        assert ui["fields"][0] == {"label": "Worker", "value": "Kishan Sah"}
        assert ui["files"] == [
            {
                "label": "Iqama — Kishan Sah",
                "fileName": "Document2 (2).pdf",
                "documentId": _DOC_ID,
            }
        ]
        assert _DOC_ID not in result["response_message"]
        prompt = gateway.complete_json.call_args.kwargs["user_message"]
        assert _DOC_ID not in prompt
        assert "doc_1: Document2 (2).pdf" in prompt

    @pytest.mark.asyncio
    async def test_any_other_details_work_the_same_way(self) -> None:
        result, _ = await _run(
            {
                "user_message": "What safety measures are on this permit?",
                "collected_data": {"measureofsafety": ["Fire extinguisher", "Goggles"]},
            },
            {
                "message": "Here are the safety measures.",
                "card": {
                    "title": "Safety details",
                    "fields": [
                        {"label": "Measure", "value": "Fire extinguisher"},
                        {"label": "Measure", "value": "Goggles"},
                    ],
                },
            },
        )

        ui = result["response_ui"]
        assert ui["type"] == "details_card"
        assert ui["title"] == "Safety details"
        assert [f["value"] for f in ui["fields"]] == ["Fire extinguisher", "Goggles"]
        assert "files" not in ui

    @pytest.mark.asyncio
    async def test_plain_reply_adds_no_card(self) -> None:
        result, gateway = await _run(
            {
                "user_message": "add worker Ahmed",
                "response_message": "Noted.",
                "collected_data": {"workers": _COLD_WORKERS},
            },
            {"message": "Got it."},
        )

        gateway.complete_json.assert_awaited_once()
        assert result["response_message"] == "Got it."
        assert "response_ui" not in result

    @pytest.mark.asyncio
    async def test_a_planned_card_wins_over_the_model_card(self) -> None:
        planned = {"type": "confirmation_card", "fields": [], "message": "Confirm?"}
        result, _ = await _run(
            {
                "user_message": "show workers",
                "response_ui": planned,
                "response_message": "Please confirm.",
                "collected_data": {"workers": _COLD_WORKERS},
            },
            {
                "message": "Please confirm the request.",
                "card": {"title": "Workers", "fields": [{"label": "Worker", "value": "X"}]},
            },
        )

        assert result["response_ui"]["type"] == "confirmation_card"
        assert "title" not in result["response_ui"]


class TestCardCleaning:
    _REFS = {_DOC_ID: "doc_1"}
    _NAMES = {_DOC_ID: "iqama.pdf"}

    def test_unknown_refs_ids_and_empty_rows_are_dropped(self) -> None:
        card = _details_card(
            {
                "title": "Workers",
                "fields": [
                    {"label": "Worker", "value": "Ali"},
                    {"label": "Document", "value": _DOC_ID},
                    {"label": "Handle", "value": "doc_1"},
                    {"label": "", "value": "orphan"},
                    {"label": "Empty", "value": "  "},
                    "not a row",
                ],
                "files": [
                    {"label": "Iqama", "ref": "doc_1"},
                    {"label": "Again", "ref": "doc_1"},
                    {"label": "Ghost", "ref": "doc_9"},
                ],
            },
            self._REFS,
            self._NAMES,
        )

        assert card is not None
        assert card["fields"] == [{"label": "Worker", "value": "Ali"}]
        assert card["files"] == [
            {"label": "Iqama", "fileName": "iqama.pdf", "documentId": _DOC_ID}
        ]

    def test_nothing_usable_means_no_card(self) -> None:
        assert _details_card(None, self._REFS, self._NAMES) is None
        assert _details_card("Workers", self._REFS, self._NAMES) is None
        assert (
            _details_card(
                {"title": "Empty", "fields": [{"label": "Id", "value": _DOC_ID}]},
                self._REFS,
                self._NAMES,
            )
            is None
        )


class TestPromptDocumentHandles:
    def test_documents_are_listed_by_handle_without_ids(self) -> None:
        refs = build_document_refs(_DOCUMENTS, {"workers": _COLD_WORKERS})
        content = build_response_generation_context(
            user_message="what's next?",
            response_intent="Continue.",
            workflow_stage="CREATE_SR",
            intent=None,
            collected_data={"workers": _COLD_WORKERS},
            missing_fields=[],
            validation_errors=[],
            confirmation_status=None,
            response_ui_type=None,
            conversation_history=[],
            doc_refs=refs,
            documents=_DOCUMENTS,
        )

        assert _DOC_ID not in content
        assert "## AVAILABLE DOCUMENTS" in content
        assert "- doc_1: Document2 (2).pdf" in content
        assert "full_name: Kishan Sah" in content
        assert "document: doc_1" in content

    def test_handles_follow_documents_then_collected_data(self) -> None:
        refs = build_document_refs(
            _DOCUMENTS,
            {"workers": [*_COLD_WORKERS, {"full_name": "Ali", "document_id": _OTHER_DOC_ID}]},
        )

        assert refs == {_DOC_ID: "doc_1", _OTHER_DOC_ID: "doc_2"}
