"""A document card must carry the id of what it shows, or Delete cannot work.

Reported live on the optional-upload card: the user deleted the uploaded
document, the row cleared, and a second later it was back.

The card's rows carried only key/label/required/status. The client reads
``documentId ?? document_id``, found neither, and took its local-only branch —
clearing the row on screen without calling the backend. No DELETE was ever
sent (zero across a whole session of trying), the draft kept the document, and
the next turn re-rendered it as uploaded.

Both card builders now carry the id, and the file name with it.
"""

from __future__ import annotations

from typing import Any

from app.agents.services.review_decorations_service import _uploaded_doc_row

_OTHER = "SR_HANDOVER_OTHER"


def _doc(**over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "document_type_id": _OTHER,
        "document_id": "7f7029f1-a451-4307-9469-68df7599b43c",
        "file_name": "walkthrough.pdf",
    }
    doc.update(over)
    return doc


class TestTheRowIdentifiesWhatItShows:
    def test_an_uploaded_document_contributes_its_id_and_name(self) -> None:
        assert _uploaded_doc_row([_doc()], _OTHER) == {
            "document_id": "7f7029f1-a451-4307-9469-68df7599b43c",
            "file_name": "walkthrough.pdf",
        }

    def test_the_platform_spelling_is_accepted(self) -> None:
        # The upload response says "filename"; the draft says "file_name"; a
        # row patched from the client says "fileName". Reading only two of the
        # three sent the card out with an id and no name, and the client then
        # overwrote the name it already had — the tick and the file name
        # disappeared from the chat card and the form together.
        row = _uploaded_doc_row(
            [{"document_type_id": _OTHER, "document_id": "1", "filename": "p.pdf"}],
            _OTHER,
        )
        assert row["file_name"] == "p.pdf"

    def test_an_explicit_none_falls_through_to_the_other_spelling(self) -> None:
        # Exactly what the live trace showed: file_name present but None.
        row = _uploaded_doc_row(
            [
                {
                    "document_type_id": _OTHER,
                    "document_id": "1",
                    "file_name": None,
                    "filename": "p.pdf",
                }
            ],
            _OTHER,
        )
        assert row["file_name"] == "p.pdf"

    def test_the_camel_case_file_name_is_accepted_too(self) -> None:
        row = _uploaded_doc_row(
            [_doc(file_name=None, fileName="camel.pdf")], _OTHER
        )
        assert row["file_name"] == "camel.pdf"

    def test_the_legacy_document_type_key_is_matched(self) -> None:
        doc = _doc()
        doc["document_type"] = doc.pop("document_type_id")
        assert _uploaded_doc_row([doc], _OTHER)["document_id"]

    def test_a_name_is_optional(self) -> None:
        assert _uploaded_doc_row([_doc(file_name=None)], _OTHER) == {
            "document_id": "7f7029f1-a451-4307-9469-68df7599b43c"
        }


class TestNothingIsInvented:
    def test_another_type_contributes_nothing(self) -> None:
        assert _uploaded_doc_row([_doc()], "DR_SR_HANDOVER_REPORT") == {}

    def test_a_row_without_an_id_contributes_nothing(self) -> None:
        # Merging {"document_id": ""} would make the client believe it can
        # delete something it cannot name.
        assert _uploaded_doc_row([{"document_type_id": _OTHER}], _OTHER) == {}

    def test_no_documents_at_all(self) -> None:
        assert _uploaded_doc_row([], _OTHER) == {}

    def test_the_first_uploaded_match_wins(self) -> None:
        rows = [
            {"document_type_id": _OTHER},
            _doc(document_id="second"),
        ]
        assert _uploaded_doc_row(rows, _OTHER)["document_id"] == "second"


class TestEveryCardBuilderCarriesIt:
    """Three separate builders emit this card; all three must identify the row.

    The first fix reached two of them and the reported symptom stayed, because
    the card the Mall Manager actually sees at the pre-submission gate is built
    in confirmation_node — recognisable in the payload by the
    ``defaultDocumentType`` its uploadContext carries, which neither of the
    others emits.
    """

    def test_the_pre_submission_gate_card_identifies_its_document(self) -> None:
        import inspect

        from app.agents.graph.nodes.handover import confirmation_node

        source = inspect.getsource(confirmation_node)
        assert "_uploaded_doc_row(documents" in source, (
            "the MM optional-upload gate card must merge the document row, or "
            "Delete cannot fire and the file name disappears on re-render"
        )

    def test_the_decoration_builders_identify_theirs(self) -> None:
        import inspect

        from app.agents.services import review_decorations_service

        source = inspect.getsource(review_decorations_service)
        assert source.count("**_uploaded_doc_row(documents") == 2
