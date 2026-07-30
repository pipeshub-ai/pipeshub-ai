"""
Candidate-selection policy and prompt-policy text for record escalation.

Three pure functions:
- needs_whole_document: boolean from intent marker or conservative regex.
- build_candidates: which records to offer for full-fetch.
- policy_text: the shared instruction block injected into system prompts.

No I/O, no LLM, no thresholds.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from app.modules.agents.record_escalation.models import (
    FetchCandidate,
    FetchPlan,
)

# ---------------------------------------------------------------------------
# Fallback signal — used only when the intent call produced no marker
# (quick mode sets skip_intent, and a failed intent call yields no marker).
# ---------------------------------------------------------------------------
# Matches CLASSES of request, not scenarios. Listing scenarios ("compare two
# reports", "read the full contract") is unbounded and misses the next phrasing
# nobody thought of — "summarize this", "what are the risks here" both slipped
# through an earlier scenario-matching version of this. What actually
# distinguishes the two cases is whether the answer is a property of the whole
# document, and that shows up in the request as an aggregative OPERATION
# (summarize, review, compare), an aggregative OBJECT (risks, obligations, key
# points — things you can only enumerate if you have seen everything), or an
# explicitly exhaustive SCOPE (all/every/entire + a document noun).
#
# Recall is worth more than precision here: a false positive costs a bounded
# candidate list the model is free to ignore, while a false negative leaves the
# model unable to see that it holds 4 of 87 blocks.

# Operations that are whole-scope by construction — you cannot summarize or
# review part of a document and call it a summary or a review.
_AGGREGATIVE_OPERATION = r"""
      summar (?: y | ies | ise[ds]? | ize[ds]? | ising | izing )
    | overview | synops[ie]s | abstract | recap | tl;?\s*dr
    | outline | breakdown | brief \s+ me
    | walk \s+ (?: me \s+ )? through
    | review | audit | assess (?: ment )? | apprais (?: e | al )
    | evaluat (?: e | ion ) | analy (?: se | ze | sing | zing | sis )
    | critique | compar (?: e | ison ) | contrast
    | due \s+ diligence | deep \s+ dive
"""

# Things that can only be reported by having seen the whole document: naming
# "the risks" implies none were missed.
_AGGREGATIVE_OBJECT = r"""
      risks? | issues? | gaps? | concerns? | red \s+ flags? | blockers?
    | obligations? | liabilit (?: y | ies ) | clauses? | provisions?
    | requirements? | deadlines? | milestones? | deliverables?
    | penalt (?: y | ies ) | exclusions? | caveats? | assumptions?
    | (?: key | main | important | major ) \s+
      (?: points? | takeaways? | findings? | themes? | terms? | details? | ideas? )
    | takeaways? | findings? | conclusions? | recommendations?
    | action \s+ items? | next \s+ steps?
    | pros \s+ and \s+ cons | strengths? | weaknesses?
"""

# Generic verbs — document-scoped only in the company of a document noun or an
# exhaustive scope, so they are not sufficient on their own ("explain how to
# reset my password" is not a whole-document request).
_WEAK_OPERATION = r"""
      explain | describe | interpret | unpack
    | list | enumerate | extract | identify | highlight | find | catalogue
    | read | go \s+ (?: over | through ) | look \s+ (?: over | through )
"""

_EXHAUSTIVE_SCOPE = r"""
      all | every | each | entire | whole | complete | completely
    | comprehensive (?: ly )? | thorough (?: ly )? | exhaustive (?: ly )?
    | full | fully | end-to-end | in \s+ detail | detailed
"""

_DOCUMENT_NOUN = r"""
      document | doc | file | record | report | contract | agreement | policy
    | paper | deck | memo | minutes | transcript | spec (?: ification )?
    | attachment | pdf | page | pages | chapter | chapters | section | sections
    | text | content | it | this | that
