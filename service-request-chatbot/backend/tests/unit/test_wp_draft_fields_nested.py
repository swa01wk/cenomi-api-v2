"""draft_preview must keep nested WP widgets as objects for Formik sync."""

from __future__ import annotations

from app.agents.schemas.work_permit_form_fields import wp_draft_fields_from_collected


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
    by_key = {row["key"]: row["value"] for row in rows}

    assert by_key["typeofwork"] == typeofwork
    assert isinstance(by_key["typeofwork"], dict)
    assert by_key["effective_starting"] == effective
    assert isinstance(by_key["effective_starting"], dict)
    assert by_key["firstperiod"] == firstperiod
    assert isinstance(by_key["firstperiod"], dict)
    # Non-widget lists stay JSON-encoded strings for the bridge.
    assert by_key["unit_codes"] == '["FF050"]'
    assert by_key["title"] == "Ops WP"
