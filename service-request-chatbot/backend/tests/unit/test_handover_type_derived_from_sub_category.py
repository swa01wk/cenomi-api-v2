"""An SR without a handover type still has one — its sub-category says which.

509 of the 578 handover SRs on the platform carry no ``already_handovered``
in their payload (all raised through the form; the newest in July). Every SR
the assistant creates has it, because both payload builders write it per path.

Opening one of the 509 left the type blank on the review card, and left the
frontend bridge without the discriminator it reads to choose between the
HANDOVER and HANDED_OVER form skeletons — so an Already Trading SR could open
against the wrong skeleton entirely.

The sub-category is the SR's own statement of which path it is, so the type is
derived from it when the payload is silent. A value the platform did send is
always kept, even one that disagrees, because that is the SR's own record.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.services.review_decorations_service import payload_to_collected_data


def _mapped(**payload: Any) -> dict[str, Any]:
    return payload_to_collected_data({"title": "handover-t0111321", **payload})


class TestDerivedWhenThePayloadIsSilent:
    @pytest.mark.parametrize(
        ("sub_category", "expected"),
        [
            ("HANDOVER", "initiate_handover"),
            ("HANDED_OVER", "already_trading"),
            ("handed_over", "already_trading"),
            ("  HANDOVER  ", "initiate_handover"),
        ],
    )
    def test_the_sub_category_supplies_the_type(
        self, sub_category: str, expected: str
    ) -> None:
        assert _mapped(sub_category=sub_category)["already_handovered"] == expected

    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_a_blank_value_is_treated_as_silence(self, blank: Any) -> None:
        mapped = _mapped(sub_category="HANDED_OVER", already_handovered=blank)
        assert mapped["already_handovered"] == "already_trading"


class TestNothingIsInvented:
    def test_a_value_the_platform_sent_is_kept(self) -> None:
        # Even disagreeing with the sub-category: the SR's own record wins.
        mapped = _mapped(sub_category="HANDOVER", already_handovered="already_trading")
        assert mapped["already_handovered"] == "already_trading"

    @pytest.mark.parametrize(
        "sub_category", ["FIT_OUT_CONFIRM_FOS", "WORK_PERMIT", "", None, "SOMETHING_NEW"]
    )
    def test_a_sub_category_that_implies_no_type_adds_none(
        self, sub_category: Any
    ) -> None:
        assert "already_handovered" not in _mapped(sub_category=sub_category)

    def test_an_empty_payload_stays_empty(self) -> None:
        assert payload_to_collected_data({}) == {}
