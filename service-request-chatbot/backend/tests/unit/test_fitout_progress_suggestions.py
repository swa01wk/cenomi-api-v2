"""Unit tests for fitout_progress_suggestions_service.py (phase 1).

Coverage
--------
TestLeaseDates          — parse_lease_date / resolve_fitout_date / resolve_window
TestElapsedDefault      — the platform's percentage arithmetic, edge for edge
TestChipLadder          — lead value + quarter ladder, one row per PRD table
TestDecorations         — labeled_options shape the frontend already renders
TestQuestion            — the single question, create and approve wording
TestPrefilledOnApprove  — a percentage that arrived with the SR, not from the user
TestApproveChips        — the approver's decision row
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.agents.services.fitout_progress_suggestions_service import (
    APPROVE_ACTION,
    LADDER,
    MAX_OPTIONS,
    REJECT_ACTION,
    build_approve_options,
    build_fitout_progress_decorations,
    build_progress_options,
    build_progress_question,
    compute_fitout_progress_default,
    display_date,
    parse_lease_date,
    prefilled_percentage,
    recorded_percentage,
    resolve_fitout_date,
    resolve_window,
    suggest_percentages,
)

# A 100-day window makes the percentage dial-able: day N == N%.
WINDOW_START = date(2026, 1, 1)
WINDOW_END = WINDOW_START + timedelta(days=100)


def _collected(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "actual_fitout_start_date": WINDOW_START.isoformat(),
        "base_fitout_end_date": WINDOW_END.isoformat(),
    }
    base.update(overrides)
    return base


def _now_at(percent: int) -> datetime:
    """The instant at which the 100-day window is exactly *percent* elapsed."""
    return datetime.combine(WINDOW_START + timedelta(days=percent), datetime.min.time())


# ---------------------------------------------------------------------------
# TestLeaseDates
# ---------------------------------------------------------------------------


class TestLeaseDates:
    def test_parses_iso(self) -> None:
        assert parse_lease_date("2026-07-15") == date(2026, 7, 15)

    def test_parses_display_formats(self) -> None:
        assert parse_lease_date("15/07/2026") == date(2026, 7, 15)
        assert parse_lease_date("15-07-2026") == date(2026, 7, 15)

    def test_parses_timestamps(self) -> None:
        assert parse_lease_date("2026-07-15T00:00:00Z") == date(2026, 7, 15)
        assert parse_lease_date("2026-07-15 00:00:00") == date(2026, 7, 15)

    def test_go_zero_value_is_unset(self) -> None:
        # The lease sync writes the Go zero value instead of NULL.
        assert parse_lease_date("0001-01-01") is None
        assert parse_lease_date("0001-01-01T00:00:00Z") is None

    def test_blank_and_garbage(self) -> None:
        assert parse_lease_date("") is None
        assert parse_lease_date(None) is None
        assert parse_lease_date("soon") is None

    def test_resolve_prefers_the_first_usable_candidate(self) -> None:
        assert resolve_fitout_date("", "0001-01-01", "2026-07-15") == "2026-07-15"

    def test_resolve_returns_blank_when_nothing_usable(self) -> None:
        assert resolve_fitout_date("", None, "0001-01-01") == ""

    def test_window_prefers_the_payload_keys(self) -> None:
        # An SR hydrated on the approve path carries the platform's own names.
        collected = {
            "fitout_start_date": "2026-07-15",
            "fitout_end_date": "2026-10-15",
            "actual_fitout_start_date": "2026-01-01",
            "base_fitout_end_date": "2026-02-01",
        }
        assert resolve_window(collected) == ("2026-07-15", "2026-10-15")

    def test_window_start_falls_back_actual_planned_base(self) -> None:
        assert resolve_window(
            {
                "planned_fitout_start_date": "2026-06-01",
                "base_fitout_start_date": "2026-05-01",
                "base_fitout_end_date": "2026-10-15",
            }
        ) == ("2026-06-01", "2026-10-15")

    def test_window_end_falls_back_base_then_planned(self) -> None:
        assert resolve_window(
            {
                "actual_fitout_start_date": "2026-07-15",
                "planned_fitout_end_date": "2026-11-01",
            }
        ) == ("2026-07-15", "2026-11-01")

    def test_display_date(self) -> None:
        assert display_date("2026-07-15") == "15 Jul 2026"
        assert display_date("") == ""


# ---------------------------------------------------------------------------
# TestElapsedDefault
# ---------------------------------------------------------------------------


class TestElapsedDefault:
    def test_midpoint(self) -> None:
        assert compute_fitout_progress_default(
            "2026-01-01", "2026-01-03", datetime(2026, 1, 2)
        ) == 50

    def test_dialled_percentages(self) -> None:
        for percent in (1, 25, 33, 50, 82, 99):
            assert (
                compute_fitout_progress_default(
                    WINDOW_START.isoformat(), WINDOW_END.isoformat(), _now_at(percent)
                )
                == percent
            )

    def test_zero_before_the_window_opens(self) -> None:
        assert compute_fitout_progress_default(
            "2026-07-15", "2026-10-15", datetime(2026, 7, 1)
        ) == 0

    def test_zero_exactly_at_the_start(self) -> None:
        assert compute_fitout_progress_default(
            "2026-07-15", "2026-10-15", datetime(2026, 7, 15)
        ) == 0

    def test_hundred_exactly_at_the_end(self) -> None:
        assert compute_fitout_progress_default(
            "2026-07-15", "2026-10-15", datetime(2026, 10, 15)
        ) == 100

    def test_hundred_past_the_end(self) -> None:
        assert compute_fitout_progress_default(
            "2026-07-15", "2026-10-15", datetime(2027, 1, 1)
        ) == 100

    def test_zero_when_the_window_is_not_positive(self) -> None:
        assert compute_fitout_progress_default(
            "2026-10-15", "2026-07-15", datetime(2026, 9, 1)
        ) == 0
        assert compute_fitout_progress_default(
            "2026-07-15", "2026-07-15", datetime(2026, 9, 1)
        ) == 0

    def test_zero_when_a_date_is_missing_or_unset(self) -> None:
        now = datetime(2026, 9, 1)
        assert compute_fitout_progress_default("", "2026-10-15", now) == 0
        assert compute_fitout_progress_default("2026-07-15", "", now) == 0
        assert compute_fitout_progress_default("0001-01-01", "2026-10-15", now) == 0
        assert compute_fitout_progress_default("2026-07-15", "0001-01-01", now) == 0

    def test_rounds_half_away_from_zero_like_go_and_js(self) -> None:
        # 10.5% of an 8-day window. Python's round() is banker's rounding and
        # would give 10; Go's math.Round and JS's Math.round give 11.
        assert compute_fitout_progress_default(
            "2026-01-01", "2026-01-09", datetime(2026, 1, 1, 20, 9, 36)
        ) == 11

    def test_never_leaves_the_range(self) -> None:
        for percent in range(-20, 140, 7):
            value = compute_fitout_progress_default(
                WINDOW_START.isoformat(), WINDOW_END.isoformat(), _now_at(percent)
            )
            assert 0 <= value <= 100


# ---------------------------------------------------------------------------
# TestChipLadder
# ---------------------------------------------------------------------------


class TestChipLadder:
    def test_suggested_82(self) -> None:
        options = build_progress_options(_collected(), _now_at(82))
        assert options == [
            {"label": "82% · suggested", "value": "82"},
            {"label": "75%", "value": "75"},
            {"label": "100% · complete", "value": "100"},
        ]

    def test_recorded_beats_a_lower_elapsed(self) -> None:
        # Progress does not go backwards: 90 on the lease outranks 82 elapsed.
        options = build_progress_options(
            _collected(recorded_fitout_progress="90"), _now_at(82)
        )
        assert options == [
            {"label": "90% · recorded", "value": "90"},
            {"label": "75%", "value": "75"},
            {"label": "100% · complete", "value": "100"},
        ]

    def test_elapsed_beats_a_lower_recorded(self) -> None:
        options = build_progress_options(
            _collected(recorded_fitout_progress="40"), _now_at(82)
        )
        assert options[0] == {"label": "82% · suggested", "value": "82"}

    def test_suggested_50_offers_both_neighbours(self) -> None:
        options = build_progress_options(_collected(), _now_at(50))
        assert [o["value"] for o in options] == ["50", "25", "75", "100"]

    def test_not_started(self) -> None:
        options = build_progress_options(_collected(), _now_at(0))
        assert options == [
            {"label": "0% · not started", "value": "0"},
            {"label": "25%", "value": "25"},
            {"label": "100% · complete", "value": "100"},
        ]

    def test_past_the_due_date(self) -> None:
        options = build_progress_options(_collected(), _now_at(120))
        assert options == [
            {"label": "100% · complete", "value": "100"},
            {"label": "75%", "value": "75"},
        ]

    def test_plain_ladder_when_dates_are_unusable(self) -> None:
        options = build_progress_options({"lease_code": "t0105712"}, _now_at(50))
        assert [o["value"] for o in options] == ["25", "50", "75", "100"]

    def test_never_more_than_four_chips(self) -> None:
        for percent in range(0, 101, 3):
            options = build_progress_options(_collected(), _now_at(percent))
            assert 1 <= len(options) <= MAX_OPTIONS

    def test_values_are_unique_and_in_range(self) -> None:
        for percent in range(0, 101, 3):
            values = [int(o["value"]) for o in build_progress_options(
                _collected(), _now_at(percent)
            )]
            assert len(values) == len(set(values))
            assert all(0 <= v <= 100 for v in values)

    def test_ladder_constant_is_quarters(self) -> None:
        assert LADDER == (0, 25, 50, 75, 100)

    def test_decimal_recorded_value_keeps_its_precision(self) -> None:
        options = build_progress_options(
            _collected(recorded_fitout_progress="82.5"), _now_at(10)
        )
        assert options[0] == {"label": "82.5% · recorded", "value": "82.5"}

    def test_suggestion_reports_its_inputs(self) -> None:
        suggestion = suggest_percentages(_collected(), _now_at(82))
        assert suggestion.start == WINDOW_START.isoformat()
        assert suggestion.end == WINDOW_END.isoformat()
        assert suggestion.elapsed == 82
        assert suggestion.lead == 82
        assert suggestion.lead_source == "suggested"
        assert suggestion.has_window is True

    def test_suggestion_without_a_window(self) -> None:
        suggestion = suggest_percentages({}, _now_at(82))
        assert suggestion.has_window is False
        assert suggestion.elapsed is None
        assert suggestion.lead is None
        assert suggestion.lead_source is None

    def test_recorded_percentage_reads_either_key(self) -> None:
        assert recorded_percentage({"recorded_fitout_progress": "65"}) == 65
        assert recorded_percentage({"fitout_progress": 65}) == 65
        assert recorded_percentage({"fitout_progress": ""}) is None
        assert recorded_percentage({}) is None


# ---------------------------------------------------------------------------
# TestDecorations
# ---------------------------------------------------------------------------


class TestDecorations:
    def test_shape_is_the_existing_renderer(self) -> None:
        decorations = build_fitout_progress_decorations(_collected(), _now_at(82))
        assert decorations["suggestions"]["kind"] == "labeled_options"
        assert decorations["suggestions"]["options"][0]["value"] == "82"

    def test_chips_carry_no_action(self) -> None:
        # Value-only chips send the number as the user's own message, so the
        # transcript reads as their answer and no new action code is needed.
        decorations = build_fitout_progress_decorations(_collected(), _now_at(82))
        for option in decorations["suggestions"]["options"]:
            assert "action" not in option
            assert set(option) == {"label", "value"}

    def test_still_offers_the_ladder_without_dates(self) -> None:
        decorations = build_fitout_progress_decorations({}, _now_at(82))
        assert len(decorations["suggestions"]["options"]) == 4


# ---------------------------------------------------------------------------
# TestQuestion
# ---------------------------------------------------------------------------


class TestQuestion:
    def test_create_question_names_the_window_and_the_suggestion(self) -> None:
        question = build_progress_question(
            _collected(
                actual_fitout_start_date="2026-07-15",
                base_fitout_end_date="2026-10-15",
            ),
            now=datetime(2026, 9, 27),
        )
        assert "15 Jul 2026" in question
        assert "15 Oct 2026" in question
        assert "%" in question
        assert "How complete is the fit-out today?" in question
        assert "type your own" in question

    def test_create_question_without_dates_asks_plainly(self) -> None:
        question = build_progress_question({}, now=datetime(2026, 9, 27))
        assert "How complete is the fit-out today?" in question
        assert "between 0 and 100" in question
        assert "elapsed" not in question

    def test_create_question_mentions_a_recorded_value(self) -> None:
        question = build_progress_question(
            _collected(recorded_fitout_progress="65"), now=_now_at(82)
        )
        assert "currently records **65%**" in question

    def test_approve_question_leads_with_the_sr_and_its_value(self) -> None:
        question = build_progress_question(
            _collected(recorded_fitout_progress="65"),
            stage="APPROVE_SR",
            sr_id="8517",
            now=_now_at(82),
        )
        assert question.startswith("SR 8517 records **65%**.")
        assert "Approve 65%" in question
        assert "give me the right percentage" in question

    def test_approve_question_without_a_recorded_value(self) -> None:
        question = build_progress_question(
            _collected(), stage="APPROVE_SR", sr_id="8517", now=_now_at(82)
        )
        assert "no percentage recorded yet" in question
        assert "How complete is the fit-out today?" in question

    def test_approve_question_without_an_sr_id(self) -> None:
        question = build_progress_question(
            _collected(recorded_fitout_progress="65"),
            stage="APPROVE_SR",
            now=_now_at(82),
        )
        assert question.startswith("This request records **65%**.")


# ---------------------------------------------------------------------------
# TestPrefilledOnApprove
# ---------------------------------------------------------------------------


class TestPrefilledOnApprove:
    """A percentage that arrived with the SR is context, not the user's answer."""

    def test_prefilled_value_is_read_until_the_user_answers(self) -> None:
        collected = {"fitout_progress_percentage": "65"}
        assert prefilled_percentage(collected) == 65

    def test_once_answered_it_is_no_longer_prefilled(self) -> None:
        collected = {"fitout_progress_percentage": "82", "_progress_confirmed": True}
        assert prefilled_percentage(collected) is None

    def test_absent(self) -> None:
        assert prefilled_percentage({}) is None

    def test_prefilled_floors_the_suggestion(self) -> None:
        suggestion = suggest_percentages(_collected(), _now_at(50), prefilled=90)
        assert suggestion.lead == 90
        assert suggestion.lead_source == "recorded"

    def test_a_higher_elapsed_still_wins(self) -> None:
        suggestion = suggest_percentages(_collected(), _now_at(95), prefilled=65)
        assert suggestion.lead == 95
        assert suggestion.lead_source == "suggested"

    def test_approve_question_names_the_stored_percentage(self) -> None:
        question = build_progress_question(
            {**_collected(), "fitout_progress_percentage": "65"},
            stage="APPROVE_SR",
            sr_id="8517",
            now=_now_at(82),
        )
        assert question.startswith("SR 8517 records **65%**.")
        assert "Approve 65%" in question

    def test_create_question_does_not_read_the_answer_back_as_context(self) -> None:
        # On create the field holds the user's own answer; quoting it back as
        # "currently records" would be nonsense.
        question = build_progress_question(
            {**_collected(), "fitout_progress_percentage": "82"},
            stage="CREATE_SR",
            now=_now_at(82),
        )
        assert "records" not in question


