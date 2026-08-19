"""Pure document-builder tests: ids, prefixes, and the role_key fix."""

from __future__ import annotations

from app.indexer.documents import build_doc, make_null_doc
from app.indexer.sources.event import build_event_docs
from app.indexer.sources.help_content import pending_chunks_from_row
from app.indexer.sources.key_contact import (
    build_contact_doc,
    build_property_docs,
    dedupe_members,
    group_contacts_by_role,
    member_from_contact_row,
    split_manager_emails,
)
from app.indexer.sources.mall_info import build_mall_docs


class TestDocuments:
    def test_make_null_doc_has_every_field_none(self) -> None:
        doc = make_null_doc()
        assert doc["id"] is None and doc["content_vector"] is None
        assert len(doc) == 19

    def test_build_doc_unknown_key_raises(self) -> None:
        import pytest
        with pytest.raises(ValueError):
            build_doc(nonexistent_field=1)

    def test_build_doc_nulls_unset_fields(self) -> None:
        doc = build_doc(id="x", title="t")
        assert doc["id"] == "x" and doc["title"] == "t"
        assert doc["mall_name"] is None and doc["page_number"] is None


def _help_row(**over):
    row = {
        "help_content_id": 42,
        "help_node_id": 7,
        "node_type": "SUBCATEGORY",
        "title_en": "Create promotion",
        "title_ar": "",
        "description_en": "<p>Steps to create a promotion.</p>",
        "description_ar": "",
        "page_name_en": "Marketing Hub",
        "page_name_ar": "مركز التسويق",
        "category_name_en": "Promotions",
        "category_name_ar": None,
        "subcategory_name_en": "Offers",
        "subcategory_name_ar": None,
        "url_pattern": "/marketing/promotions",
    }
    row.update(over)
    return row


class TestHelpContentBuilder:
    def test_exact_id_shape(self) -> None:
        docs = pending_chunks_from_row(_help_row())
        assert docs[0]["id"] == "help_content_42_en_0"

    def test_breadcrumb_on_body_and_title(self) -> None:
        doc = pending_chunks_from_row(_help_row())[0]
        breadcrumb = "Marketing Hub > Promotions > Offers"
        assert doc["content"].startswith(f"{breadcrumb}\nCreate promotion\n")
        assert doc["title"] == f"{breadcrumb} — Create promotion"

    def test_category_field_is_page_name(self) -> None:
        doc = pending_chunks_from_row(_help_row())[0]
        assert doc["category_name_en"] == "Marketing Hub"
        assert doc["category_name_ar"] == "مركز التسويق"

    def test_empty_lang_skipped(self) -> None:
        docs = pending_chunks_from_row(_help_row())
        assert all(d["language"] == "en" for d in docs)  # ar description empty

    def test_null_optional_fields_present(self) -> None:
        doc = pending_chunks_from_row(_help_row())[0]
        assert doc["mall_name"] is None
        assert doc["announcement_event_id"] is None

    def test_both_langs_when_present(self) -> None:
        docs = pending_chunks_from_row(
            _help_row(description_ar="<p>خطوات إنشاء عرض ترويجي.</p>", title_ar="إنشاء عرض")
        )
        langs = {d["language"] for d in docs}
        assert langs == {"en", "ar"}
        ar = [d for d in docs if d["language"] == "ar"][0]
        assert ar["id"] == "help_content_42_ar_0"


class TestMallInfoBuilder:
    def test_id_and_prefix(self) -> None:
        row = {
            "unique_property_id": 9,
            "marketing_name": "Tala Mall",
            "mall_information": {"region": "Western"},
            "mall_services": None,
            "total_gross_leasable_area": None,
        }
        docs = build_mall_docs(row)
        assert docs[0]["id"] == "mall_info_9_overview"
        assert docs[0]["content"].startswith("Tala Mall\n")
        assert docs[0]["title"] == "Tala Mall — Overview"
        assert docs[0]["unique_property_id"] == 9
        assert docs[0]["chunk_index"] == 0

    def test_mall_name_fallback(self) -> None:
        row = {
            "unique_property_id": 5,
            "marketing_name": None,
            "mall_information": {"region": "E"},
            "mall_services": None,
            "total_gross_leasable_area": None,
        }
        docs = build_mall_docs(row)
        assert docs[0]["content"].startswith("Property 5\n")


