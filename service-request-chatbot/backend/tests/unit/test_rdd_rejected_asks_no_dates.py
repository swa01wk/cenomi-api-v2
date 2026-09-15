"""A disapproved handover has no dates to record, so none are asked for.

Reported from RDD PM testing: after uploading a Rejected report — which sets
``document_status = DISAPPROVED`` — the assistant went on to ask for the
Actual Handover Date, Fit-out Start Date and Fit-out End Date. The RDD form
has no fields for them on a rejection, so the reviewer was asked three
questions they had no way to answer.

Both halves matter. Dropping them from get_missing_fields stops the asking;
dropping them from the required set stops them blocking the submit. With only
the first, the reviewer would be asked nothing and still be unable to submit —
the worse half of the same bug.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.schemas.handover_schema import get_missing_fields
from app.agents.services.validation_service import ValidationService

_DATES = ["actual_handover_date", "fitout_start_date", "fitout_end_date"]
_REJECTED_REPORT = [
    {"document_type_id": "SR_REJECTED_HANDOVER_REPORT", "document_id": "d1"}
]


def _draft(status: str, **over: Any) -> dict[str, Any]:
    draft: dict[str, Any] = {
        "lease_code": "t0110565",
        "lease_brand_mall": "t0110565 - Brand - Mall",
        "title": "handover-t0110565",
        "description": "Handover inspection.",
        "guideLineLink": "https://guidelines.example/handover",
        "comments": "Rejected — fit-out not to specification.",
        "document_status": status,
    }
    draft.update(over)
    return draft


def _blocking(draft: dict[str, Any]) -> list[str]:
    errors = ValidationService().validate_draft(
        data=draft,
        workflow_stage="RDD_REVIEW",
        documents=_REJECTED_REPORT,
        role="DD_ENGINEER",
    )
    return [e["field"] for e in errors if e.get("blocking")]


class TestDisapproved:
    def test_no_dates_are_asked_for(self) -> None:
        assert get_missing_fields("RDD_REVIEW", _draft("DISAPPROVED")) == []

    def test_no_date_blocks_the_submission(self) -> None:
        assert not [f for f in _blocking(_draft("DISAPPROVED")) if f in _DATES]

    @pytest.mark.parametrize("spelling", ["disapproved", "  DISAPPROVED  "])
    def test_the_status_is_matched_however_it_is_written(self, spelling: str) -> None:
        assert get_missing_fields("RDD_REVIEW", _draft(spelling)) == []


class TestTheHandoverStillGoingAhead:
    @pytest.mark.parametrize("status", ["APPROVED", "APPROVED_WITH_CHANGES"])
    def test_the_dates_are_still_required(self, status: str) -> None:
        assert get_missing_fields("RDD_REVIEW", _draft(status)) == _DATES
        assert [f for f in _blocking(_draft(status)) if f in _DATES] == _DATES

    def test_an_unanswered_status_still_asks_for_everything(self) -> None:
        missing = get_missing_fields("RDD_REVIEW", _draft(""))
        assert missing[0] == "document_status"
        assert all(date in missing for date in _DATES)

    def test_dates_already_given_on_a_rejection_are_left_alone(self) -> None:
        # Nothing is wiped — a reviewer who recorded dates before rejecting
        # keeps them; they simply stop being demanded.
        draft = _draft("DISAPPROVED", actual_handover_date="2026-09-10")
        assert get_missing_fields("RDD_REVIEW", draft) == []
        assert draft["actual_handover_date"] == "2026-09-10"
