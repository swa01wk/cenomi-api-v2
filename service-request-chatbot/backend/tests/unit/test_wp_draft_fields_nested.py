"""draft_preview must keep nested WP widgets as objects for Formik sync."""

from __future__ import annotations

from app.agents.schemas.work_permit_form_fields import (
    EMPTY_PERIOD,
    format_wp_field_display,
    summarize_period,
    wp_draft_fields_from_collected,
    wp_english_part,
    wp_safety_answer,
)


def test_measure_of_safety_lists_only_affirmative_items() -> None:
    shown = format_wp_field_display(
        "measureofsafety",
        {
            "fire_extinguisher": "No - لا",
            "fire_hoses": "Yes - نعم",
            "sprinker_on": "yes",
            "volatile_remove": "no",
            "fire_proofcoating": "",
        },
    )
    assert shown == "Fire hoses, Sprinkler on"


def test_safety_answer_normalizes_bilingual_and_flags() -> None:
    assert wp_safety_answer("Yes - نعم") == "Yes"
    assert wp_safety_answer("No - لا") == "No"
    assert wp_safety_answer(True) == "Yes"
    assert wp_safety_answer("false") == "No"
    assert wp_safety_answer("Not applicable") == "Not applicable"
    assert wp_safety_answer("") == ""


def test_english_part_only_strips_an_arabic_half() -> None:
    assert wp_english_part("Store - متجر") == "Store"
    assert wp_english_part("Gate 3 - North") == "Gate 3 - North"
    assert wp_english_part("  Plain  ") == "Plain"


def test_wp_draft_fields_keeps_nested_widget_objects() -> None:
    typeofwork = {"value": ["maintenance"], "otheroption": ""}
    effective = {"datefrom": "01/04/2026", "dateto": "02/04/2026"}
    firstperiod = {"from": "09:00", "to": "17:00"}

    rows = wp_draft_fields_from_collected(
        {
            "title": "Ops WP",
            "typeofwork": typeofwork,
            "effective_starting": effective,
            "firstperiod": firstperiod,
            "unit_codes": ["FF050"],
        }
    )
    by_key = {row["key"]: row for row in rows}

    assert by_key["typeofwork"]["value"] == typeofwork
    assert isinstance(by_key["typeofwork"]["value"], dict)
    assert by_key["typeofwork"]["display"] == "maintenance"

    assert by_key["effective_starting"]["value"] == effective
    assert isinstance(by_key["effective_starting"]["value"], dict)
    assert by_key["effective_starting"]["display"] == "01/04/2026 → 02/04/2026"

    # Expanded for portal TimePickerInput (fromLT/toLT + ISO).
    period_val = by_key["firstperiod"]["value"]
    assert period_val["fromLT"] == "9:00 AM"
    assert period_val["toLT"] == "5:00 PM"
    assert period_val["from"] == "2026-04-01T09:00:00.000Z"
    assert period_val["to"] == "2026-04-01T17:00:00.000Z"
    assert isinstance(period_val, dict)
    assert by_key["firstperiod"]["display"] == "9:00 AM – 5:00 PM"

    # Non-widget lists stay JSON-encoded strings for the bridge.
    assert by_key["unit_codes"]["value"] == '["FF050"]'
    assert by_key["title"]["value"] == "Ops WP"


def test_wp_draft_period_unchanged_without_effective_date() -> None:
    firstperiod = {"from": "15:00", "to": "17:00"}
    rows = wp_draft_fields_from_collected({"firstperiod": firstperiod})
    by_key = {row["key"]: row for row in rows}
    assert by_key["firstperiod"]["value"] == firstperiod
    assert by_key["firstperiod"]["display"] == "15:00 – 17:00"


def test_wp_draft_empty_secondperiod_has_blank_display() -> None:
    rows = wp_draft_fields_from_collected({"secondperiod": dict(EMPTY_PERIOD)})
    by_key = {row["key"]: row for row in rows}
    assert by_key["secondperiod"]["value"] == EMPTY_PERIOD
    assert by_key["secondperiod"]["display"] == ""


def test_wp_draft_omits_display_only_company() -> None:
    rows = wp_draft_fields_from_collected(
        {
            "company": "Retail Co",
            "company_name": "4821",
            "tenant_profile_id": 4821,
        }
    )
    by_key = {row["key"]: row for row in rows}
    assert "company" not in by_key
    assert by_key["company_name"]["value"] == "4821"
    assert str(by_key["tenant_profile_id"]["value"]) == "4821"


def test_format_wp_field_display_empty_period() -> None:
    assert summarize_period(dict(EMPTY_PERIOD)) == ""
    assert format_wp_field_display("secondperiod", dict(EMPTY_PERIOD)) == ""
    assert not isinstance(
        format_wp_field_display("secondperiod", dict(EMPTY_PERIOD)),
        dict,
    )