# ---------------------------------------------------------------------------
# TestApproveChips
# ---------------------------------------------------------------------------


class TestApproveChips:
    """The approver's row: a decision, two alternatives, the other decision."""

    def _approve_collected(self) -> dict[str, object]:
        return {**_collected(), "fitout_progress_percentage": "65"}

    def test_row_leads_with_the_decision_and_ends_with_the_other(self) -> None:
        options = build_approve_options(self._approve_collected(), _now_at(82))
        assert options[0]["label"] == "Approve 65%"
        assert options[0]["action"] == APPROVE_ACTION
        assert options[-1]["label"] == "Reject"
        assert options[-1]["action"] == REJECT_ACTION

    def test_alternatives_are_value_only(self) -> None:
        # Supplying a different number is the update-and-approve the form's
        # single button performs, so it re-renders the card rather than deciding.
        options = build_approve_options(self._approve_collected(), _now_at(82))
        for option in options[1:-1]:
            assert "action" not in option

    def test_the_stored_value_is_not_offered_twice(self) -> None:
        options = build_approve_options(
            {**_collected(), "fitout_progress_percentage": "75"}, _now_at(75)
        )
        values = [o["value"] for o in options if "action" not in o]
        assert "75" not in values

    def test_row_stays_short(self) -> None:
        for percent in range(0, 101, 7):
            options = build_approve_options(
                {**_collected(), "fitout_progress_percentage": "65"}, _now_at(percent)
            )
            assert len(options) <= 4

    def test_no_stored_value_means_no_approve_chip(self) -> None:
        # Nothing to approve yet — the platform requires a percentage to approve.
        options = build_approve_options(_collected(), _now_at(82))
        assert all(o.get("action") != APPROVE_ACTION for o in options)
        assert options[-1]["action"] == REJECT_ACTION

    def test_an_answered_percentage_is_no_longer_a_stored_one(self) -> None:
        options = build_approve_options(
            {
                **_collected(),
                "fitout_progress_percentage": "90",
                "_progress_confirmed": True,
            },
            _now_at(82),
        )
        assert all(o.get("action") != APPROVE_ACTION for o in options)

    def test_create_stage_has_no_decision_chips(self) -> None:
        decorations = build_fitout_progress_decorations(
            self._approve_collected(), _now_at(82), stage="CREATE_SR"
        )
        assert all(
            "action" not in o for o in decorations["suggestions"]["options"]
        )

    def test_approve_stage_decorations_use_the_decision_row(self) -> None:
        decorations = build_fitout_progress_decorations(
            self._approve_collected(), _now_at(82), stage="APPROVE_SR"
        )
        actions = [o.get("action") for o in decorations["suggestions"]["options"]]
        assert APPROVE_ACTION in actions
        assert REJECT_ACTION in actions
