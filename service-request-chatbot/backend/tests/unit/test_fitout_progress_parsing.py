"""Unit tests for Fitout Progress percentage parsing (phase 1).

``parse_percentage`` is what lets the entry node bind a chip tap or a typed
number to the field without an LLM round-trip, so its accept/reject boundary is
the whole behaviour: anything it accepts is recorded on the lease, and anything
it refuses must fall through to extraction or to a validation error.

Coverage
--------
TestAcceptedForms   — the shapes a user types, and chip values
TestRejectedForms   — out of range, and numbers that are not percentages
TestFormatting      — the string shape the platform payload expects
TestAnswerParsing   — the looser parse used when the percentage is the question
"""

from __future__ import annotations

import pytest

from app.agents.services.fitout_progress_suggestions_service import (
    format_percentage,
    parse_percentage,
    parse_percentage_answer,
)


# ---------------------------------------------------------------------------
# TestAcceptedForms
# ---------------------------------------------------------------------------


class TestAcceptedForms:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("82", 82.0),
            ("0", 0.0),
            ("100", 100.0),
            ("82%", 82.0),
            (" 82 % ", 82.0),
            ("82 percent", 82.0),
            ("82 Percent", 82.0),
            ("82percent", 82.0),
            ("82 pct", 82.0),
            ("82 percentage", 82.0),
        ],
    )
    def test_plain_numbers_and_percent_signs(self, raw: str, expected: float) -> None:
        assert parse_percentage(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("82.5", 82.5),
            ("82,5", 82.5),
            ("0.5", 0.5),
            ("99.99", 99.99),
        ],
    )
    def test_decimals(self, raw: str, expected: float) -> None:
        # The platform columns are NUMERIC(5,2), so two decimals survive.
        assert parse_percentage(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("complete", 100.0),
            ("Completed", 100.0),
            ("done", 100.0),
            ("done.", 100.0),
            ("finished", 100.0),
            ("hundred", 100.0),
            ("not started", 0.0),
            ("none", 0.0),
            ("zero", 0.0),
            ("half", 50.0),
            ("halfway", 50.0),
            ("quarter", 25.0),
            ("three quarters", 75.0),
        ],
    )
    def test_the_short_word_list(self, raw: str, expected: float) -> None:
        assert parse_percentage(raw) == expected

    @pytest.mark.parametrize("raw", [82, 82.5, 0, 100])
    def test_numeric_types(self, raw: float) -> None:
        assert parse_percentage(raw) == float(raw)

    def test_chip_values_round_trip(self) -> None:
        # Every chip the service offers must parse back to its own number.
        for value in ("0", "25", "50", "75", "82", "82.5", "100"):
            assert format_percentage(parse_percentage(value)) == value


# ---------------------------------------------------------------------------
# TestRejectedForms
# ---------------------------------------------------------------------------


class TestRejectedForms:
    @pytest.mark.parametrize("raw", ["101", "120", "-5", "1000", "100.01"])
    def test_out_of_range_is_refused_not_clamped(self, raw: str) -> None:
        # Refused rather than clamped: the user gets the question again instead
        # of a silently corrected number written to the lease.
        assert parse_percentage(raw) is None

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            None,
            "eighty-ish",
            "most of it",
            "nearly there",
            "a bit more than half",
        ],
    )
    def test_prose_falls_through_to_extraction(self, raw: object) -> None:
        assert parse_percentage(raw) is None

    @pytest.mark.parametrize(
        "raw",
        [
            "t0105712",           # lease code
            "2026-07-15",         # date
            "15/07/2026",         # date
            "8517",               # SR id
            "82 sqm",             # area
            "82 units",
        ],
    )
    def test_numbers_that_are_not_percentages(self, raw: str) -> None:
        assert parse_percentage(raw) is None

    def test_booleans_are_not_percentages(self) -> None:
        # bool is an int subclass in Python; True must not become 1%.
        assert parse_percentage(True) is None
        assert parse_percentage(False) is None

    def test_three_decimals_are_refused(self) -> None:
        assert parse_percentage("82.555") is None


# ---------------------------------------------------------------------------
# TestFormatting
# ---------------------------------------------------------------------------


class TestFormatting:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (82, "82"),
            (82.0, "82"),
            (82.5, "82.5"),
            (0, "0"),
            (100.0, "100"),
            (82.25, "82.25"),
            (None, ""),
        ],
    )
    def test_payload_string_shape(self, value: float | None, expected: str) -> None:
        assert format_percentage(value) == expected


# ---------------------------------------------------------------------------
# TestAnswerParsing
# ---------------------------------------------------------------------------


class TestAnswerParsing:
    """``parse_percentage_answer`` — used where the percentage is the open question."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("90", 90.0),
            ("90%", 90.0),
            ("make it 90", 90.0),
            ("set it to 90", 90.0),
            ("~82.5", 82.5),
            ("about 60 percent", 60.0),
            ("90.", 90.0),
            ("it's 75%!", 75.0),
            ("complete", 100.0),
        ],
    )
    def test_accepts_a_message_that_is_only_an_answer(
        self, raw: str, expected: float
    ) -> None:
        assert parse_percentage_answer(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "check SR 8517 and fix it",   # id, plus words after
            "between 60 and 70",          # two numbers
            "t0105712",                   # lease code
            "2026-07-15",                 # date
            "90 but only in the back of house",  # words after the number
            "120",                        # out of range
            "",
            None,
        ],
    )
    def test_refuses_anything_else(self, raw: object) -> None:
        assert parse_percentage_answer(raw) is None

    def test_never_disagrees_with_the_strict_parser(self) -> None:
        # Whatever the strict parser accepts, the loose one must read the same.
        for raw in ("0", "25", "82.5", "100", "82%", "done"):
            assert parse_percentage_answer(raw) == parse_percentage(raw)
