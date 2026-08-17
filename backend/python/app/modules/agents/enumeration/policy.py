"""Recognise questions about WHICH records exist, rather than what they say.

Semantic retrieval answers "what does the corpus say about X" by sampling the
most similar passages. It cannot answer "how many are there" or "list them all",
because a sample is not a census — and asking a model to count from a sample
produces a confident number with nothing to attribute it to (see #2975).

This classifier is deliberately separate from
`record_escalation.policy.needs_whole_document`. That one decides how much of a
single record to read; this one decides whether the question is about the record
set at all. Folding them together routes counting questions into whole-document
fetches, which is worse than doing nothing.
"""

from __future__ import annotations

import re

# Verbs and phrasings that ask for a census. "How many" is the obvious one, but
# the measured failure is broader: an inventory request strips citations the same
# way while returning a correct list.
_ENUMERATION_OPERATION = r"""
      how \s+ many | how \s+ much
    | count | counts | counted | tally | total \s+ number | number \s+ of
    | list | lists | enumerate | inventory | catalogue | catalog
    | which \s+ (?: ones | files | documents | records )
    | what (?: \s+ all )? \s+ (?: files | documents | records | do \s+ we \s+ have )
    | do \s+ we \s+ have \s+ any
"""

# The things being enumerated. Without one of these, a bare "list" is usually
# about the contents of one document ("list the risks"), which is the other
# classifier's job.
_CORPUS_OBJECT = r"""
      documents? | docs? | files? | records? | items?
    | sources? | knowledge \s+ base | corpus | connectors?
    | pages? | attachments? | uploads?
"""

# Scope words that make an otherwise-ambiguous request corpus-wide.
_CORPUS_SCOPE = r"""
      all | every | each | entire | whole | total
    | in \s+ (?: the \s+ )? (?: knowledge \s+ base | corpus | workspace )
    | do \s+ we \s+ have | are \s+ there | exists? | available
"""


def _alt(pattern: str) -> re.Pattern[str]:
    return re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE | re.VERBOSE)


_ENUMERATION_OPERATION_RE = _alt(_ENUMERATION_OPERATION)
_CORPUS_OBJECT_RE = _alt(_CORPUS_OBJECT)
_CORPUS_SCOPE_RE = _alt(_CORPUS_SCOPE)


def is_enumeration_query(*texts: str) -> bool:
    """True when the question asks which records exist rather than what they say.

    Requires an enumeration operation AND either a corpus object or a
    corpus-wide scope. Both halves matter: "how many risks does this contract
    list" is about one document and must not route here, while "how many
    documents do we have" must.
    """
    combined = " ".join(t for t in texts if t)
    if not combined.strip():
        return False
    if not _ENUMERATION_OPERATION_RE.search(combined):
        return False
    return bool(
        _CORPUS_OBJECT_RE.search(combined) or _CORPUS_SCOPE_RE.search(combined)
    )
