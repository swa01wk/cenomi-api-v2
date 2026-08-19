"""chunk_text / count_tokens unit tests."""

from __future__ import annotations

import pytest
import tiktoken

from app.indexer.transform.chunker import chunk_text, count_tokens

_ENC = tiktoken.get_encoding("cl100k_base")


class TestChunkText:
    def test_under_limit_passthrough_is_original_string(self) -> None:
        text = "A short paragraph that fits."
        result = chunk_text(text, max_tokens=500, overlap=50)
        assert result == [text]
        assert result[0] is text  # not re-decoded

    def test_empty_and_whitespace_and_none(self) -> None:
        assert chunk_text("") == []
        assert chunk_text("   \n\t ") == []
        assert chunk_text(None) == []

    def test_long_text_splits_with_overlap(self) -> None:
        words = " ".join(f"word{i}" for i in range(400))
        chunks = chunk_text(words, max_tokens=100, overlap=20)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(_ENC.encode(chunk)) <= 100
        # Overlap: last 20 tokens of chunk N == first 20 tokens of chunk N+1.
        for a, b in zip(chunks, chunks[1:]):
            assert _ENC.encode(a)[-20:] == _ENC.encode(b)[:20]

    def test_all_content_preserved(self) -> None:
        words = " ".join(f"w{i}" for i in range(300))
        chunks = chunk_text(words, max_tokens=50, overlap=10)
        reassembled = _ENC.decode(
            [t for i, c in enumerate(chunks)
             for t in (_ENC.encode(c) if i == 0 else _ENC.encode(c)[10:])]
        )
        assert reassembled == words

    def test_overlap_must_be_less_than_max(self) -> None:
        with pytest.raises(ValueError):
            chunk_text("text", max_tokens=50, overlap=50)


class TestCountTokens:
    def test_empty(self) -> None:
        assert count_tokens("") == 0

    def test_counts(self) -> None:
        assert count_tokens("hello world") == len(_ENC.encode("hello world"))
