"""clean_html unit tests."""

from __future__ import annotations

from app.indexer.transform.html_cleaner import clean_html


class TestCleanHtml:
    def test_scripts_and_styles_removed(self) -> None:
        html = (
            "<p>Keep</p><script>alert('x')</script>"
            "<style>.a{color:red}</style><div>Also keep</div>"
        )
        result = clean_html(html)
        assert "Keep" in result and "Also keep" in result
        assert "alert" not in result and "color" not in result

    def test_entities_decoded(self) -> None:
        assert clean_html("<p>Fish &amp; Chips</p>") == "Fish & Chips"

    def test_whitespace_collapsed(self) -> None:
        assert clean_html("<p>a</p>\n\n   <p>b\t\tc</p>") == "a b c"

    def test_none_and_empty(self) -> None:
        assert clean_html(None) == ""
        assert clean_html("") == ""

    def test_plain_text_unchanged(self) -> None:
        assert clean_html("just text") == "just text"
