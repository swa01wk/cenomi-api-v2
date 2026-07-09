"""Parse service-request reference numbers from natural-language chat messages."""

from __future__ import annotations

import re

# Explicit ref patterns — safe to extract on any non-CREATE intent turn.
_EXPLICIT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:ref\.?\s*no\.?|reference\s*(?:no\.?|number|#)?)\s*:?\s*(\d+)", re.I),
    re.compile(r"\bopen\s+sr\s*:?\s*(\d+)", re.I),
    re.compile(r"\bsr\s*(?:ref\.?|no\.?|number|#)?\s*:?\s*(\d+)", re.I),
    re.compile(r"\bservice\s*request\s*(?:ref\.?|no\.?|number|#)?\s*:?\s*(\d+)", re.I),
    re.compile(r"\bhandover\s*sr\s*(?:ref\.?|no\.?|number|#)?\s*:?\s*(\d+)", re.I),
)

# Standalone numeric ID — only when user is clearly checking status (avoid CREATE false positives).
_STANDALONE_PATTERN = re.compile(r"^\s*(\d{4,})\s*$")


def parse_sr_reference(message: str, *, allow_standalone: bool = False) -> str | None:
    """Return a platform SR id string extracted from *message*, or ``None``.

  Parameters
  ----------
  allow_standalone:
      When ``True``, accept a message that is only digits (e.g. ``"11711"``).
      Use only when supervisor intent is ``CHECK_SERVICE_REQUEST_STATUS``.
    """
    text = (message or "").strip()
    if not text:
        return None

    for pattern in _EXPLICIT_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)

    if allow_standalone:
        match = _STANDALONE_PATTERN.match(text)
        if match:
            return match.group(1)

    return None
