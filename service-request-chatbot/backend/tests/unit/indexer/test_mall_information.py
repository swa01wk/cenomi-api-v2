"""parse_mall_information quirk tests — the legacy behaviours are contractual."""

from __future__ import annotations

from app.indexer.transform.mall_information import _fmt_value, parse_mall_information


def _sections(result):
    return {s["section"]: s["content"] for s in result}


class TestFmtValue:
    def test_none(self) -> None:
        assert _fmt_value(None) == ""

    def test_list(self) -> None:
        assert _fmt_value(["a", "b"]) == "a, b"

    def test_dict_with_default(self) -> None:
        assert (
            _fmt_value({"default": "Food court", "extra": "Cinema"})
            == "Food court; extra: Cinema"
        )

    def test_dict_only_default(self) -> None:
        assert _fmt_value({"default": "Food court"}) == "Food court"

    def test_dict_no_default(self) -> None:
        assert _fmt_value({"a": 1, "b": 2}) == "a: 1; b: 2"

    def test_scalar(self) -> None:
        assert _fmt_value(42) == "42"


class TestGLA:
    def test_missing_blob_gla_only_still_emits_overview(self) -> None:
        result = parse_mall_information(None, "Tala Mall", total_gross_leasable_area=111864)
        sections = _sections(result)
        assert "overview" in sections
        assert "Total Gross Leasable Area: 111,864 sqm" in sections["overview"]

    def test_gla_column_overrides_json_total_area(self) -> None:
        blob = {"overview": {"total_area": "999 stale sqm"}}
        result = parse_mall_information(blob, "Tala Mall", total_gross_leasable_area=50000)
        overview = _sections(result)["overview"]
        assert "Total Gross Leasable Area: 50,000 sqm" in overview
        assert "999 stale" not in overview

    def test_json_total_area_used_when_no_column(self) -> None:
        blob = {"overview": {"total_area": "88,000 sqm"}}
        overview = _sections(parse_mall_information(blob, "M"))["overview"]
        assert "Total Gross Leasable Area: 88,000 sqm" in overview


class TestTiming:
    HEADER = (
        "Opening hours / operating hours / timings / working hours / "
        "schedule for Tala Mall:"
    )

    def test_operations_working_hours_priority(self) -> None:
        blob = {
            "operations": {"working_hours": "10:00 - 23:00"},
            "MallTiming": [{"day_en": "Mon", "start_time": "09", "end_time": "22"}],
        }
        overview = _sections(parse_mall_information(blob, "Tala Mall"))["overview"]
        assert self.HEADER in overview
        assert "10:00 - 23:00" in overview
        assert "Mon" not in overview  # lower-priority shape must not render

    def test_mall_timing_array(self) -> None:
        blob = {
            "MallTiming": [
                {"day_en": "Weekdays", "start_time": "10:00", "end_time": "23:00"},
                {"dayen": "Friday", "start_time": "14:00", "end_time": "23:30"},
            ]
        }
        overview = _sections(parse_mall_information(blob, "Tala Mall"))["overview"]
        assert self.HEADER in overview
        assert "Weekdays: 10:00 - 23:00" in overview
        assert "Friday: 14:00 - 23:30" in overview

    def test_working_time_fallback(self) -> None:
        blob = {"working_time": "Daily 9-11"}
        overview = _sections(parse_mall_information(blob, "Tala Mall"))["overview"]
        assert self.HEADER in overview
        assert "Daily 9-11" in overview

    def test_timing_merged_not_separate_section(self) -> None:
        blob = {"working_time": "Daily 9-11"}
        result = parse_mall_information(blob, "Tala Mall")
        assert [s["section"] for s in result] == ["overview"]


class TestOverviewFields:
    def test_labelled_fields_and_region(self) -> None:
        blob = {
            "region": "Western",
            "overview": {
                "floors": 4,
                "parking": "5000 sqm",
                "mall_emergency_hotline": "966118252138",
                "emergency_assembly_points": ["Main gate 1", "Loading area 1"],
            },
        }
        overview = _sections(parse_mall_information(blob, "M"))["overview"]
        assert "Region: Western" in overview
        assert "Floors: 4" in overview
        assert "Parking: 5000 sqm" in overview
        assert "Emergency Hotline: 966118252138" in overview
        assert "Emergency Assembly Points: Main gate 1, Loading area 1" in overview

    def test_json_string_blob_accepted(self) -> None:
        overview = _sections(
            parse_mall_information('{"region": "Central"}', "M")
        )["overview"]
        assert "Region: Central" in overview


class TestServices:
    def test_services_from_column_when_json_lacks_key(self) -> None:
        column = {"ATM": [{"location": "Gate 2"}, {"name": "Food court"}]}
        result = parse_mall_information({}, "M", mall_services=column)
        services = _sections(result)["services"]
        assert "ATM: Gate 2, Food court" in services

    def test_json_services_win_over_column(self) -> None:
        blob = {"mall_services": {"WiFi": []}}
        result = parse_mall_information(blob, "M", mall_services={"ATM": []})
        services = _sections(result)["services"]
        assert "WiFi: available" in services
        assert "ATM" not in services

    def test_empty_entry_list_renders_available(self) -> None:
        result = parse_mall_information({"mall_services": {"Prayer Room": []}}, "M")
        assert "Prayer Room: available" in _sections(result)["services"]

    def test_list_form_single_line(self) -> None:
        result = parse_mall_information({"mall_services": ["WiFi", "ATM"]}, "M")
        assert _sections(result)["services"] == "Services: WiFi, ATM"


class TestSocial:
    def test_social_block(self) -> None:
        blob = {"Social": {"instagram": "https://ig/x", "x": "https://x/y"}}
        social = _sections(parse_mall_information(blob, "Tala Mall"))["social"]
        assert social.splitlines()[0] == "Social media links for Tala Mall:"
        assert "instagram: https://ig/x" in social
        assert "x: https://x/y" in social

    def test_no_social_no_section(self) -> None:
        result = parse_mall_information({"region": "W"}, "M")
        assert "social" not in _sections(result)


class TestEmpty:
    def test_totally_empty_returns_nothing(self) -> None:
        assert parse_mall_information(None, "M") == []
        assert parse_mall_information({}, "M") == []
