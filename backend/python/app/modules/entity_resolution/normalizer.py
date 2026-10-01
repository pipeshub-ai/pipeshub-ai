"""Name normalization for taxonomy entities.

``normalize_name`` produces the comparison key two spellings are matched on
(Tier 0 of the resolver and the deterministic node key). ``display_form``
keeps the author's casing for the node's display name. Both strip the same
noise so a name and its display form always normalize to the same key.
"""

from __future__ import annotations

import re
import unicodedata

MIN_NAME_LENGTH = 2
MAX_NAME_LENGTH = 100

_WHITESPACE_RE = re.compile(r"\s+")
_ZERO_WIDTH_RE = re.compile("[​-‍⁠﻿]")
_NON_WORD_RE = re.compile(r"[\W_]+")
_SURROUNDING_QUOTES = "\"'`“”‘’«»"
_TRAILING_PUNCTUATION = ".,;:!?"


def _strip_once(text: str) -> str:
    # Quotes only come off as a pair, so a quoted word inside a name keeps both.
    if len(text) >= 2 and text[0] in _SURROUNDING_QUOTES and text[-1] in _SURROUNDING_QUOTES:
        text = text[1:-1].strip()
    return text.rstrip(_TRAILING_PUNCTUATION).strip()


def _strip_noise(raw: str) -> str:
    text = unicodedata.normalize("NFKC", raw or "")
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    # Repeated to a fixed point: stripping punctuation can expose a quote
    # ('Project "Phoenix".'), and a name must key the same as its display form.
    while (stripped := _strip_once(text)) != text:
        text = stripped
    return text


def display_form(raw: str) -> str:
    """The cleaned name as the author spelled it, casing preserved."""
    return _strip_noise(raw)


def normalize_name(raw: str) -> str:
    """The case-insensitive comparison key for a taxonomy name."""
    return _strip_noise(raw).casefold()


def spelling_key(name: str) -> str:
    """``name`` with case, punctuation and whitespace removed. Two names with
    the same spelling key differ only in presentation, not in words."""
    return _NON_WORD_RE.sub("", normalize_name(name))


def is_acceptable_name(normalized: str) -> bool:
    """Whether a normalized name is a usable taxonomy label.

    Rejects empty and one-character strings, and strings long enough to be a
    sentence rather than a label.
    """
    return MIN_NAME_LENGTH <= len(normalized) <= MAX_NAME_LENGTH


__all__ = [
    "MAX_NAME_LENGTH",
    "MIN_NAME_LENGTH",
    "display_form",
    "is_acceptable_name",
    "normalize_name",
    "spelling_key",
]
