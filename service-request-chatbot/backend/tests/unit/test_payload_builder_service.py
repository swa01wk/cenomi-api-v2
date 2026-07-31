"""Unit tests — build_create_handover_payload and PayloadBuilderService.

Test groups
-----------
TestBuildCreateHandoverPayloadShape     — Top-level and inner payload structure.
TestBuildCreateHandoverPayloadValues    — All field values map correctly.
TestBuildCreateHandoverPayloadDefaults  — Optional / default fields.
TestBuildCreateHandoverPayloadValidation — Missing / empty key raises ValueError.
TestBuildCreateHandoverPayloadConstants — Static constants (service_category, etc.).
TestPayloadBuilderServiceClass          — Wrapper class delegation.
TestPayloadBuilderNodeIntegration       — Async node stores payload in backend_refs.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.services.payload_builder_service import (
    PayloadBuilderService,
    _REQUIRED_DATA_KEYS,
    build_create_handover_payload,
    build_fm_approve_payload,
    build_fm_review_payload,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _full_data() -> dict[str, Any]:
    """Complete ``collected_data`` dict with all required keys present."""
    return {
        "mall": "Riyadh Park",
        "brand": "Nike",
        "lease_code": "LC-001",
        "lease_id": "LEASE-001",
        "title": "Handover SR",
        "endDate": "2025-09-01",
        "comments": "All good",
        "startDate": "2025-03-01",
        "description": "Tenant fit-out handover",
        "inspection_done_by": "FM_MANAGER",
        "lease_brand_mall": "LC-001|Nike|Riyadh Park",
        "unit_codes": ["U-01", "U-02"],
        "contracted_area": 200,
        "city": "Riyadh",
        "brand_id": "BR-001",
        "tenant_profile_id": "TP-001",
        "contract_id": "CONT-001",
        "property_id": "PROP-001",
        "notes": "Optional note",
        "startDateLT": "2025-03-01T00:00:00+03:00",
        "endDateLT": "2025-09-01T00:00:00+03:00",
    }


# ===========================================================================
# TestBuildCreateHandoverPayloadShape
# ===========================================================================


class TestBuildCreateHandoverPayloadShape:
    def test_returns_dict(self) -> None:
        assert isinstance(build_create_handover_payload(_full_data()), dict)

    def test_top_level_keys_present(self) -> None:
        result = build_create_handover_payload(_full_data())
        expected_top = {
            "payload",
            "title",
            "tenant_profile_id",
            "property_id",
            "service_category",
            "sub_category",
            "lease_code",
            "lease_id",
            "service_request_id",
        }
        assert expected_top <= result.keys()

    def test_inner_payload_is_dict(self) -> None:
        result = build_create_handover_payload(_full_data())
        assert isinstance(result["payload"], dict)

    def test_inner_payload_has_all_expected_keys(self) -> None:
        result = build_create_handover_payload(_full_data())
        inner = result["payload"]
        expected = {
            "mall", "brand", "lease", "notes", "title", "endDate", "comments",
            "startDate", "attachments", "description", "documents_ids",
            "guideLineLink", "inspectionDoneBy", "lease_brand_mall",
            "inspection_done_by", "document_status_map", "unit_readiness_date",
            "expected_handover_date", "company_name", "tenant_contact",
            "user_action", "unit_codes", "contracted_area", "city", "brand_id",
            "tenant_profile_id", "contract_id", "property_id",
            "startDateLT", "endDateLT",
        }
        assert expected <= inner.keys()

    def test_service_request_id_is_empty_string(self) -> None:
        result = build_create_handover_payload(_full_data())
        assert result["service_request_id"] == ""

    def test_attachments_is_empty_string(self) -> None:
        result = build_create_handover_payload(_full_data())
        assert result["payload"]["attachments"] == ""

    def test_documents_ids_is_empty_list(self) -> None:
        result = build_create_handover_payload(_full_data())
        assert result["payload"]["documents_ids"] == []

    def test_document_status_map_is_empty_list(self) -> None:
        result = build_create_handover_payload(_full_data())
        assert result["payload"]["document_status_map"] == []

    def test_guideline_link_is_empty_string(self) -> None:
        result = build_create_handover_payload(_full_data())
        assert result["payload"]["guideLineLink"] == ""

    def test_user_action_is_none(self) -> None:
        result = build_create_handover_payload(_full_data())
        assert result["payload"]["user_action"] is None

    def test_unit_readiness_date_is_empty_string(self) -> None:
        result = build_create_handover_payload(_full_data())
        assert result["payload"]["unit_readiness_date"] == ""

    def test_expected_handover_date_is_empty_string(self) -> None:
        result = build_create_handover_payload(_full_data())
        assert result["payload"]["expected_handover_date"] == ""

    def test_tenant_contact_is_empty_string(self) -> None:
        result = build_create_handover_payload(_full_data())
        assert result["payload"]["tenant_contact"] == ""

    def test_status_key_absent_from_payload(self) -> None:
        """status must never be sent during initial Mall Manager creation."""
        result = build_create_handover_payload(_full_data())
        assert "status" not in result
        assert "status" not in result["payload"]


# ===========================================================================
# TestBuildCreateHandoverPayloadValues
# ===========================================================================


class TestBuildCreateHandoverPayloadValues:
    def test_mall_mapped(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["payload"]["mall"] == data["mall"]

    def test_brand_mapped(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["payload"]["brand"] == data["brand"]

    def test_lease_code_mapped_to_lease(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["payload"]["lease"] == data["lease_code"]

    def test_title_mapped_top_level_and_inner(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["title"] == data["title"]
        assert result["payload"]["title"] == data["title"]

    def test_start_date_mapped(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["payload"]["startDate"] == "2025-03-01T06:00:00.000Z"

    def test_end_date_mapped(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["payload"]["endDate"] == "2025-09-01T06:00:00.000Z"

    def test_comments_mapped(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["payload"]["comments"] == data["comments"]

    def test_description_mapped(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["payload"]["description"] == data["description"]

    def test_inspection_done_by_mapped_to_both_keys(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["payload"]["inspectionDoneBy"] == data["inspection_done_by"]
        assert result["payload"]["inspection_done_by"] == data["inspection_done_by"]

    def test_lease_brand_mall_mapped(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["payload"]["lease_brand_mall"] == data["lease_brand_mall"]

    def test_unit_codes_mapped(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["payload"]["unit_codes"] == data["unit_codes"]

    def test_contracted_area_mapped(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["payload"]["contracted_area"] == data["contracted_area"]

    def test_city_mapped(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["payload"]["city"] == data["city"]

    def test_brand_id_mapped(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["payload"]["brand_id"] == data["brand_id"]

    def test_tenant_profile_id_mapped_top_and_inner(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["tenant_profile_id"] == data["tenant_profile_id"]
        assert result["payload"]["tenant_profile_id"] == data["tenant_profile_id"]

    def test_contract_id_mapped(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["payload"]["contract_id"] == data["contract_id"]

    def test_property_id_mapped_top_and_inner(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["property_id"] == data["property_id"]
        assert result["payload"]["property_id"] == data["property_id"]

    def test_lease_code_mapped_top_level(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["lease_code"] == data["lease_code"]

    def test_lease_id_mapped_top_level(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["lease_id"] == data["lease_id"]

    def test_company_name_is_str_of_tenant_profile_id(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["payload"]["company_name"] == str(data["tenant_profile_id"])

    def test_company_name_coerces_int_to_str(self) -> None:
        data = _full_data()
        data["tenant_profile_id"] = 12345
        result = build_create_handover_payload(data)
        assert result["payload"]["company_name"] == "12345"
        assert isinstance(result["payload"]["company_name"], str)

    def test_notes_mapped_when_present(self) -> None:
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["payload"]["notes"] == data["notes"]

    def test_startDateLT_mapped_when_present(self) -> None:
        data = _full_data()
        data["startDateLT"] = "01/03/2025 2:30 PM"
        result = build_create_handover_payload(data)
        assert result["payload"]["startDateLT"] == "01/03/2025 2:30 PM"
        assert result["payload"]["startDate"] == "2025-03-01T11:30:00.000Z"

    def test_endDateLT_mapped_when_present(self) -> None:
        data = _full_data()
        data["endDateLT"] = "01/09/2025 2:30 PM"
        result = build_create_handover_payload(data)
        assert result["payload"]["endDateLT"] == "01/09/2025 2:30 PM"
        assert result["payload"]["endDate"] == "2025-09-01T11:30:00.000Z"


# ===========================================================================
# TestBuildCreateHandoverPayloadDefaults
# ===========================================================================


class TestBuildCreateHandoverPayloadDefaults:
    def test_notes_defaults_to_empty_string_when_absent(self) -> None:
        data = _full_data()
        del data["notes"]
        result = build_create_handover_payload(data)
        assert result["payload"]["notes"] == ""

    def test_startDateLT_derived_from_date_when_absent(self) -> None:
        data = _full_data()
        del data["startDateLT"]
        result = build_create_handover_payload(data)
        assert result["payload"]["startDateLT"] == "01/03/2025 9:00 AM"
        assert result["payload"]["startDate"] == "2025-03-01T06:00:00.000Z"

    def test_endDateLT_derived_from_date_when_absent(self) -> None:
        data = _full_data()
        del data["endDateLT"]
        result = build_create_handover_payload(data)
        assert result["payload"]["endDateLT"] == "01/09/2025 9:00 AM"
        assert result["payload"]["endDate"] == "2025-09-01T06:00:00.000Z"

    def test_invalid_stored_lt_is_replaced_from_date(self) -> None:
        """Legacy ISO-offset LT values are not portal display format — regenerate."""
        data = _full_data()
        result = build_create_handover_payload(data)
        assert result["payload"]["startDateLT"] == "01/03/2025 9:00 AM"
        assert result["payload"]["endDateLT"] == "01/09/2025 9:00 AM"

    def test_all_optional_fields_absent_still_builds(self) -> None:
        data = _full_data()
        for key in ("notes", "startDateLT", "endDateLT"):
            data.pop(key, None)
        result = build_create_handover_payload(data)
        assert isinstance(result, dict)


# ===========================================================================
# TestBuildCreateHandoverPayloadValidation
# ===========================================================================


class TestBuildCreateHandoverPayloadValidation:
    def test_raises_value_error_on_empty_data(self) -> None:
        with pytest.raises(ValueError):
            build_create_handover_payload({})

    def test_raises_value_error_when_mall_missing(self) -> None:
        data = _full_data()
        del data["mall"]
        with pytest.raises(ValueError, match="mall"):
            build_create_handover_payload(data)

    def test_raises_value_error_when_title_empty_string(self) -> None:
        data = _full_data()
        data["title"] = ""
        with pytest.raises(ValueError, match="title"):
            build_create_handover_payload(data)

    def test_raises_value_error_when_unit_codes_empty_list(self) -> None:
        data = _full_data()
        data["unit_codes"] = []
        with pytest.raises(ValueError, match="unit_codes"):
            build_create_handover_payload(data)

    def test_raises_value_error_when_lease_id_none(self) -> None:
        data = _full_data()
        data["lease_id"] = None
        with pytest.raises(ValueError, match="lease_id"):
            build_create_handover_payload(data)

    def test_error_message_lists_missing_keys(self) -> None:
        data = _full_data()
        del data["brand"]
        del data["city"]
        with pytest.raises(ValueError) as exc_info:
            build_create_handover_payload(data)
        msg = str(exc_info.value)
        assert "brand" in msg
        assert "city" in msg

    @pytest.mark.parametrize("key", sorted(_REQUIRED_DATA_KEYS))
    def test_each_required_key_triggers_error_when_missing(self, key: str) -> None:
        data = _full_data()
        del data[key]
        with pytest.raises(ValueError):
            build_create_handover_payload(data)

    def test_integer_zero_contracted_area_does_not_raise(self) -> None:
        """contracted_area = 0 is valid — must not be treated as missing."""
        data = _full_data()
        data["contracted_area"] = 0
        result = build_create_handover_payload(data)
        assert result["payload"]["contracted_area"] == 0

    def test_false_value_does_not_raise(self) -> None:
        """False is a valid value — must not be treated as missing."""
        data = _full_data()
        data["contracted_area"] = False
        result = build_create_handover_payload(data)
        assert result["payload"]["contracted_area"] is False


# ===========================================================================
# TestBuildCreateHandoverPayloadConstants
# ===========================================================================


class TestBuildCreateHandoverPayloadConstants:
    def test_service_category_is_fit_out_and_handover(self) -> None:
        result = build_create_handover_payload(_full_data())
        assert result["service_category"] == "FIT_OUT_AND_HANDOVER"

    def test_sub_category_is_handover(self) -> None:
        result = build_create_handover_payload(_full_data())
        assert result["sub_category"] == "HANDOVER"

    def test_service_request_id_is_empty_for_new_creation(self) -> None:
        result = build_create_handover_payload(_full_data())
        assert result["service_request_id"] == ""

    def test_attachments_always_empty_string(self) -> None:
        result = build_create_handover_payload(_full_data())
        assert result["payload"]["attachments"] == ""

    def test_guideline_link_always_empty_string(self) -> None:
        result = build_create_handover_payload(_full_data())
        assert result["payload"]["guideLineLink"] == ""

    def test_already_handovered_is_initiate_handover(self) -> None:
        """Fix 1: inner payload must carry already_handovered='initiate_handover'."""
        result = build_create_handover_payload(_full_data())
        assert result["payload"]["already_handovered"] == "initiate_handover"

    def test_fm_approve_no_service_category_or_sr_id_at_top(self) -> None:
        """Fix 3: minimal FM approve PATCH must NOT include service_category or service_request_id."""
        data = {
            "unit_readiness_date": "2026-07-01",
            "expected_handover_date": "2026-07-08",
        }
        refs = {
            "sr_id": "SR-100",
            "create_payload": {
                "payload": {
                    "mall": "M", "brand": "B", "lease": "L",
                    "title": "T", "tenant_profile_id": 1, "property_id": 2,
                },
                "lease_id": "LID",
            },
            "uploaded_documents": [],
            "sr_operations": [],
        }
        payload = build_fm_approve_payload(data, refs)
        assert "service_category" not in payload
        assert "service_request_id" not in payload
        assert payload["status"] == "APPROVED"


# ===========================================================================
# TestBuildFmApprovePayload
# ===========================================================================


class TestBuildFmApprovePayload:
    def _backend_refs(self, **kwargs: Any) -> dict[str, Any]:
        base = {
            "sr_id": "11714",
            "create_payload": {
                "payload": {
                    "mall": "Riyadh Park",
                    "brand": "Nike",
                    "lease": "LC-001",
                    "title": "Handover SR",
                    "tenant_profile_id": "TP-001",
                    "property_id": "PROP-001",
                },
                "lease_id": "LEASE-001",
            },
            "uploaded_documents": ["doc-1"],
            "sr_operations": [{"assigned_role": "FM_MANAGER", "status": "IN_PROGRESS"}],
        }
        base.update(kwargs)
        return base

    def test_inner_current_sr_status_stays_in_process(self) -> None:
        data = {
            "unit_readiness_date": "01/07/2026",
            "expected_handover_date": "02/07/2026",
        }
        payload = build_fm_approve_payload(data, self._backend_refs(), comment="ok")
        assert payload["status"] == "APPROVED"
        assert payload["payload"]["current_sr_status"] == "IN_PROCESS"
        assert payload["payload"]["mall"] == "Riyadh Park"
        assert payload["payload"]["lease"] == "LC-001"
        assert "lease_code" not in payload
        assert "lease_id" not in payload
        assert payload["user_action"] is None
        assert payload["comment"] == "ok"

    def test_fallback_from_collected_when_create_payload_empty(self) -> None:
        data = {
            "mall": "Mall X",
            "brand": "Brand Y",
            "lease_code": "T999",
            "title": "Open existing",
            "unit_readiness_date": "01/07/2026",
            "expected_handover_date": "02/07/2026",
            "tenant_profile_id": 10,
            "property_id": 20,
        }
        refs = {
            "sr_id": "11714",
            "create_payload": {},
            "uploaded_documents": [],
            "sr_operations": [],
        }
        payload = build_fm_approve_payload(data, refs)
        assert payload["payload"]["mall"] == "Mall X"
        assert payload["payload"]["lease"] == "T999"
        assert payload["payload"]["current_sr_status"] == "IN_PROCESS"
        assert payload["status"] == "APPROVED"

    def test_save_progress_still_includes_lease_top_level(self) -> None:
        data = {
            "unit_readiness_date": "01/07/2026",
            "expected_handover_date": "02/07/2026",
        }
        payload = build_fm_review_payload(data, self._backend_refs())
        assert payload["status"] == "IN_PROCESS"
        assert payload["lease_code"] == "LC-001"

    def test_fm_writes_comments_and_comment(self) -> None:
        data = {
            "unit_readiness_date": "2026-07-01",
            "expected_handover_date": "2026-07-08",
            "comments": "Site ready",
        }
        payload = build_fm_review_payload(data, self._backend_refs())
        assert payload["payload"]["comments"] == "Site ready"
        assert payload["payload"]["comment"] == "Site ready"

    def test_fm_dates_output_in_iso_yyyy_mm_dd(self) -> None:
        """Fix 5: FM review payload must emit dates as YYYY-MM-DD, not DD/MM/YYYY."""
        data = {
            "unit_readiness_date": "2026-07-01",
            "expected_handover_date": "2026-07-08",
        }
        payload = build_fm_review_payload(data, self._backend_refs())
        assert payload["payload"]["unit_readiness_date"] == "2026-07-01"
        assert payload["payload"]["expected_handover_date"] == "2026-07-08"

    def test_fm_dates_normalised_from_dd_mm_yyyy_to_iso(self) -> None:
        """Fix 5: DD/MM/YYYY input dates must also be normalised to YYYY-MM-DD."""
        data = {
            "unit_readiness_date": "01/07/2026",
            "expected_handover_date": "08/07/2026",
        }
        payload = build_fm_review_payload(data, self._backend_refs())
        assert payload["payload"]["unit_readiness_date"] == "2026-07-01"
        assert payload["payload"]["expected_handover_date"] == "2026-07-08"


# ===========================================================================
# TestBuildRddPayloadsPreserveAndMirror
# ===========================================================================


class TestBuildRddPayloadsPreserveAndMirror:
    def test_rdd_report_mirrors_dates_at_payload_root(self) -> None:
        from app.agents.services.payload_builder_service import build_rdd_report_payload

        data = {
            "guideLineLink": "https://example.com/g",
            "actual_handover_date": "2026-08-20",
            "fitout_start_date": "2026-08-30",
            "fitout_end_date": "2026-09-10",
            "trading_date": "2026-09-18",
            "document_status": "APPROVED",
            "comments": "RDD notes",
            "unit_readiness_date": "2026-08-01",
            "expected_handover_date": "2026-08-08",
        }
        refs = {
            "sr_id": "sr-1",
            "create_payload": {
                "payload": {
                    "mall": "Mall",
                    "brand": "Brand",
                    "lease": "L1",
                    "title": "T",
                    "tenant_profile_id": 1,
                    "property_id": 2,
                },
                "lease_id": "LEASE-1",
            },
            "uploaded_documents": ["fm-1"],
            "rdd_document_id": "rdd-1",
        }
        payload = build_rdd_report_payload(data, refs)
        inner = payload["payload"]
        assert inner["actual_handover_date"] == "20/08/2026"
        assert inner["fitout_start_date"] == "30/08/2026"
        assert inner["fitout_end_date"] == "10/09/2026"
        assert inner["trading_date"] == "18/09/2026"
        assert inner["unit_readiness_date"] == "01/08/2026"
        assert inner["comments"] == "RDD notes"
        assert inner["comment"] == "RDD notes"
        assert inner["guideLineLink"] == "https://example.com/g"
        rdd_entry = next(e for e in inner["document_status_map"] if e["id"] == "rdd-1")
        assert rdd_entry["actual_handover_date"] == "20/08/2026"

    def test_rdd_approve_preserves_report_fields_from_rdd_payload(self) -> None:
        from app.agents.services.payload_builder_service import build_rdd_approve_payload

        refs = {
            "sr_id": "sr-1",
            "create_payload": {
                "payload": {
                    "mall": "Mall",
                    "title": "Create only",
                    "tenant_profile_id": 1,
                    "property_id": 2,
                }
            },
            "rdd_payload": {
                "payload": {
                    "mall": "Mall",
                    "title": "After report",
                    "guideLineLink": "https://example.com/g",
                    "actual_handover_date": "20/08/2026",
                    "fitout_start_date": "30/08/2026",
                    "trading_date": "18/09/2026",
                    "document_status_map": [
                        {
                            "id": "rdd-1",
                            "document_status": "APPROVED",
                            "actual_handover_date": "20/08/2026",
                        }
                    ],
                    "documents_ids": ["fm-1", "rdd-1"],
                    "tenant_profile_id": 1,
                    "property_id": 2,
                }
            },
        }
        payload = build_rdd_approve_payload(refs, comment="Final")
        inner = payload["payload"]
        assert inner["guideLineLink"] == "https://example.com/g"
        assert inner["actual_handover_date"] == "20/08/2026"
        assert inner["documents_ids"] == ["fm-1", "rdd-1"]
        assert inner["document_status_map"][0]["id"] == "rdd-1"
        assert inner["comments"] == "Final"
        assert inner["current_sr_status"] == "REPORT_SUBMITTED"

    def test_rdd_report_attaches_sr_operations_and_current_sr_status(self) -> None:
        """Fix 6: build_rdd_report_payload must explicitly set sr_operations and current_sr_status."""
        from app.agents.services.payload_builder_service import build_rdd_report_payload

        data = {
            "guideLineLink": "https://example.com/g",
            "actual_handover_date": "2026-08-20",
            "fitout_start_date": "2026-08-30",
            "fitout_end_date": "2026-09-10",
            "trading_date": "2026-09-18",
            "document_status": "APPROVED",
            "comments": "",
            "unit_readiness_date": "2026-08-01",
            "expected_handover_date": "2026-08-08",
        }
        sr_ops = [{"assigned_role": "DD_ENGINEER", "status": "IN_PROGRESS"}]
        refs = {
            "sr_id": "sr-2",
            "create_payload": {
                "payload": {
                    "mall": "Mall",
                    "brand": "Brand",
                    "lease": "L2",
                    "title": "T2",
                    "tenant_profile_id": 10,
                    "property_id": 20,
                },
                "lease_id": "LEASE-2",
            },
            "uploaded_documents": ["fm-doc"],
            "rdd_document_id": "rdd-doc",
            "sr_operations": sr_ops,
        }
        payload = build_rdd_report_payload(data, refs)
        inner = payload["payload"]
        assert inner["sr_operations"] == sr_ops
        assert "current_sr_status" in inner  # present (Fix 6 ensures it is set explicitly)

    def test_rdd_approve_minimal_patch_no_extra_keys(self) -> None:
        """Fix 4: build_rdd_approve_payload must NOT include service_category, lease_code, lease_id."""
        from app.agents.services.payload_builder_service import build_rdd_approve_payload

        refs = {
            "sr_id": "sr-3",
            "create_payload": {
                "payload": {
                    "mall": "Mall", "title": "T3",
                    "tenant_profile_id": 1, "property_id": 2,
                }
            },
            "rdd_payload": {
                "payload": {
                    "mall": "Mall", "title": "T3",
                    "guideLineLink": "https://g",
                    "actual_handover_date": "20/08/2026",
                    "fitout_start_date": "30/08/2026",
                    "trading_date": "18/09/2026",
                    "document_status_map": [],
                    "documents_ids": [],
                    "tenant_profile_id": 1,
                    "property_id": 2,
                }
            },
        }
        payload = build_rdd_approve_payload(refs, comment="Done")
        assert "service_category" not in payload
        assert "lease_code" not in payload
        assert "lease_id" not in payload
        assert "service_request_id" not in payload
        assert payload["status"] == "APPROVED"
        # sub_category is intentionally kept for platform routing
        assert payload["sub_category"] == "HANDOVER"

    def test_rdd_approve_enriches_document_status_map_with_dates(self) -> None:
        """Fix 4: document_status_map entries must include unit_readiness_date and expected_handover_date."""
        from app.agents.services.payload_builder_service import build_rdd_approve_payload

        refs = {
            "sr_id": "sr-4",
            "create_payload": {"payload": {"tenant_profile_id": 1, "property_id": 2}},
            "rdd_payload": {
                "payload": {
                    "tenant_profile_id": 1,
                    "property_id": 2,
                    "unit_readiness_date": "2026-08-01",
                    "expected_handover_date": "2026-08-08",
                    "document_status_map": [
                        {"id": "doc-1", "document_status": "APPROVED"},
                    ],
                    "documents_ids": ["doc-1"],
                    "actual_handover_date": "20/08/2026",
                    "fitout_start_date": "30/08/2026",
                    "trading_date": "18/09/2026",
                    "guideLineLink": "",
                    "mall": "M",
                    "title": "T",
                }
            },
        }
        payload = build_rdd_approve_payload(refs)
        entry = payload["payload"]["document_status_map"][0]
        assert "unit_readiness_date" in entry
        assert "expected_handover_date" in entry

    def test_snapshot_create_payload_from_platform(self) -> None:
        from app.agents.services.payload_builder_service import (
            snapshot_create_payload_from_platform,
        )

        refs: dict[str, Any] = {"sr_id": "sr-9", "create_payload": {"lease_id": "OLD"}}
        snapshot_create_payload_from_platform(
            refs,
            {
                "mall": "M",
                "title": "Fresh",
                "lease": "L9",
                "workflow_version": 2,
                "unit_readiness_date": "01/07/2026",
            },
            sr_id="sr-9",
        )
        assert refs["create_payload"]["payload"]["title"] == "Fresh"
        assert "workflow_version" not in refs["create_payload"]["payload"]
        assert refs["create_payload"]["lease_code"] == "L9"
        assert refs["create_payload"]["lease_id"] == "OLD"


# ===========================================================================
# TestPayloadBuilderServiceClass
# ===========================================================================


class TestPayloadBuilderServiceClass:
    _svc = PayloadBuilderService()

    def test_build_returns_dict_copy(self) -> None:
        draft = {"key": "value"}
        result = self._svc.build(draft)
        assert result == draft
        assert result is not draft

    def test_build_create_handover_payload_delegates(self) -> None:
        data = _full_data()
        result = self._svc.build_create_handover_payload(data)
        assert result == build_create_handover_payload(data)

    def test_build_create_handover_payload_raises_on_missing(self) -> None:
        with pytest.raises(ValueError):
            self._svc.build_create_handover_payload({})


# ===========================================================================
# TestPayloadBuilderNodeIntegration
# ===========================================================================


class TestPayloadBuilderNodeIntegration:
    @pytest.mark.asyncio
    async def test_node_stores_payload_in_backend_refs(self) -> None:
        from app.agents.graph.nodes.handover.payload_builder_node import payload_builder_node

        state: dict[str, Any] = {
            "collected_data": _full_data(),
            "workflow_stage": "CREATE_SR",
        }
        result = await payload_builder_node(state)
        assert "backend_refs" in result
        assert "create_payload" in result["backend_refs"]

    @pytest.mark.asyncio
    async def test_node_payload_has_correct_structure(self) -> None:
        from app.agents.graph.nodes.handover.payload_builder_node import payload_builder_node

        state: dict[str, Any] = {
            "collected_data": _full_data(),
            "workflow_stage": "CREATE_SR",
        }
        result = await payload_builder_node(state)
        create_payload = result["backend_refs"]["create_payload"]
        assert "payload" in create_payload
        assert create_payload["service_category"] == "FIT_OUT_AND_HANDOVER"
        assert create_payload["sub_category"] == "HANDOVER"
        assert create_payload["service_request_id"] == ""

    @pytest.mark.asyncio
    async def test_node_returns_failed_status_on_missing_keys(self) -> None:
        from app.agents.graph.nodes.handover.payload_builder_node import payload_builder_node

        state: dict[str, Any] = {
            "collected_data": {},
            "workflow_stage": "CREATE_SR",
        }
        result = await payload_builder_node(state)
        assert result.get("status") == "FAILED"

    @pytest.mark.asyncio
    async def test_node_skips_non_create_sr_stages(self) -> None:
        from app.agents.graph.nodes.handover.payload_builder_node import payload_builder_node

        state: dict[str, Any] = {
            "collected_data": _full_data(),
            "workflow_stage": "FM_REVIEW",
        }
        result = await payload_builder_node(state)
        assert result == {}

    @pytest.mark.asyncio
    async def test_node_preserves_existing_backend_refs(self) -> None:
        from app.agents.graph.nodes.handover.payload_builder_node import payload_builder_node

        state: dict[str, Any] = {
            "collected_data": _full_data(),
            "workflow_stage": "CREATE_SR",
            "backend_refs": {"existing_key": "existing_value"},
        }
        result = await payload_builder_node(state)
        assert result["backend_refs"]["existing_key"] == "existing_value"
        assert "create_payload" in result["backend_refs"]

    @pytest.mark.asyncio
    async def test_node_defaults_to_create_sr_when_stage_absent(self) -> None:
        from app.agents.graph.nodes.handover.payload_builder_node import payload_builder_node

        state: dict[str, Any] = {"collected_data": _full_data()}
        result = await payload_builder_node(state)
        assert "backend_refs" in result
        assert "create_payload" in result["backend_refs"]
