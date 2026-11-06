"""Deterministic signals that a draft answer may not be fully supported.

Any one of them sends the draft to the checker; none of them means the draft
is final at no extra cost. They read the whole draft, not its opening.
"""

from __future__ import annotations

import re
from itertools import combinations
from typing import TYPE_CHECKING

from app.agents.agent_loop.coverage.facets import tokens

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.agents.agent_loop.coverage.evidence import Evidence
    from app.agents.agent_loop.coverage.facets import Facet, FacetPlan

_MAX_ADMISSIONS = 5
_MAX_QUOTE_CHARS = 300
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")

ABSTENTION_RE = re.compile(
    r"\b(?:"
    r"(?:does|do|did)\s+not|(?:does|do|did)n['’]t"
    r")\s+(?:explicitly\s+|clearly\s+|directly\s+)?"
    r"(?:specify|mention|state|say|include|contain|provide|indicate|identify|list|name|describe|record)\b"
    r"|\b(?:is|are|was|were)(?:\s+not|n['’]t)\s+(?:explicitly\s+|clearly\s+)?"
    r"(?:specified|mentioned|stated|available|provided|found|included|listed|documented|indicated|recorded)\b"
    r"|\b(?:could|can|was|were)(?:\s*not|n['’]t)\s+"
    r"(?:find|locate|determine|identify|confirm|be\s+determined|be\s+found|be\s+confirmed)\b"
    r"|\bcannot\s+(?:find|locate|determine|identify|confirm|be\s+determined|be\s+found|be\s+confirmed)\b"
    r"|\bunable\s+to\s+(?:find|locate|determine|identify|confirm)\b"
    r"|\bno\s+(?:information|mention|details?|data|records?|results?)\s+(?:about|on|regarding|for|of|was|were|is|that)\b"
    r"|\bnot\s+(?:found|available)\s+in\s+the\b"
    r"|\bnone\s+of\s+[\w\s]{0,40}?\b(?:mention|specif|state|say|contain|provide|include|list)\w*\b"
    r"|\b(?:I|we)\s+(?:do\s+not|don['’]t)\s+have\s+(?:enough\s+|any\s+|sufficient\s+)?(?:information|details|data)\b",
    re.IGNORECASE,
)
"""A sentence saying what was asked for is not specified, found or available."""

NEAR_MISS_RE = re.compile(
    r"\b(?:closest|nearest|best)\s+(?:match(?:ing)?|equivalent|record|thing|candidate|related)\b"
    r"|\b(?:does|do|did|is|are)(?:n['’]t|\s+not)\s+(?:quite\s+|exactly\s+|fully\s+|directly\s+)?"
    r"(?:meet|match|satisfy|fit|cover|address|correspond)\b"
    r"|\bnot\s+(?:exactly|precisely|specifically|necessarily)\s+(?:the|what|a|an)\b"
    r"|\b(?:I|we)\s+(?:did\s+not|didn['’]t|could\s+not|couldn['’]t|was\s+unable\s+to|were\s+unable\s+to"
    r"|cannot|can['’]t|have\s+not|haven['’]t)\s+"
    r"(?:find|found|retrieve|retrieved|locate|located|read|access|open|see|seen|confirm|verify|identify|determine)\b"
    r"|\b(?:wasn['’]t|was\s+not|weren['’]t|were\s+not|isn['’]t|is\s+not|aren['’]t|are\s+not)\s+"
    r"(?:retrieved|among|in\s+the\s+(?:records|results|documents|sources|knowledge\s+base))\b"
    r"|\bno\s+(?:record|document|source|mention|result|evidence|reference)s?\s+"
    r"(?:of|that|which|for|about|on|mentions?|states?|says?)\b"
    r"|\b(?:none|neither)\s+of\s+the\s+(?:records|documents|sources|results|retrieved)\b"
    r"|\bnot\s+(?:explicitly|directly|clearly)\s+(?:stated|mentioned|specified|documented|named|covered)\b"
    r"|\bif\s+you\s+(?:mean|meant|are\s+asking\s+about)\b"
    r"|\bwhich\s+one\s+you\s+mean\b"
    r"|\bnot\s+(?:certain|sure)\s+(?:it|this|that)(?:['’]s|\s+is)?\b",
    re.IGNORECASE,
)

