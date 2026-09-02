"""PDF → (page_number, text) extraction for the flagged PDF sources."""

from __future__ import annotations

import io


def extract_pdf_pages(data: bytes) -> list[tuple[int, str]]:
    """Return ``(page_number, text)`` per non-empty page, 1-indexed.

    ``pdfplumber`` is imported lazily — it lives in the ``indexer`` extra and
    unit tests of other modules must not require it.
    """
    import pdfplumber

    pages: list[tuple[int, str]] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append((number, text))
    return pages
