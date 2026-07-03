"""Shared helpers for splitting oversized text during block parsing."""

from __future__ import annotations

import re

# spaCy's default max_length is 1_000_000 chars; stay safely below it.
MAX_TEXT_BLOCK_CHARS = 500_000

_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


def split_long_text(
    text: str,
    max_chars: int = MAX_TEXT_BLOCK_CHARS,
) -> list[str]:
    """Split *text* into chunks at sentence boundaries, each at most *max_chars*."""
    if not text or len(text) <= max_chars:
        return [text] if text else []

    sentences = _SENTENCE_BOUNDARY_RE.split(text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if current:
                chunks.append(" ".join(current))
                current, current_len = [], 0
            for offset in range(0, len(sentence), max_chars):
                chunks.append(sentence[offset : offset + max_chars])
            continue

        added_len = len(sentence) + (1 if current else 0)
        if current_len + added_len > max_chars:
            chunks.append(" ".join(current))
            current, current_len = [sentence], len(sentence)
        else:
            current.append(sentence)
            current_len += added_len

    if current:
        chunks.append(" ".join(current))

    return chunks or [text]
