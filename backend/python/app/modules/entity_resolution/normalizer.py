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
_SURROUNDING_QUOTES = "\"'`“”‘’«»"
_TRAILING_PUNCTUATION = ".,;:!?"


def _strip_noise(raw: str) -> str:
    text = unicodedata.normalize("NFKC", raw or "")
    text = _WHITESPACE_RE.sub(" ", text).strip()
    text = text.strip(_SURROUNDING_QUOTES).strip()
    return text.rstrip(_TRAILING_PUNCTUATION).strip()


def display_form(raw: str) -> str:
    """The cleaned name as the author spelled it, casing preserved."""
    return _strip_noise(raw)


def normalize_name(raw: str) -> str:
    """The case-insensitive comparison key for a taxonomy name."""
    return _strip_noise(raw).casefold()


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
]
