"""Token-window chunking with tiktoken cl100k_base."""

from __future__ import annotations

import tiktoken

_ENCODING = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Token count for cost logging."""
    if not text:
        return 0
    return len(_ENCODING.encode(text))


def chunk_text(text: str | None, max_tokens: int = 500, overlap: int = 50) -> list[str]:
    """Split *text* into overlapping token windows.

    - Empty / whitespace-only / ``None`` → ``[]``.
    - Under the limit → ``[text]`` — the ORIGINAL string, not a re-decoded
      copy, so byte-identical passthrough is guaranteed.
    - Otherwise sliding window of *max_tokens* advancing by
      ``max_tokens - overlap``.

    ``overlap`` must be strictly less than ``max_tokens``.
    """
    if overlap >= max_tokens:
        raise ValueError(f"overlap ({overlap}) must be < max_tokens ({max_tokens})")
    if text is None or not text.strip():
        return []

    tokens = _ENCODING.encode(text)
    if len(tokens) <= max_tokens:
        return [text]

    chunks: list[str] = []
    step = max_tokens - overlap
    for start in range(0, len(tokens), step):
        window = tokens[start : start + max_tokens]
        if not window:
            break
        chunks.append(_ENCODING.decode(window))
        if start + max_tokens >= len(tokens):
            break
    return chunks
