"""HTML → plain text for indexable content."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

_WHITESPACE_RE = re.compile(r"\s+")


def clean_html(html: str | None) -> str:
    """Strip tags/scripts/styles and collapse whitespace.

    ``None`` or empty input → ``""``.  Entities (&amp; etc.) are decoded by
    the parser.
    """
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    return _WHITESPACE_RE.sub(" ", text).strip()