CONFLICT_RE = re.compile(
    r"\b(?:conflict(?:ing|s)?|discrepanc(?:y|ies)|inconsisten(?:t|cy|cies)|contradict(?:s|ory|ion)?"
    r"|disagree(?:s|ment)?)\b"
    r"|\b(?:sources|records|documents|threads)\s+(?:differ|disagree|conflict|vary)\b"
    r"|\b(?:another|a\s+different|a\s+second|an\s+older|a\s+newer)\s+(?:version|record|source|document)\s+"
    r"(?:says|states|gives|lists|puts|sets)\b",
    re.IGNORECASE,
)


# Record names: distinctive words only, so "rollout plan v2" and "rollout plan
# (revised)" read as one subject.
_MIN_SHARED_NAME_TOKENS = 2
_MIN_NAME_OVERLAP = 0.5
_NAME_TOKEN_RE = re.compile(r"[a-z0-9]+")
_VERSION_RE = re.compile(r"v\d+")
_NAME_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "into", "about", "notes", "note", "doc",
    "draft", "copy", "new", "old", "txt", "pdf", "docx", "md", "html",
    "update", "updated", "revised", "revision", "correction", "corrected",
    "amended", "amendment", "addendum", "erratum", "errata", "final",
    "supersedes", "superseding", "superseded", "replaces",
})


def _name_tokens(name: str) -> set[str]:
    return {
        token for token in _NAME_TOKEN_RE.findall(name.lower())
        if len(token) > 2
        and not token.isdigit()
        and token not in _NAME_STOPWORDS
        and not _VERSION_RE.fullmatch(token)
    }


def same_subject(a: str, b: str) -> bool:
    """Whether two record names share most of their distinctive words."""
    tokens_a, tokens_b = _name_tokens(a), _name_tokens(b)
    shared = tokens_a & tokens_b
    if len(shared) < _MIN_SHARED_NAME_TOKENS:
        return False
    return len(shared) / len(tokens_a | tokens_b) >= _MIN_NAME_OVERLAP


def _sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in _SENTENCE_RE.split(text) if sentence.strip()]


def find_admissions(text: str) -> list[str]:
    """Sentences anywhere in the draft that concede a near miss, a missing
    fact, or a record not read."""
    found: list[str] = []
    for sentence in _sentences(text):
        if NEAR_MISS_RE.search(sentence) or ABSTENTION_RE.search(sentence):
            found.append(sentence[:_MAX_QUOTE_CHARS])
            if len(found) >= _MAX_ADMISSIONS:
                break
    return found


def mentions_conflict(text: str) -> bool:
    return bool(CONFLICT_RE.search(text))


def _covers(reference: "Facet", texts: "Iterable[str]") -> bool:
    wanted = tokens(reference.text) | {t for term in reference.terms for t in tokens(term)}
    if not wanted:
        return True
    need = len(wanted) if len(wanted) < 3 else max(2, (len(wanted) * 2 + 2) // 3)
    return any(len(wanted & tokens(text)) >= need for text in texts)


def unread_references(plan: "FacetPlan | None", evidence: "Evidence") -> list["Facet"]:
    """References the question names that no record read so far is, or quotes."""
    if plan is None:
        return []
    read = evidence.read_records()
    return [
        reference for reference in plan.references
        if not any(_covers(reference, (record.name, *record.passages)) for record in read)
    ]


def near_duplicate_reads(evidence: "Evidence", cited_record_ids: "Iterable[str]") -> list[tuple[str, str]]:
    """Pairs of read records with near-identical names where at least one is
    cited: lookalikes the answer may be weighing as versions of each other."""
    cited = set(cited_record_ids)
    read = [record for record in evidence.read_records() if record.name and record.record_id]
    pairs: list[tuple[str, str]] = []
    for a, b in combinations(read, 2):
        if (a.record_id in cited or b.record_id in cited) and same_subject(a.name, b.name):
            pairs.append((a.record_id or "", b.record_id or ""))
    return pairs


__all__ = [
    "ABSTENTION_RE",
    "CONFLICT_RE",
    "NEAR_MISS_RE",
    "find_admissions",
    "mentions_conflict",
    "near_duplicate_reads",
    "same_subject",
    "unread_references",
]
