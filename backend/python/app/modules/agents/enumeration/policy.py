"""Recognise a corpus-wide census, and nothing else.

Semantic retrieval answers "what does the corpus say about X" by sampling the
most similar passages. It cannot answer "how many are there", because a sample
is not a census — and a model asked to count from a sample produces a confident
number with no passage to attribute it to (#2975).

This classifier is deliberately narrow. It fires only when the question asks for
every record the caller can see, with no constraint left over that the census
does not apply. That restraint is the whole safety property: an unfiltered count
answering a filtered question is a worse failure than the one being fixed,
because it is confidently and completely cited.

It is also deliberately separate from `record_escalation.policy`. That module
decides how much of one document to read; this one decides whether the question
is about the record set at all. Adding counting phrases there would route
counting questions into whole-document fetches.
"""

from __future__ import annotations

import re

# Asking for a census.
_ENUMERATION_OPERATION = r"""
      how \s+ many | how \s+ much
    | count | counts | tally | total \s+ number | number \s+ of
    | list | lists | enumerate | inventory | catalogue | catalog
    | what (?: \s+ all )? \s+ (?: files | documents | records | docs )
    | do \s+ we \s+ have
"""

# The unit being counted must be a RECORD. "Pages", "chapters" and "sections"
# are units inside one document, so "how many pages are in this PDF" is not a
# census and must not route here.
_RECORD_NOUN = r"""
      documents? | docs? | files? | records? | items?
    | sources? | knowledge \s+ base | corpus | uploads?
"""

_CORPUS_SCOPE = r"""
      all | every | entire | whole
    | in \s+ (?: the \s+ )? (?: knowledge \s+ base | corpus | workspace )
    | do \s+ we \s+ have | are \s+ there | available
"""

# Anything below means the question is NOT "everything I can see". A census
# cannot honour these, so the query belongs on the agent path where retrieval
# can actually apply them.
# "about" has two senses here and only one is a constraint. "documents about
# onboarding" filters the set; "what is each one about" asks for summaries of
# the whole set and is the question in #2975. So the topical sense requires a
# subject after it — a trailing "about?" is not a filter.
_TOPICAL_CONSTRAINT = r"""
      mention (?: s | ed | ing )? | regarding | concerning
    | related \s+ to | relating \s+ to | referenc (?: e | es | ing )
    | containing | contains | discuss (?: es | ing )? | cover (?: s | ing )?
    | on \s+ the \s+ (?: subject | topic ) | with \s+ respect \s+ to
    | that \s+ (?: say | says | mention | talk )
"""

# Narrowed to a container, or to one document, rather than the whole corpus.
_CONTAINER_CONSTRAINT = r"""
      in \s+ (?: this | that | the ) \s+ (?: folder | directory | space | project
        | channel | drive | pdf | document | doc | file | deck | report )
    | this \s+ (?: pdf | document | doc | file | folder | deck | report )
    | pages? | chapters? | sections? | slides? | paragraphs?
    | under \s+ | inside \s+ | within \s+ (?: this | that | the )
"""

# Time-bounded, which a census over the whole set does not honour.
_TEMPORAL_CONSTRAINT = r"""
      yesterday | today | this \s+ (?: week | month | quarter | year )
    | last \s+ (?: week | month | quarter | year | night )
    | since | before | after | between | recent (?: ly )? | updated | modified
    | created | added | changed | new (?: est )?
"""

# A follow-up referring to an earlier result. Only the current question is
# classified, so "how many of those" would otherwise census the whole corpus.
_ANAPHORA = r"""
      of \s+ (?: those | these | them | that | it )
    | those | these | them
"""


def _alt(pattern: str) -> re.Pattern[str]:
    return re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE | re.VERBOSE)


_OPERATION_RE = _alt(_ENUMERATION_OPERATION)
_RECORD_NOUN_RE = _alt(_RECORD_NOUN)
_CORPUS_SCOPE_RE = _alt(_CORPUS_SCOPE)

# "about" has two senses and only one narrows the set. "documents about
# onboarding" is a filter; "what is each one about" asks for summaries of the
# whole set and is the question in #2975. The filtering sense needs a subject
# after it, so this is matched outside the \b...\b wrapper the others use.
_TOPICAL_ABOUT_RE = re.compile(
    r"\babout\s+(?!it\b|them\b|that\b|this\b)[a-z]", re.IGNORECASE
)

_EXCLUSIONS = (
    ("topical", _TOPICAL_ABOUT_RE),
    ("topical", _alt(_TOPICAL_CONSTRAINT)),
    ("container", _alt(_CONTAINER_CONSTRAINT)),
    ("temporal", _alt(_TEMPORAL_CONSTRAINT)),
    ("anaphora", _alt(_ANAPHORA)),
)


def excluded_reason(text: str) -> str | None:
    """Name the constraint that makes this not a whole-corpus census, if any."""
    for name, pattern in _EXCLUSIONS:
        if pattern.search(text):
            return name
    return None


def is_enumeration_query(*texts: str) -> bool:
    """True only for a census over every record the caller can see.

    Requires an enumeration operation and a record noun or corpus scope, and
    rejects any query carrying a constraint the census cannot apply. When in
    doubt this returns False: falling through to the agent gives a possibly
    uncited answer, whereas firing wrongly gives a fully cited wrong one.
    """
    combined = " ".join(t for t in texts if t)
    if not combined.strip():
        return False
    if excluded_reason(combined) is not None:
        return False
    if not _OPERATION_RE.search(combined):
        return False
    return bool(_RECORD_NOUN_RE.search(combined) or _CORPUS_SCOPE_RE.search(combined))
