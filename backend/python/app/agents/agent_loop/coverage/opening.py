"""Whether a cited draft opens with a limitation instead of its substance: what
the assistant itself cannot do or hand over, or the status of the thing asked
for ("not yet delivered"), ahead of the cited facts that follow.

Read deterministically from the opening alone: the part before the first
citation, up to the first paragraph break. A draft with no citation is a
not-found answer and is left alone, as is a status sentence after the first,
and a status sentence when the question itself asks about status.
"""

from __future__ import annotations

import re

_MAX_OPENING_CHARS = 500
_MAX_FIRST_SENTENCE_CHARS = 300

_CITATION_RE = re.compile(r"\[[^\[\]\n]*\]\((?:ref[\w-]+|https?://[^\s)]+)\)")
_LEADING_HEADINGS_RE = re.compile(r"^(?:[ \t]*#+[^\n]*\n)+")
_PARAGRAPH_END_RE = re.compile(r"\n\s*\n|\n\s*(?:#|[-*•]\s|\d+[.)]\s|\|)")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])[*_)\]\"'’”]*\s+")

_CANNOT = (
    r"\bI\s+(?:can['’]t|cannot|can\s+not|couldn['’]t|could\s+not|am\s+(?:not\s+able|unable)\s+to"
    r"|['’]m\s+(?:not\s+able|unable)\s+to|was\s+(?:not\s+able|unable)\s+to|have\s+no\s+way\s+to"
    r"|(?:do\s+not|don['’]t)\s+have\s+(?:the\s+)?(?:ability|access|a\s+way)\s+to)\s+"
    r"(?:\w+\s+(?:or|and)\s+)?"
)
_HAND_OVER = r"(?:generate|sign|send|hand|deliver|attach|export|upload|share|email|transmit)"
# "Produce", "create" or "provide" alone also open a caveat about the answer
# itself ("I could not produce a full tally"), so they count only first.
_MAKE = r"(?:generate|produce|sign|send|hand|deliver|attach|create|build|export|upload|run|execute|share|email|transmit|issue)"

_INABILITY_RE = re.compile(
    _CANNOT + _HAND_OVER + r"\b"
    r"|" + _CANNOT + r"(?:" + _MAKE + r"|provide|give)\b[^.\n]{0,60}?\b(?:myself|itself|for\s+you)\b"
    r"|\bI\s+can\s+(?:only\s+)?(?:describe|summari[sz]e|outline|explain|list|tell\s+you)\b[^.\n]{0,120}?\bnot\s+"
    r"(?:hand|send|give|provide|produce|generate|deliver|attach|share|sign|create)\b",
    re.IGNORECASE,
)
_FIRST_INABILITY_RE = re.compile(_CANNOT + r"(?:" + _MAKE + r"|provide)\b", re.IGNORECASE)

_DONE_STATES = (
    r"(delivered|built|sent|shipped|produced|generated|signed|finished|released|published|completed"
    r"|uploaded|exported|issued|assembled|prepared)"
)
# Each alternative captures the state word, so one the question itself uses
# (the topic, not a status) can be told apart.
_STATUS_RE = re.compile(
    r"\b(?:doesn['’]t|does\s+not|don['’]t|do\s+not|didn['’]t|did\s+not)\s+show\s+(?:it|them|this|that|the\s+\w+)?\s*"
    r"(?:as\s+|was\s+|were\s+|has\s+been\s+|had\s+been\s+|being\s+|to\s+be\s+)?(?:\w+\s+(?:or|and)\s+)?"
    + _DONE_STATES + r"\b"
    r"|\b(?:is|are|was|were)(?:\s+not|n['’]t)\s+(?:yet\s+)?" + _DONE_STATES + r"\b"
    r"|\b(?:has|have|had)(?:\s+not|n['’]t)\s+(?:yet\s+)?been\s+" + _DONE_STATES + r"\b"
    r"|\bnot\s+(?:yet\s+)?(?:been\s+|confirmed\s+as\s+|shown\s+as\s+|as\s+)?" + _DONE_STATES + r"\b"
    r"|\byet\s+to\s+be\s+" + _DONE_STATES + r"\b"
    r"|\bnot\s+yet\s+(available|ready)\b"
    r"|\b(?:still|remains?)\s+(pending)\b"
    r"|\bstill\s+being\s+" + _DONE_STATES + r"\b",
    re.IGNORECASE,
)

_ASKED_STATES = (
    r"(?:delivered|shipped|done|completed|finished|released|available|ready|sent|built|signed|approved|live"
    r"|launched|deployed|merged|resolved|fixed|closed|deliver|ship|complete|finish|release|launch|deploy|merge"
    r"|resolve|fix|close)"
)
_STATUS_QUESTION_RE = re.compile(
    r"\bstatus\s+(?:of|on|for)\b|\b(?:current|latest)\s+status\b|\bwhat['’]?s\s+the\s+status\b"
    r"|\bprogress\s+(?:of|on)\b|\bwhen\s+(?:will|is|are|was|were|does|do|did|can)\b"
    r"|\bwhether\b[^?.;()]{0,80}?\b" + _ASKED_STATES + r"\b"
    r"|(?:^|[,;:?]\s*|\b(?:and|or|but|so)\s+)(?:was|were|has|have|had|is|are|did|does|do)\s+(?:\S+\s+){0,8}?"
    + _ASKED_STATES + r"\b",
    re.IGNORECASE,
)


def _opening(draft: str) -> str | None:
    first_citation = _CITATION_RE.search(draft)
    if first_citation is None:
        return None
    head = _LEADING_HEADINGS_RE.sub("", draft[: first_citation.start()].lstrip()).strip()
    paragraph_end = _PARAGRAPH_END_RE.search(head)
    return (head[: paragraph_end.start()] if paragraph_end else head)[:_MAX_OPENING_CHARS]


def asks_about_status(question: str) -> bool:
    return bool(_STATUS_QUESTION_RE.search(question))


def _sentence_at(text: str, position: int) -> str:
    start = 0
    for end in _SENTENCE_END_RE.finditer(text):
        if end.start() >= position:
            return text[start:end.start()].strip()
        start = end.end()
    return text[start:].strip()


def limitation_lead(question: str, draft: str) -> str | None:
    """The sentence a cited draft opens with that states a limitation or a
    status ahead of its substance, or None."""
    opening = _opening(draft)
    if not opening:
        return None
    first_sentence = _SENTENCE_END_RE.split(opening, maxsplit=1)[0][:_MAX_FIRST_SENTENCE_CHARS]
    found = _INABILITY_RE.search(opening) or _FIRST_INABILITY_RE.search(first_sentence)
    if found is None and not asks_about_status(question):
        asked = question.lower()
        found = next(
            (status for status in _STATUS_RE.finditer(first_sentence)
             if next(word for word in status.groups() if word).lower() not in asked),
            None,
        )
    if found is None:
        return None
    return _sentence_at(opening, found.start())[:_MAX_FIRST_SENTENCE_CHARS]


__all__ = ["asks_about_status", "limitation_lead"]
