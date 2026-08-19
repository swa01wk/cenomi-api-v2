"""Pure document-builder tests: ids, prefixes, and the role_key fix."""

from __future__ import annotations

from app.indexer.documents import build_doc, make_null_doc
from app.indexer.sources.event import build_event_docs
from app.indexer.sources.help_content import pending_chunks_from_row
from app.indexer.sources.key_contact import (
    build_manager_doc,
    build_role_group_doc,
    group_role_rows,
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


class TestKeyContactBuilder:
    def test_split_manager_emails(self) -> None:
        assert split_manager_emails(" A@x.com , b@y.com,,") == ["a@x.com", "b@y.com"]
        assert split_manager_emails(None) == []

    def test_manager_doc(self) -> None:
        doc = build_manager_doc(
            3, "Nakheel Mall",
            [{"full_name": "Sara Ali", "email": "sara@x.com", "phone": "0501"}],
        )
        assert doc["id"] == "key_contact_3_MALL_MANAGER"
        lines = doc["content"].splitlines()
        assert lines[0] == "Mall Manager contacts for Nakheel Mall:"
        assert lines[1] == "- Sara Ali | sara@x.com | 0501"

    def test_manager_doc_omits_blank_email_phone(self) -> None:
        doc = build_manager_doc(3, "M", [{"full_name": "N", "email": "", "phone": ""}])
        assert doc["content"].splitlines()[1] == "- N"

    def test_role_key_from_own_group_not_leaked(self) -> None:
        """The legacy leaked-loop-var bug: each group must use ITS role."""
        rows = [
            {"unique_property_id": 1, "role_name": "FM_MANAGER",
             "role_name_en": "FM Manager", "role_name_ar": "",
             "first_name": "A", "last_name": "B", "email": "a@x", "phone": ""},
            {"unique_property_id": 1, "role_name": "AREA_MANAGER",
             "role_name_en": "Area Manager", "role_name_ar": "",
             "first_name": "C", "last_name": "D", "email": "c@x", "phone": ""},
        ]
        groups = group_role_rows(rows)
        docs = {
            build_role_group_doc(pid, "Mall", members)["id"]
            for (pid, _), members in groups.items()
        }
        assert docs == {"key_contact_1_FM_MANAGER", "key_contact_1_AREA_MANAGER"}

    def test_role_group_content_header(self) -> None:
        doc = build_role_group_doc(
            2, "Mall Of Arabia",
            [{"role_name": "FM_MANAGER", "role_name_en": "FM Manager",
              "first_name": "A", "last_name": "B", "email": "a@x", "phone": "05"}],
        )
        assert doc["content"].splitlines()[0] == "FM Manager contacts for Mall Of Arabia:"
        assert doc["content"].splitlines()[1] == "- A B | a@x | 05"

    def test_empty_members_returns_none(self) -> None:
        assert build_manager_doc(1, "M", []) is None
        assert build_role_group_doc(1, "M", []) is None


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
