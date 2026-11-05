"""Whether a cited draft frames its answer badly: it opens with a limitation
instead of its substance (what the assistant itself cannot do or hand over,
the status of the thing asked for, that no single record answers, that the
records conflict), or it disputes wording the question uses.

Read deterministically. The limitation and status rules read the part before
the first citation, up to the first paragraph break; a status sentence after
the first is left alone, as is a status sentence when the question itself
asks about status. The broader opening patterns read the first three
sentences, and the dispute patterns the whole draft. A draft with no citation,
or whose opening says the item is absent, is a not-found answer and is left
alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

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


# "Closest / best match" and "isn't in the records" are left out on purpose:
# rewriting them changed nothing, since the first marks an answer resting on
# the wrong record and the second a neutral aside.
_OPENING_PATTERNS = (
    r"\bno single\b|\bhas no single\b|\bnot one (?:document|record)\b",
    r"\b(?:can['’]t|cannot|can not|couldn['’]t|could not) tell (?:which|whether|if)\b|\bisn['’]t clear which\b"
    r"|\bis not clear which\b|\bnot be the one (?:you|the question)",
    r"\bI\s+(?:can['’]t|cannot|can not|couldn['’]t|could not|am unable to|['’]m unable to|am not able to)\s+"
    r"(?:\w+\s+(?:or|and)\s+)?(?:hand|produce|deliver|provide|give|generate|send|attach|export|share|sign)\b",
    r"\b(?:could|can)(?:n['’]t| not)(?: fully)? reconcile\b",
    r"\b(?:the )?(?:two |these )?(?:documents|records|sources|notes|threads) (?:conflict|disagree|contradict)\b",
    r"\bnothing (?:I found |in the (?:records|documents) )?(?:resolves|settles|says which)\b",
    r"\bno (?:document|record|source) (?:I found )?(?:lists|says|states|confirms|gives|names)\b",
)
_DISPUTE_PATTERNS = (
    r"\bnot as a direct quote\b|\bnot a direct quote\b|\b(?:don['’]t|do not) attribute\b",
    r"\b(?:does not|doesn['’]t|do not|don['’]t|did not|didn['’]t) (?:say|state|specify|show|confirm) (?:whether|if)\b",
    r"\brather than [\"“‘']",
    r"\bno record (?:that|of) (?:it|the|this|that|they|its)\b",
    r"\b(?:does not|doesn['’]t|do not|don['’]t) use the (?:term|word|phrase)\b"
    r"|\bnot (?:the )?(?:question['’]s|your) (?:term|word)\b|\bI read it as\b|\bis not stated as\b",
)
_OPENING_RES = tuple(re.compile(pattern, re.IGNORECASE) for pattern in _OPENING_PATTERNS)
_DISPUTE_RES = tuple(re.compile(pattern, re.IGNORECASE) for pattern in _DISPUTE_PATTERNS)
_ABSENT_RE = re.compile(
    r"\b(?:could(?:n['’]t| not)|did(?:n['’]t| not)|was unable to|were unable to|can['’]t|cannot) (?:find|locate|see)\b"
    r"|\bI found no\b|\bno (?:record|document|mention|information|evidence|data|details?)s? "
    r"(?:of|about|on|for|that|in|describing)\b"
    r"|\b(?:isn['’]t|is not|aren['’]t|are not|wasn['’]t|was not|weren['’]t|were not) "
    r"(?:in|covered|documented|recorded|mentioned|described|available)\b"
    r"|\bnot (?:found|documented|recorded|covered|mentioned) (?:in|anywhere)\b|\bthere (?:is|are|was|were) no\b"
    r"|\bnone of the (?:records|documents|sources)\b|\bdoes(?:n['’]t| not) (?:appear|exist)\b"
    r"|\b(?:doesn['’]t|does not|don['’]t|do not|never) (?:list|define|name|state|give|mention|include|specify|contain"
    r"|document|provide|describe|set|record)\b",
    re.IGNORECASE,
)
# Wider than `_ABSENT_RE`, read on the first sentence only: "the policies do not establish a quorum".
_NOT_ESTABLISHED_RE = re.compile(
    r"\b(?:do|does|did)(?: not|n['’]t) (?:establish|enumerate|confirm|cover|identify|indicate|say|show|contain|specify"
    r"|state|define|list|name|give|mention|include|document|provide|describe|record)\b"
    r"|\b(?:remains?|is|are) (?:unconfirmed|unknown|unspecified|undocumented)\b"
    r"|\bnot (?:specified|stated|documented|defined|established|enumerated|confirmed)\b",
    re.IGNORECASE,
)
_LINK_RE = re.compile(r"\s*\[\d+\]\((?:https?://[^)\s]+|ref[\w-]+)\)|\s*\[source\]\([^)]*\)")
_ANSWER_LABEL_RE = re.compile(r"^\*\*(?:Short answer|Answer|Summary):?\*\*:?\s*")
_MARKUP_RE = re.compile(r"[*_`]+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_MAX_SENTENCE_WINDOW_CHARS = 250
_ABSENT_SENTENCES = 2
_OPENING_SENTENCES = 3
_DISPUTE_CONTEXT_CHARS = 80


@dataclass(frozen=True)
class FramingHit:
    """``opening``: the draft leads with it; ``dispute``: the draft says it;
    ``checker``: the dispute checker reads it as disputing a stated detail."""

    kind: Literal["opening", "dispute", "checker"]
    quote: str


def _first_sentences(text: str, count: int) -> str:
    text = _ANSWER_LABEL_RE.sub("", _LEADING_HEADINGS_RE.sub("", text.strip()))
    sentences = _SENTENCE_SPLIT_RE.split(text.strip(), maxsplit=count)[:count]
    return " ".join(sentences)[:_MAX_SENTENCE_WINDOW_CHARS * count]


def plain_text(draft: str) -> str:
    """The draft without its citation links."""
    return _LINK_RE.sub("", draft).strip()


def framing_exempt(draft: str) -> bool:
    """A draft that cites nothing, or opens by saying the item is absent: its
    concession is the answer."""
    if _CITATION_RE.search(draft) is None:
        return True
    return _ABSENT_RE.search(_first_sentences(plain_text(draft), _ABSENT_SENTENCES)) is not None


def concedes_absence(draft: str) -> bool:
    """The draft's first sentence says what was asked for is not in the records."""
    first = _first_sentences(_MARKUP_RE.sub("", plain_text(draft)), 1).split("\n\n")[0]
    return _ABSENT_RE.search(first) is not None or _NOT_ESTABLISHED_RE.search(first) is not None


def framing_hits(question: str, draft: str) -> list[FramingHit]:
    """What in a cited draft's framing should be rewritten, or nothing."""
    if framing_exempt(draft):
        return []
    hits: list[FramingHit] = []
    lead = limitation_lead(question, draft)
    if lead:
        hits.append(FramingHit("opening", lead))
    text = plain_text(draft)
    opening = _first_sentences(text, _OPENING_SENTENCES)
    for pattern in _OPENING_RES:
        found = pattern.search(opening)
        if found and not (lead and found.group(0) in lead):
            hits.append(FramingHit("opening", found.group(0)))
    for pattern in _DISPUTE_RES:
        found = pattern.search(text)
        if found:
            context = text[max(0, found.start() - _DISPUTE_CONTEXT_CHARS):found.end() + _DISPUTE_CONTEXT_CHARS]
            hits.append(FramingHit("dispute", context))
    return hits


__all__ = [
    "FramingHit",
    "asks_about_status",
    "concedes_absence",
    "framing_exempt",
    "framing_hits",
    "limitation_lead",
    "plain_text",
]
