"""Regression tests for AzureSearchRepository._deduplicate interleaving.

Guards the bug where only ONE key_contact card could reach the LLM: the merge
interleaved its first pass but then drained groups one at a time, so the
earlier source types (help_content, mall_info) consumed every remaining slot
under the top_k cap. A question like "who handles operations at X" was then
answered from whichever single contact card ranked first — the Mall Manager —
silently substituting the wrong role.
"""

from __future__ import annotations

from app.integrations.search.azure_search import AzureSearchRepository
from app.integrations.search.base import SearchResult


def _r(source_type: str, title: str) -> SearchResult:
    return SearchResult(source_type=source_type, title=title, content="c")


class TestDeduplicateInterleaving:
    def _grouped(self) -> list[list[SearchResult]]:
        return [
            [_r("help_content", f"h{i}") for i in range(1, 4)],
            [_r("mall_info", f"m{i}") for i in range(1, 5)],
            [_r("key_contact", f"k{i}") for i in range(1, 4)],
            [_r("event", "e1"), _r("event", "e2")],
        ]

    def test_second_card_of_each_type_beats_third_of_any(self) -> None:
        merged = AzureSearchRepository._deduplicate(self._grouped())
        assert [x.title for x in merged[:8]] == [
            "h1", "m1", "k1", "e1", "h2", "m2", "k2", "e2",
        ]

    def test_two_key_contacts_survive_default_top_k(self) -> None:
        """The actual regression: >=2 contact cards inside the top_k window."""
        merged = AzureSearchRepository._deduplicate(self._grouped())[:8]
        contacts = [x.title for x in merged if x.source_type == "key_contact"]
        assert len(contacts) >= 2, "only one contact card reaches the model again"

    def test_all_results_present_and_deduped(self) -> None:
        merged = AzureSearchRepository._deduplicate(self._grouped())
        assert len(merged) == 12
        assert len({(x.source_type, x.title) for x in merged}) == 12

    def test_duplicates_across_groups_collapse(self) -> None:
        dup = _r("help_content", "same")
        merged = AzureSearchRepository._deduplicate([[dup], [dup]])
        assert len(merged) == 1

    def test_empty_and_ragged_groups(self) -> None:
        merged = AzureSearchRepository._deduplicate(
            [[], [_r("mall_info", "m1")], [], [_r("event", "e1"), _r("event", "e2")]]
        )
        assert [x.title for x in merged] == ["m1", "e1", "e2"]

    def test_no_groups(self) -> None:
        assert AzureSearchRepository._deduplicate([]) == []


class TestSearchDefaults:
    def test_top_k_leaves_room_for_every_source_type(self) -> None:
        """top_k must exceed the 4 source types, or types get one slot each."""
        import inspect
        sig = inspect.signature(AzureSearchRepository.search)
        top_k = sig.parameters["top_k"].default
        from app.integrations.search.azure_search import _QUERY_CONFIG
        assert top_k >= 2 * len(_QUERY_CONFIG), (
            f"top_k={top_k} with {len(_QUERY_CONFIG)} source types allows fewer "
            "than two results per type"
        )