class TestKeyContactAllowlist:
    """Only the seven allowlisted roles may ever become documents."""

    def _row(self, role_name: str, prop: int = 1, **over) -> dict:
        row = {
            "unique_property_id": prop, "role_name": role_name,
            "first_name": "A", "last_name": "B",
            "email": f"{role_name.lower()}@x.com", "phone": "0500",
        }
        row.update(over)
        return row

    def test_all_seven_roles_indexed(self) -> None:
        rows = [
            self._row(r) for r in (
                "MALL_MANAGER", "OPERATIONS", "SALES_MANAGER", "DD_ENGINEER",
                "DD_ARCH", "DD_MEP", "DD_ELEC_ENGINEER",
            )
        ]
        docs = build_property_docs(
            1, "Tala Mall", json_manager_members=[], contact_rows=rows
        )
        assert len(docs) == 7

    def test_non_allowlisted_roles_dropped(self) -> None:
        rows = [
            self._row("MALL_ADMIN"), self._row("FM_MANAGER"),
            self._row("MARKETING"), self._row("AREA_MANAGER"),
            self._row("REGIONAL_HEAD"), self._row("CUSTOMER_RELATIONS_TEAM"),
        ]
        assert build_property_docs(
            1, "M", json_manager_members=[], contact_rows=rows
        ) == []

    def test_hidden_roles_never_surface(self) -> None:
        """LEASING_MANAGER / LEGAL / COLLECTOR must never be indexed."""
        for role in ("LEASING_MANAGER", "LEGAL", "COLLECTOR"):
            docs = build_property_docs(
                1, "M", json_manager_members=[], contact_rows=[self._row(role)]
            )
            assert docs == [], f"{role} leaked into the index"

    def test_regional_dd_engineer_excluded_despite_dd_prefix(self) -> None:
        docs = build_property_docs(
            1, "M", json_manager_members=[],
            contact_rows=[self._row("REGIONAL_DD_ENGINEER")],
        )
        assert docs == []

    def test_role_match_is_case_sensitive(self) -> None:
        for variant in ("dd_engineer", "Dd_Engineer", "DD_Engineer"):
            docs = build_property_docs(
                1, "M", json_manager_members=[], contact_rows=[self._row(variant)]
            )
            assert docs == [], f"{variant} matched but should not"

    def test_no_allowlisted_contact_yields_no_document(self) -> None:
        """Never substitute a non-allowlisted role as a fallback."""
        rows = [self._row("LEASING_MANAGER"), self._row("LEGAL")]
        assert build_property_docs(
            1, "M", json_manager_members=[], contact_rows=rows
        ) == []


class TestKeyContactPresentation:
    def test_display_label_not_raw_role_name(self) -> None:
        doc = build_contact_doc(
            2, "Mall Of Arabia", "DD_ENGINEER",
            [{"name": "A B", "email": "a@x", "phone": "05"}],
        )
        assert doc["content"].splitlines()[0] == (
            "RDD Project Manager contacts for Mall Of Arabia:"
        )
        assert doc["title"] == "RDD Project Manager — Mall Of Arabia"
        assert "DD_ENGINEER" not in doc["content"]

    def test_label_comes_from_map_not_db_column(self) -> None:
        """The DB label carries a trailing space; ours must not."""
        rows = [{
            "unique_property_id": 1, "role_name": "DD_ELEC_ENGINEER",
            "role_name_en": "RDD Electrical Engineer ",  # dirty source value
            "first_name": "A", "last_name": "B", "email": "a@x", "phone": "",
        }]
        docs = build_property_docs(
            1, "M", json_manager_members=[], contact_rows=rows
        )
        assert docs[0]["content"].startswith("RDD Electrical Engineer contacts for M:")
        assert "Engineer  contacts" not in docs[0]["content"]

    def test_id_uses_role_name(self) -> None:
        doc = build_contact_doc(
            9, "M", "DD_MEP", [{"name": "N", "email": "n@x", "phone": ""}]
        )
        assert doc["id"] == "key_contact_9_DD_MEP"

    def test_role_key_from_own_group_not_leaked(self) -> None:
        """Legacy leaked-loop-var bug: each group must use ITS own role."""
        rows = [
            {"unique_property_id": 1, "role_name": "DD_ARCH", "first_name": "A",
             "last_name": "B", "email": "a@x", "phone": ""},
            {"unique_property_id": 1, "role_name": "OPERATIONS", "first_name": "C",
             "last_name": "D", "email": "c@x", "phone": ""},
        ]
        ids = {d["id"] for d in build_property_docs(
            1, "M", json_manager_members=[], contact_rows=rows)}
        assert ids == {"key_contact_1_DD_ARCH", "key_contact_1_OPERATIONS"}

    def test_members_omit_blank_fields(self) -> None:
        doc = build_contact_doc(
            1, "M", "OPERATIONS", [{"name": "N", "email": "", "phone": ""}]
        )
        assert doc["content"].splitlines()[1] == "- N"

    def test_empty_members_returns_none(self) -> None:
        assert build_contact_doc(1, "M", "OPERATIONS", []) is None

    def test_disallowed_role_returns_none_directly(self) -> None:
        assert build_contact_doc(
            1, "M", "LEGAL", [{"name": "N", "email": "n@x", "phone": ""}]
        ) is None


