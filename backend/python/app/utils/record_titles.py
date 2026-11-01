"""Record names as titles.

Exported and synced files are often named by an id, a timestamp and a slug
(``exp_<hex>__1735689600-sensor-drift.txt``); as a title, only the slug says
what the record is about.
"""

from __future__ import annotations

import re

_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,5}$")
# Hex digests and UUIDs, with any short "<letters>_" prefix they carry, and
# 9-13 digit unix timestamps in seconds or milliseconds.
_NAME_ID = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:(?:[A-Za-z]{1,8}_)?(?:[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}|[0-9a-fA-F]{16,})|[0-9]{9,13})"
    r"(?![A-Za-z0-9])"
)
_NAME_SEPARATORS = re.compile(r"[\s_\-]+")
_DANGLING_SEPARATORS = re.compile(r"(?:^|(?<=\s))[_\-]+|[_\-]+(?=\s|$)")
# A hyphen before a word separates words; before a digit it is part of an
# identifier or a date ("OPS-42", "2026-03-09"), which is how it is asked about.
_WORD_HYPHEN = re.compile(r"-+(?=[A-Za-z])")
_SPACING = re.compile(r"[\s_]+")
_MIN_TITLE_WORDS = 3


def readable_record_name(name: str, *, strip_extension: bool = True) -> str:
    """``name`` without its extension and, when it carries ids, without them
    and with its word separators as spaces. ``""`` when the name is nothing
    but ids.

    A name without ids keeps its separators: "OPS-42" is how a ticket is
    asked about.
    """
    stem = name.strip()
    if strip_extension:
        stem = _EXTENSION.sub("", stem)
    if not has_id_tokens(stem):
        return stem
    words = _DANGLING_SEPARATORS.sub(" ", _NAME_ID.sub(" ", stem))
    return _SPACING.sub(" ", _WORD_HYPHEN.sub(" ", words)).strip()


def has_id_tokens(name: str) -> bool:
    return _NAME_ID.search(name) is not None


def is_mostly_ids(name: str) -> bool:
    """Whether ids make up most of ``name`` (its extension already removed)."""
    return len(readable_record_name(name, strip_extension=False)) * 2 < len(name.strip())


def is_weak_title(title: str) -> bool:
    """Fewer than three words names a channel or a section ("plant-ops",
    "Summary") more often than it says what a record is about."""
    return len(title.split()) < _MIN_TITLE_WORDS


def title_words(title: str) -> set[str]:
    return {word.lower() for word in _NAME_SEPARATORS.split(title) if word}
