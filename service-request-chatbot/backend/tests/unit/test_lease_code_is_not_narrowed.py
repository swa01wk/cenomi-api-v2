"""A lease code must not be narrowed by identifiers the model guessed.

Reported live on t0108880, "Brand Taxi Mall Rental". The picker listed the
lease, the user selected it, and the assistant answered:

    Sorry, I couldn't find a lease matching that information. Please
    double-check the lease code, brand name, company name, or mall name...

The trace shows why. The extractor split the brand name in two — it contains
the word "Mall" — and the lookup went out as:

    lease_code=t0108880, brand='Brand Taxi', mall='Mall Rental'

The platform ANDs every identifier onto the code, so a mall that does not
exist removed the row and the lease came back not_found. The lease itself was
fine: activated 2025-09-18, no handover SR, and reachable by the same user's
picker a moment earlier.

A lease code identifies one lease on its own — the platform matches it exactly
against pms_tenant_code — so nothing else is sent with it. RBAC scope and
purpose are not identifiers and still travel.
"""

from __future__ import annotations

import app.agents.services.lease_lookup_service as lease_lookup_service

_build_params = lease_lookup_service.HttpLeaseLookupService._build_params
Query = lease_lookup_service.LeaseLookupQuery


class TestALeaseCodeTravelsAlone:
    def test_a_split_brand_name_cannot_filter_the_lease_away(self) -> None:
        params = _build_params(
            Query(lease_code="t0108880", brand="Brand Taxi", mall="Mall Rental")
        )
        assert params == {"lease_code": "t0108880"}

    def test_company_is_dropped_with_the_rest(self) -> None:
        params = _build_params(
            Query(lease_code="t0108880", company="Taxi Mall Rental EST")
        )
        assert params == {"lease_code": "t0108880"}

    def test_the_property_scope_still_travels(self) -> None:
        # RBAC, not an identifier — dropping it would show a user leases in
        # malls they do not manage.
        params = _build_params(Query(lease_code="t0108880", brand="X", property_ids=[6]))
        assert params == {"lease_code": "t0108880", "property_id": [6]}

    def test_purpose_still_travels(self) -> None:
        # Fitout confirmations rely on it to bypass the handover-exists gate.
        params = _build_params(Query(lease_code="t0108880", purpose="trading_confirm"))
        assert params == {"lease_code": "t0108880", "purpose": "trading_confirm"}


class TestWithoutACodeNothingChanges:
    def test_brand_and_mall_still_narrow_a_search(self) -> None:
        params = _build_params(Query(brand="Nike", mall="Riyadh Park"))
        assert params == {"brand": "Nike", "mall": "Riyadh Park"}

    def test_company_still_narrows_a_search(self) -> None:
        params = _build_params(Query(company="Azadea", mall="Riyadh Park"))
        assert params == {"company": "Azadea", "mall": "Riyadh Park"}

    def test_an_empty_query_sends_nothing(self) -> None:
        assert _build_params(Query()) == {}
