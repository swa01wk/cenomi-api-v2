"""Cold WP create readiness — planneddate, second period, workers, display."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.work_permit.work_permit_payload_builder_node import (
    build_create_work_permit_payload,
)
from app.agents.schemas.work_permit_form_fields import (
    EMPTY_PERIOD,
    apply_cold_worker_iqama_documents,
    cold_worker_iqama_doc_key,
    cold_workers_missing_doc_indexes,
    cold_workers_text_incomplete,
    expand_planneddate_for_api,
    format_wp_field_display,
    is_tenant_field_missing,
    summarize_list_workers,
    summarize_planneddate,
    wp_draft_fields_from_collected,
)
from app.agents.schemas.work_permit_schema import get_missing_fields


def _base_cold(subtype: str = "CONSTRUCTION_COLD") -> dict[str, Any]:
    return {
        "work_permit_type": subtype,
        "_wp_type_confirmed": True,
        "lease_code": "t0111816",
        "lease_id": 1,
        "property_id": 2,
        "tenant_profile_id": 9,
        "lease_brand_mall": "t0111816 / Brand / Mall",
        "unit_codes": ["GFK20"],
        "mall": "Mall",
        "brand": "Brand",
        "unit_type": "Store",
        "department": "operations",
        "owner": "Owner",
        "trade_name": "Trade",
        "responsible_person": "Kishan",
        "phone": "8486697082",
        "type_work": "Fit-out",
        "list_equipment": "Drill",
        "work_info": "Night work",
        "title": "Cold WP",
        "description": "Cold WP desc",
    }


def _full_planneddate(**extra: str) -> dict[str, str]:
    base = {
        "worktimedate": "25/09/2026",
        "completionworkdate": "28/09/2026",
        "worktimefrom": "09:00",
        "worktimeto": "17:00",
        "completionfrom": "",
        "completionto": "",
    }
    base.update(extra)
    return base


def _full_worker(**extra: str) -> dict[str, str]:
    row = {
        "full_name": "Omar",
        "designation": "Tech",
        "company_name": "ABC",
        "phone": "501111111",
        "country_code": "966",
        "iqama_number": "2345678901",
        "document_id": "doc-1",
    }
    row.update(extra)
    return row


class TestColdPlanneddateCompleteness:
    def test_dates_without_times_incomplete(self) -> None:
        val = {
            "worktimedate": "25/09/2026",
            "completionworkdate": "28/09/2026",
            "worktimefrom": "",
            "worktimeto": "",
            "completionfrom": "",
            "completionto": "",
        }
        assert is_tenant_field_missing("planneddate", val, field_type="planneddate")

    def test_dates_and_first_period_complete(self) -> None:
        assert not is_tenant_field_missing(
            "planneddate", _full_planneddate(), field_type="planneddate"
        )

    def test_expand_planneddate_adds_lt(self) -> None:
        out = expand_planneddate_for_api(_full_planneddate())
        assert out["worktimefromLT"] == "9:00 AM"
        assert out["worktimetoLT"] == "5:00 PM"


class TestColdSecondPeriodOptional:
    @pytest.mark.parametrize("subtype", ["CONSTRUCTION_COLD", "MAINTENANCE_COLD"])
    def test_second_period_asked_when_none(self, subtype: str) -> None:
        collected = {
            **_base_cold(subtype),
            "planneddate": _full_planneddate(),
            "workers": [_full_worker()],
        }
        missing = get_missing_fields("CREATE_WORK_PERMIT", collected)
        assert "planned_second_period" in missing
        assert "notes" in missing

    @pytest.mark.parametrize("subtype", ["CONSTRUCTION_COLD", "MAINTENANCE_COLD"])
    def test_second_period_skip_does_not_reask(self, subtype: str) -> None:
        collected = {
            **_base_cold(subtype),
            "planneddate": _full_planneddate(),
            "planned_second_period": dict(EMPTY_PERIOD),
            "notes": "",
            "workers": [_full_worker()],
        }
        missing = get_missing_fields("CREATE_WORK_PERMIT", collected)
        assert "planned_second_period" not in missing
        assert "notes" not in missing
        assert "planneddate" not in missing
        assert "workers" not in missing


class TestColdWorkersIqama:
    def test_text_incomplete_without_iqama(self) -> None:
        workers = [_full_worker(iqama_number="", document_id="")]
        assert cold_workers_text_incomplete(workers)

    def test_docs_missing_when_text_done(self) -> None:
        workers = [_full_worker(document_id="")]
        assert not cold_workers_text_incomplete(workers)
        assert cold_workers_missing_doc_indexes(workers) == [0]
        assert is_tenant_field_missing("workers", workers, field_type="listWorker")

    def test_apply_iqama_document(self) -> None:
        workers = [_full_worker(document_id="")]
        key = cold_worker_iqama_doc_key(0)
        synced = apply_cold_worker_iqama_documents(
            workers,
            [{"document_type_id": key, "document_id": "uploaded-9", "status": "uploaded"}],
        )
        assert synced[0]["document_id"] == "uploaded-9"
        assert not is_tenant_field_missing("workers", synced, field_type="listWorker")


class TestColdDisplayAndDraft:
    def test_planneddate_and_workers_display(self) -> None:
        planned = expand_planneddate_for_api(
            _full_planneddate(completionfrom="18:00", completionto="20:00")
        )
        human = summarize_planneddate(planned)
        assert "25/09/2026" in human
        assert "9:00 AM" in human or "09:00" in human
        assert "second" in human

        workers = [_full_worker()]
        assert "Omar" in summarize_list_workers(workers)
        assert "2345678901" in summarize_list_workers(workers)

    def test_draft_keeps_objects_and_adds_display(self) -> None:
        planned = _full_planneddate()
        workers = [_full_worker()]
        rows = wp_draft_fields_from_collected(
            {
                **_base_cold(),
                "planneddate": planned,
                "workers": workers,
            }
        )
        by_key = {r["key"]: r for r in rows}
        assert isinstance(by_key["planneddate"]["value"], dict)
        assert by_key["planneddate"]["value"]["worktimefromLT"] == "9:00 AM"
        assert by_key["planneddate"]["display"]
        assert isinstance(by_key["workers"]["value"], list)
        assert by_key["workers"]["display"]
        assert "planned_second_period" not in by_key
        assert format_wp_field_display("planneddate", by_key["planneddate"]["value"])


class TestColdPayloadBothSubtypes:
    @pytest.mark.parametrize("subtype", ["CONSTRUCTION_COLD", "MAINTENANCE_COLD"])
    def test_payload_includes_iqama_doc_and_lt(self, subtype: str) -> None:
        data = {
            **_base_cold(subtype),
            "planneddate": _full_planneddate(
                completionfrom="18:00", completionto="20:00"
            ),
            "planned_second_period": {"from": "18:00", "to": "20:00"},
            "notes": "",
            "workers": [_full_worker()],
        }
        # Mirror merge sync of second period into planneddate.
        data["planneddate"]["completionfrom"] = "18:00"
        data["planneddate"]["completionto"] = "20:00"

        payload = build_create_work_permit_payload(data)
        assert payload["sub_category"] == subtype
        inner = payload["payload"]
        assert inner["planneddate"]["worktimefromLT"] == "9:00 AM"
        assert inner["planneddate"]["completionfromLT"] == "6:00 PM"
        assert inner["workers"][0]["iqama_number"] == "2345678901"
        assert inner["workers"][0]["document_id"] == "doc-1"
        assert "planned_second_period" not in inner
