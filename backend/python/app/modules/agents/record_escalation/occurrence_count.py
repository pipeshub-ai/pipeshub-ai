"""Detect occurrence-count questions and count a phrase over full record text.

Used when the agent would otherwise tally a word from retrieved snippets
(issue #2996). Pure functions: no I/O, no LLM.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

# Intent: the user wants a tally of a phrase inside a document, not a
# corpus-level "how many documents" count and not a locatable fact.
_OCCURRENCE_INTENT_RE = re.compile(
    r"""
    how \s+ many \s+ times
    | how \s+ often
    | how \s+ many \s+ mentions?
    | (?: count | number \s+ of ) \s+ (?: the \s+ )?
      (?: occurrences? | mentions? | appearances? )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Without one of these, "how many times did we meet" is not a document tally.
_OCCURRENCE_SCOPE_RE = re.compile(
    r"""
    mentioned | mentions | appear(?:s|ed|ances?)? | occur(?:s|red|rences?)?
    | show(?:s|ed)? \s+ up
    | \b (?: book | document | doc | file | pdf | record | text | chapter ) \b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_QUOTED_PHRASE_RE = re.compile(r"""["“”'](?P<phrase>[^"“”']+)["“”']""")

_PHRASE_AFTER_TIMES_RE = re.compile(
    r"""
    how \s+ many \s+ times \s+
    (?: is | are | does | do | was | were )? \s*
    (?P<phrase>.+?) \s+
    (?: mentioned | appear(?:s|ed)? | occur(?:s|red)? | show(?:s|ed)? \s+ up )
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

_PHRASE_AFTER_OFTEN_RE = re.compile(
    r"""
    how \s+ often \s+
    (?: is | are | does | do | was | were )? \s*
    (?P<phrase>.+?) \s+
    (?: mentioned | appear(?:s|ed)? | occur(?:s|red)? )
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

_PHRASE_AFTER_COUNT_OF_RE = re.compile(
    r"""
    (?:
        (?: count | number \s+ of ) \s+ (?: the \s+ )?
        (?: occurrences? | mentions? | appearances? )
      | how \s+ many \s+ mentions?
    )
    \s+ of \s+
    (?P<phrase>.+?)
    (?: \s+ in \b | $ )
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

_TRAILING_SCOPE_RE = re.compile(
    r"""
    \s+ in \s+ (?: the \s+ )?
    (?: book | document | doc | file | pdf | record | text | chapter | it )
    [\s?.!]* $
    """,
    re.IGNORECASE | re.VERBOSE,
)

_LEADING_ARTICLES_RE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)


def is_occurrence_count_query(*texts: str) -> bool:
    """True when the request is tallying a phrase inside a document."""
    combined = " ".join(t for t in texts if t)
    if not combined.strip():
        return False
    if not _OCCURRENCE_INTENT_RE.search(combined):
        return False
    return bool(_OCCURRENCE_SCOPE_RE.search(combined))


def parse_occurrence_phrase(*texts: str) -> str | None:
    """Extract the phrase to count, or None if this is not that question."""
    combined = " ".join(t for t in texts if t)
    if not is_occurrence_count_query(combined):
        return None

    quoted = _QUOTED_PHRASE_RE.search(combined)
    if quoted:
        return _normalize_phrase(quoted.group("phrase"))

    for pattern in (
        _PHRASE_AFTER_TIMES_RE,
        _PHRASE_AFTER_OFTEN_RE,
        _PHRASE_AFTER_COUNT_OF_RE,
    ):
        match = pattern.search(combined)
        if match:
            phrase = _normalize_phrase(match.group("phrase"))
            if phrase:
                return phrase
    return None


def count_occurrences(haystack: str, phrase: str) -> int:
    """Case-insensitive, non-overlapping phrase count over ``haystack``."""
    if not haystack or not phrase:
        return 0
    pattern = _phrase_regex(phrase)
    return sum(1 for _ in pattern.finditer(haystack))


def record_plain_text(record: Mapping[str, Any]) -> str:
    """Concatenate block text for a fetched record, skipping fragment children."""
    containers = record.get("block_containers") or {}
    blocks = containers.get("blocks") if isinstance(containers, Mapping) else None
    if not isinstance(blocks, list):
        blocks = record.get("blocks") or []
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        if block.get("parent_block_index") is not None:
            continue
        text = _block_text(block)
        if text:
            parts.append(text)
    return "\n".join(parts)


def format_occurrence_count_note(
    *,
    phrase: str,
    per_record: list[tuple[str, str, int]],
) -> str:
    """Instruction block so the model states the computed count instead of recounting."""
    if not per_record:
        return ""
    lines = [
        "## Computed occurrence count — do not recount",
        f'Phrase: "{phrase}"',
        "Counted with a case-insensitive search over the **full record text** "
        "(not the retrieved snippets, not the truncated blocks shown above).",
    ]
    for record_id, record_name, n in per_record:
        name = record_name or "(unnamed)"
        lines.append(f'- Record ID `{record_id}` ({name}): **{n}**')
    lines.append(
        "When stating how many times the phrase appears, use these numbers "
        "and cite the record. Do not invent a different tally from the visible blocks."
    )
    return "\n".join(lines)


def _normalize_phrase(raw: str) -> str | None:
    cleaned = _TRAILING_SCOPE_RE.sub("", raw or "").strip()
    cleaned = _LEADING_ARTICLES_RE.sub("", cleaned).strip(" \t\n\r\"'`.,;:?!")
    return cleaned or None


def _phrase_regex(phrase: str) -> re.Pattern[str]:
    tokens = [re.escape(t) for t in phrase.split() if t]
    if not tokens:
        return re.compile(r"(?!)")
    body = r"\s+".join(tokens)
    if re.match(r"\w", phrase[0], re.UNICODE):
        body = r"\b" + body
    if re.search(r"\w$", phrase, re.UNICODE):
        body = body + r"\b"
    return re.compile(body, re.IGNORECASE)


def _block_text(block: Mapping[str, Any]) -> str:
    data = block.get("data")
    if isinstance(data, str):
        return data
    if isinstance(data, Mapping):
        for key in ("text", "row_natural_language_text", "content"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""
