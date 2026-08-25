"""Offline contract eval for the RAG indexer (ticket 3547).

Checks the document contract the FAQ read path depends on — id shapes,
breadcrumb/mall-name prefixes, timing header, role_key derivation — without
any network or database.  Prints a PASS/FAIL table and exits 1 on failure.

Run:  PYTHONPATH=$(pwd) python -m tests.eval.indexer_eval
"""

from __future__ import annotations

import sys

from app.indexer.config import KEY_CONTACT_ROLES
from app.indexer.documents import INDEX_FIELDS, make_null_doc
from app.indexer.sources.event import build_event_docs
from app.indexer.sources.help_content import pending_chunks_from_row
from app.indexer.sources.key_contact import build_property_docs
from app.indexer.sources.mall_info import build_mall_docs
from app.indexer.transform.chunker import chunk_text
from app.indexer.transform.html_cleaner import clean_html
from app.indexer.transform.mall_information import parse_mall_information

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(condition), detail))


def main() -> int:
    # ── Document envelope ─────────────────────────────────────────────────
    null_doc = make_null_doc()
    check("null_doc has all 19 index fields", len(null_doc) == 19 == len(INDEX_FIELDS))
    check("null_doc all values None", all(v is None for v in null_doc.values()))

    # ── Transforms ────────────────────────────────────────────────────────
    check("clean_html strips script", "alert" not in clean_html("<script>alert(1)</script>hi"))
    check("chunk passthrough identity", chunk_text("short") == ["short"])
    check("chunk empty → []", chunk_text("  ") == [])

    sections = parse_mall_information(
        {"operations": {"working_hours": "10-23"}},
        "Tala Mall",
        total_gross_leasable_area=111864,
    )
    overview = next(s["content"] for s in sections if s["section"] == "overview")
    check("GLA column formatted", "Total Gross Leasable Area: 111,864 sqm" in overview)
    check(
        "timing header exact",
        "Opening hours / operating hours / timings / working hours / "
        "schedule for Tala Mall:" in overview,
    )
    check("timing merged into overview", [s["section"] for s in sections] == ["overview"])

    # ── help_content contract ─────────────────────────────────────────────
    help_docs = pending_chunks_from_row(
        {
            "help_content_id": 42,
            "title_en": "Create promotion",
            "title_ar": "",
            "description_en": "<p>Steps.</p>",
            "description_ar": "",
            "page_name_en": "Marketing Hub",
            "page_name_ar": "",
            "category_name_en": "Promotions",
            "category_name_ar": None,
            "subcategory_name_en": None,
            "subcategory_name_ar": None,
            "url_pattern": "/marketing",
        }
    )
    check("help id shape", help_docs[0]["id"] == "help_content_42_en_0")
    check(
        "help breadcrumb on body",
        help_docs[0]["content"].startswith("Marketing Hub > Promotions\n"),
    )
    check(
        "help breadcrumb on title",
        help_docs[0]["title"] == "Marketing Hub > Promotions — Create promotion",
    )
    check("help category = page name", help_docs[0]["category_name_en"] == "Marketing Hub")

    # ── mall_info contract ────────────────────────────────────────────────
    mall_docs = build_mall_docs(
        {
            "unique_property_id": 9,
            "marketing_name": "Tala Mall",
            "mall_information": {"region": "Western"},
            "mall_services": None,
            "total_gross_leasable_area": None,
        }
    )
    check("mall id shape", mall_docs[0]["id"] == "mall_info_9_overview")
    check("mall name prefix", mall_docs[0]["content"].startswith("Tala Mall\n"))

    # ── key_contact allowlist ─────────────────────────────────────────────
    def _contact(role, prop=1):
        return {"unique_property_id": prop, "role_name": role, "first_name": "A",
                "last_name": "B", "email": f"{role.lower()}@x", "phone": ""}

    check("exactly 7 allowlisted roles", len(KEY_CONTACT_ROLES) == 7,
          str(sorted(KEY_CONTACT_ROLES)))

    all_seven = build_property_docs(
        1, "M", json_manager_members=[],
        contact_rows=[_contact(r) for r in KEY_CONTACT_ROLES])
    check("all 7 roles produce a card", len(all_seven) == 7, str(len(all_seven)))

    ids = {d["id"] for d in all_seven}
    check("role_key from own group (leak fixed)",
          ids == {f"key_contact_1_{r}" for r in KEY_CONTACT_ROLES}, str(ids))

    for hidden in ("LEASING_MANAGER", "LEGAL", "COLLECTOR"):
        check(f"hidden role {hidden} never indexed",
              build_property_docs(1, "M", json_manager_members=[],
                                  contact_rows=[_contact(hidden)]) == [])

    check("REGIONAL_DD_ENGINEER excluded despite DD_ prefix",
          build_property_docs(1, "M", json_manager_members=[],
                              contact_rows=[_contact("REGIONAL_DD_ENGINEER")]) == [])

    check("role match is case-sensitive",
          build_property_docs(1, "M", json_manager_members=[],
                              contact_rows=[_contact("dd_engineer")]) == [])

    elec = build_property_docs(1, "M", json_manager_members=[],
                               contact_rows=[_contact("DD_ELEC_ENGINEER")])
    check("display label used, no trailing space",
          elec[0]["content"].startswith("RDD Electrical Engineer contacts for M:"),
          elec[0]["content"].splitlines()[0])

    merged = build_property_docs(
        3, "M",
        json_manager_members=[{"name": "S", "email": "s@x.com", "phone": ""}],
        contact_rows=[_contact("MALL_MANAGER", prop=3)])
    mm = [d for d in merged if d["id"] == "key_contact_3_MALL_MANAGER"]
    check("MALL_MANAGER sources merge, not collide", len(mm) == 1, str(len(mm)))
    check("merged card keeps both members",
          "S" in mm[0]["content"] and "A B" in mm[0]["content"])

    # ── event contract ────────────────────────────────────────────────────
    event_docs = build_event_docs(
        {
            "announcement_event_id": 7, "title_en": "Sale", "title_ar": "",
            "description_en": "x", "description_ar": "", "start_date": None,
            "end_date": None, "is_event": True, "is_announcement": False,
            "applies_to_all_malls": True, "deleted_at": None, "mall_name": None,
        }
    )
    check("event id shape", event_docs[0]["id"] == "event_7_en_0")
    check("event All Malls fallback", "Mall: All Malls" in event_docs[0]["content"])
    check(
        "deleted event builds nothing",
        build_event_docs({"announcement_event_id": 1, "deleted_at": "2026-01-01"}) == [],
    )

    # ── Report ────────────────────────────────────────────────────────────
    width = max(len(name) for name, _, _ in CHECKS) + 2
    failures = 0
    print(f"\n{'CHECK'.ljust(width)}RESULT")
    print("-" * (width + 6))
    for name, ok, detail in CHECKS:
        print(f"{name.ljust(width)}{'PASS' if ok else 'FAIL'}{('  ' + detail) if (detail and not ok) else ''}")
        failures += 0 if ok else 1
    print("-" * (width + 6))
    print(f"{len(CHECKS) - failures}/{len(CHECKS)} passed\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
