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
_SURROUNDING_QUOTES = "\"'`“”‘’«»"
_TRAILING_PUNCTUATION = ".,;:!?"
# Unicode punctuation that still tells two names apart ("C#" is not "C").
_SIGNIFICANT_PUNCTUATION = frozenset("#%@*")
# Separators that are part of a number ("3.11", "1/2", "2024-25") when they
# sit between digits, and of a name or sign (".NET", "-5") at a word's start.
_NUMBER_SEPARATORS = frozenset(".,/-:")
_LEADING_MARKS = frozenset(".-")
_ASCII_OPENING_QUOTES = frozenset("\"'")


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


def _is_spelling(char: str) -> bool:
    # Letters, combining marks, digits and symbols: dropping a mark changes the
    # word in many scripts ("दिन" is "day", "दीन" is "poor"), and "C++" is not "C".
    return unicodedata.category(char)[0] in "LMNS" or char in _SIGNIFICANT_PUNCTUATION


def _keeps(text: str, i: int) -> bool:
    char = text[i]
    if _is_spelling(char):
        return True
    before = text[i - 1] if i > 0 else " "
    after = text[i + 1] if i + 1 < len(text) else " "
    if char in _NUMBER_SEPARATORS and before.isdigit() and after.isdigit():
        return True
    return char in _LEADING_MARKS and _starts_word(text, i) and after.isalnum()


def _is_opener(char: str) -> bool:
    return unicodedata.category(char) in ("Ps", "Pi") or char in _ASCII_OPENING_QUOTES


def _starts_word(text: str, i: int) -> bool:
    # Openers are dropped from the key, so "(.NET)" must key as ".NET", not "NET".
    j = i - 1
    while j >= 0 and _is_opener(text[j]):
        j -= 1
    return j < 0 or text[j].isspace()


def spelling_key(name: str) -> str:
    """``name`` with case, whitespace and punctuation removed. Two names with
    the same spelling key differ only in presentation, not in words: a
    separator inside a number or a leading mark (".NET", "-5") is kept."""
    text = normalize_name(name)
    return "".join(c for i, c in enumerate(text) if _keeps(text, i))


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