class TestMallManagerMerge:
    """MALL_MANAGER has two sources sharing one document id — they must merge."""

    def test_json_and_tenant_contacts_merge_into_one_doc(self) -> None:
        json_members = [{"name": "Sara Ali", "email": "sara@x.com", "phone": "0501"}]
        rows = [{
            "unique_property_id": 3, "role_name": "MALL_MANAGER",
            "first_name": "Omar", "last_name": "Zaid",
            "email": "omar@x.com", "phone": "0502",
        }]
        docs = build_property_docs(
            3, "Nakheel Mall", json_manager_members=json_members, contact_rows=rows
        )
        managers = [d for d in docs if d["id"] == "key_contact_3_MALL_MANAGER"]
        assert len(managers) == 1, "collision — one source overwrote the other"
        content = managers[0]["content"]
        assert "Sara Ali" in content and "Omar Zaid" in content

    def test_duplicate_email_deduped_across_sources(self) -> None:
        json_members = [{"name": "Sara Ali", "email": "sara@x.com", "phone": "0501"}]
        rows = [{
            "unique_property_id": 3, "role_name": "MALL_MANAGER",
            "first_name": "Sara", "last_name": "Ali",
            "email": "SARA@x.com", "phone": "0501",
        }]
        docs = build_property_docs(
            3, "M", json_manager_members=json_members, contact_rows=rows
        )
        body = docs[0]["content"].splitlines()
        assert len([ln for ln in body if ln.startswith("- ")]) == 1

    def test_json_only_still_works(self) -> None:
        docs = build_property_docs(
            3, "M",
            json_manager_members=[{"name": "S", "email": "s@x", "phone": ""}],
            contact_rows=[],
        )
        assert docs[0]["id"] == "key_contact_3_MALL_MANAGER"
        assert docs[0]["content"].startswith("Mall Manager contacts for M:")


class TestKeyContactHelpers:
    def test_split_manager_emails(self) -> None:
        assert split_manager_emails(" A@x.com , b@y.com,,") == ["a@x.com", "b@y.com"]
        assert split_manager_emails(None) == []

    def test_group_contacts_by_role_keys_on_role_name(self) -> None:
        rows = [
            {"unique_property_id": 1, "role_name": "DD_MEP"},
            {"unique_property_id": 1, "role_name": "LEGAL"},
        ]
        assert set(group_contacts_by_role(rows)) == {(1, "DD_MEP")}

    def test_dedupe_keeps_members_without_email(self) -> None:
        members = [
            {"name": "A", "email": "", "phone": ""},
            {"name": "B", "email": "", "phone": ""},
        ]
        assert len(dedupe_members(members)) == 2

    def test_member_from_contact_row(self) -> None:
        m = member_from_contact_row(
            {"first_name": "A", "last_name": "B", "email": "a@x", "phone": "05"}
        )
        assert m == {"name": "A B", "email": "a@x", "phone": "05"}


def _event_row(**over):
    row = {
        "announcement_event_id": 77,
        "title_en": "White Friday Offers",
        "title_ar": "",
        "description_en": "<p>Upload your offers.</p>",
        "description_ar": "",
        "start_date": "2026-11-20",
        "end_date": "2026-11-27",
        "is_event": True,
        "is_announcement": False,
        "applies_to_all_malls": False,
        "deleted_at": None,
        "mall_name": "Mall of Arabia",
    }
    row.update(over)
    return row


class TestEventBuilder:
    def test_id_and_content(self) -> None:
        docs = build_event_docs(_event_row())
        assert docs[0]["id"] == "event_77_en_0"
        lines = docs[0]["content"].splitlines()
        assert lines[0] == "Event: White Friday Offers"
        assert lines[1] == "Date: 2026-11-20 to 2026-11-27"
        assert lines[2] == "Mall: Mall of Arabia"
        assert "Upload your offers." in lines[3]

    def test_skip_empty_title(self) -> None:
        assert build_event_docs(_event_row(title_en="", title_ar="")) == []

    def test_all_malls_fallback(self) -> None:
        docs = build_event_docs(
            _event_row(mall_name=None, applies_to_all_malls=True)
        )
        assert "Mall: All Malls" in docs[0]["content"]

    def test_no_mall_no_flag_blank(self) -> None:
        docs = build_event_docs(_event_row(mall_name=None, applies_to_all_malls=False))
        assert "Mall: " in docs[0]["content"]

    def test_type_lines(self) -> None:
        both = build_event_docs(_event_row(is_event=True, is_announcement=True))
        assert both[0]["content"].startswith("Event, Announcement:")
        ann = build_event_docs(_event_row(is_event=False, is_announcement=True))
        assert ann[0]["content"].startswith("Announcement:")
        neither = build_event_docs(_event_row(is_event=False, is_announcement=False))
        assert neither[0]["content"].startswith("Event:")

    def test_date_line_start_only(self) -> None:
        docs = build_event_docs(_event_row(end_date=None))
        assert "Date: 2026-11-20\n" in docs[0]["content"]

    def test_date_line_omitted(self) -> None:
        docs = build_event_docs(_event_row(start_date=None, end_date=None))
        assert "Date:" not in docs[0]["content"]

    def test_deleted_rows_build_nothing(self) -> None:
        assert build_event_docs(_event_row(deleted_at="2026-08-01")) == []
