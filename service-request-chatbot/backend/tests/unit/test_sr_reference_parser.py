"""Unit tests for sr_reference_parser."""

from __future__ import annotations

from app.agents.services.sr_reference_parser import parse_sr_reference


class TestParseSrReferenceExplicit:
    def test_ref_no_colon(self) -> None:
        assert parse_sr_reference("Ref. No:11711") == "11711"

    def test_open_sr_phrase(self) -> None:
        assert parse_sr_reference("Open SR 11711") == "11711"

    def test_ref_no_with_upload_context(self) -> None:
        msg = "Ref. No:11711, upload document for this handover SR"
        assert parse_sr_reference(msg) == "11711"

    def test_sr_prefix(self) -> None:
        assert parse_sr_reference("Upload document for SR 11711") == "11711"

    def test_service_request_phrase(self) -> None:
        assert parse_sr_reference("check service request 11494") == "11494"

    def test_handover_sr(self) -> None:
        assert parse_sr_reference("handover SR no: 11215") == "11215"


class TestParseSrReferenceStandalone:
    def test_standalone_disabled_by_default(self) -> None:
        assert parse_sr_reference("11711") is None

    def test_standalone_when_allowed(self) -> None:
        assert parse_sr_reference("11711", allow_standalone=True) == "11711"

    def test_standalone_requires_check_intent_gate_in_supervisor(self) -> None:
        """Parser alone does not block standalone — supervisor gates allow_standalone."""
        assert parse_sr_reference("999", allow_standalone=False) is None


class TestParseSrReferenceNegative:
    def test_empty_message(self) -> None:
        assert parse_sr_reference("") is None

    def test_no_digits(self) -> None:
        assert parse_sr_reference("create handover please") is None