"""


def _alt(pattern: str) -> re.Pattern[str]:
    return re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE | re.VERBOSE)


_AGGREGATIVE_OPERATION_RE = _alt(_AGGREGATIVE_OPERATION)
_AGGREGATIVE_OBJECT_RE = _alt(_AGGREGATIVE_OBJECT)
_WEAK_OPERATION_RE = _alt(_WEAK_OPERATION)
_EXHAUSTIVE_SCOPE_RE = _alt(_EXHAUSTIVE_SCOPE)
_DOCUMENT_NOUN_RE = _alt(_DOCUMENT_NOUN)


def needs_whole_document(marker: str | None, *texts: str) -> bool:
    """
    Return True if answering this request plausibly depends on whole-document
    content rather than on a locatable passage.

    Args:
        marker: The WHOLE_DOCUMENT marker from the intent call ("yes"/"no"), or
                None when the intent call was skipped or produced no marker.
                A real model judgment, so it wins outright when present.
        *texts: Request texts, used only when `marker` is None.

    The fallback is class-based (see the module constants above), not a list of
    known request shapes: it fires on aggregative operations, aggregative
    objects, or an exhaustive scope applied to a document. It biases toward
    True because a false positive only costs a candidate list the model can
    ignore, whereas a false negative hides coverage information it needs.
    """
    if marker is not None:
        return marker.lower().strip() == "yes"

    combined = " ".join(t for t in texts if t)
    if not combined.strip():
        return False

    if _AGGREGATIVE_OPERATION_RE.search(combined):
        return True
    if _AGGREGATIVE_OBJECT_RE.search(combined):
        return True

    document_scoped = bool(_DOCUMENT_NOUN_RE.search(combined))
    exhaustive = bool(_EXHAUSTIVE_SCOPE_RE.search(combined))
    if exhaustive and document_scoped:
        return True
    return bool(_WEAK_OPERATION_RE.search(combined)) and (document_scoped or exhaustive)


def build_candidates(
    *,
    coverage: dict[str, tuple[int, int]],
    records_in_relevance_order: Sequence[dict[str, Any]],
    already_fetched_ids: set[str],
    max_candidates: int = 8,
) -> FetchPlan:
    """
    Build a FetchPlan from records that are incomplete and not yet fetched.

    Ordering follows `records_in_relevance_order` — the upstream relevance
    ranking from final_results (before the display sort).  Each record appears
    at most once.

    Exclusion reasons are recorded for logging only and never shown to the model.
    """
    candidates: list[FetchCandidate] = []
    excluded: list[tuple[str, str]] = []
    seen_record_ids: set[str] = set()

    for record in records_in_relevance_order:
        if len(candidates) >= max_candidates:
            break

        record_id: str | None = record.get("id")
        if not record_id:
            continue
        if record_id in seen_record_ids:
            continue
        seen_record_ids.add(record_id)

        if record_id in already_fetched_ids:
            excluded.append((record_id, "already fetched"))
            continue

        cov = coverage.get(record_id)
        if cov is None:
            excluded.append((record_id, "not in coverage"))
            continue

        blocks_held, blocks_total = cov
        if blocks_total > 0 and blocks_held >= blocks_total:
            excluded.append((record_id, "all blocks already retrieved"))
            continue

        # Metadata for the candidate list — from context_metadata / semantic_metadata.
        record_name = record.get("record_name") or record.get("recordName") or ""
        sem = record.get("semantic_metadata") or {}
        topics: tuple[str, ...] = tuple(sem.get("topics") or [])
        summary: str = (sem.get("summary") or "")[:200]

        candidates.append(
            FetchCandidate(
                record_id=record_id,
                record_name=record_name,
                topics=topics,
                summary=summary,
                blocks_held=blocks_held,
                blocks_total=blocks_total,
            )
        )

    return FetchPlan(
        candidates=tuple(candidates),
        excluded=tuple(excluded),
    )


def policy_text(tool_ref: str) -> str:
    """
    Return the full-record fetch policy injected into system prompts.

    States the decision inline so smaller models that do not follow
    cross-references to function-calling schemas still get the rule.

    Step 1 — relevance: is the record actually about the question? If not, skip.
    Step 2 — fetch by default: if relevant, call `tool_ref` unless the exact
    fact needed is already visible in a block you hold (or metadata alone
    settles a status/assignee-style question). The skip case is a subordinate
    "unless", not a co-equal step — without a gate/judge backstop, giving it
    equal billing reads as permission to stop after step 1.

    `tool_ref` is the tool name the model should call.
    """
    return (
        f"## Reading Records in Full\n\n"
        f"Search results are ranked fragments, not complete documents. "
        f"`lookup_record`, `navigate`, and `list_files` return even less: "
        f"an ID and metadata, no content at all. Before answering, check "
        f"each record the question is actually about:\n\n"
        f"1. Is the record relevant? If not, skip it.\n"
        f"2. If it is relevant, call `{tool_ref}` with those record_ids in "
        f"ONE call, then answer — UNLESS the exact fact needed is already "
        f"visible in a block you hold, or metadata alone (status, assignee) "
        f"settles the goal. This covers both no content yet from lookup/"
        f"navigate/list_files, and incomplete coverage for a whole-document "
        f"property (a summary, risks or key points, a comparison, whether it "
        f"mentions something anywhere). Never infer content from a title.\n\n"
        f"A retrieval result may include a candidate list showing how much of "
        f"each record you hold ('you have 4 of 87 blocks'), which tells you "
        f"whether reading further would add anything; use it when present. "
        f"Pass the exact Record ID value(s) shown — never invent IDs."
    )
