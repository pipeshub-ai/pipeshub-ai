"""The facet plan: the parts of a question an answer must have evidence for,
and a lexical test of whether a body of evidence covers each part.

The plan is written once per request by a small model; the lexical test is
deterministic so the gate's covered case costs nothing.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, Field

from app.agents.agent_loop.coverage.llm import complete_json

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from langchain_core.messages import BaseMessage

FacetKind = Literal["entity", "condition", "sub_question", "document_type", "reference"]
_KINDS: frozenset[str] = frozenset({"entity", "condition", "sub_question", "document_type", "reference"})
_KIND_ALIASES = {
    "subquestion": "sub_question", "question": "sub_question", "doc_type": "document_type",
    "doctype": "document_type", "document": "document_type", "constraint": "condition",
    "named_reference": "reference", "ref": "reference",
}
MAX_FACETS = 6
_MAX_TERMS = 3
_MAX_FIELD_CHARS = 200

PLAN_SYSTEM = "You break a workplace question into the parts an answer must cover. Reply with JSON only."
PLAN_USER = (
    "Question: {question}\n\n"
    "List the parts a complete answer needs evidence for:\n"
    "- entity: the specific customer, system, team, product, project, person or event the question is about;\n"
    "- condition: a constraint a source must meet (time window, scope, version, region, kind of change, "
    "threshold, status);\n"
    "- sub_question: each separate thing asked (who, when, which value, why, which steps);\n"
    "- document_type: a kind of document the question names (playbook, postmortem, ADR, meeting notes, "
    "ticket, pull request, runbook, policy, email, chat thread);\n"
    "- reference: a specific document, ticket, id or title the question names.\n"
    "At most 6 parts; leave out parts the question does not state and merge trivial ones. For each give "
    '"kind", "text" (the part in the question\'s words), "terms" (1-3 short distinctive words or phrases '
    "from the question that a passage supporting this part would contain; no generic words such as issue, "
    'team, document, change) and "query" (a search query of 5-15 words for this part, in the words a '
    'workplace document would use, keeping the question\'s main subject). Also give "anchor": the main '
    "subject in the question's words.\n"
    'Reply as {{"anchor": "...", "facets": [{{"kind": "...", "text": "...", "terms": ["..."], '
    '"query": "..."}}]}}'
)


class Facet(BaseModel):
    id: str
    kind: FacetKind
    text: str
    terms: list[str] = Field(default_factory=list[str])
    query: str = ""


class FacetPlan(BaseModel):
    anchor: str = ""
    facets: list[Facet] = Field(default_factory=list[Facet])

    @property
    def document_types(self) -> list[str]:
        return [facet.text for facet in self.facets if facet.kind == "document_type"]

    @property
    def references(self) -> list[Facet]:
        return [facet for facet in self.facets if facet.kind == "reference"]

    def get(self, facet_id: str) -> Facet | None:
        return next((facet for facet in self.facets if facet.id == facet_id), None)


def _clean(value: object) -> str:
    return " ".join(str(value).split())[:_MAX_FIELD_CHARS] if isinstance(value, (str, int, float)) else ""


def _kind(value: object) -> FacetKind | None:
    raw = _clean(value).lower().replace("-", "_").replace(" ", "_")
    raw = _KIND_ALIASES.get(raw, raw)
    return cast("FacetKind", raw) if raw in _KINDS else None


def _terms(value: object) -> list[str]:
    raw: list[object] = [value] if isinstance(value, str) else cast("list[object]", value) if isinstance(value, list) else []
    return [term for term in (_clean(item) for item in raw) if term][:_MAX_TERMS]


def parse_facet_plan(payload: dict[str, Any] | None) -> FacetPlan | None:
    """A plan from the model's JSON, tolerant of extra fields, bad kinds and
    missing queries; None when no usable part is left."""
    if not isinstance(payload, dict):
        return None
    raw_facets = payload.get("facets")
    if not isinstance(raw_facets, list):
        return None
    anchor = _clean(payload.get("anchor"))
    facets: list[Facet] = []
    seen: set[str] = set()
    for item in cast("list[object]", raw_facets):
        if not isinstance(item, dict):
            continue
        raw = cast("dict[str, Any]", item)
        kind, text = _kind(raw.get("kind")), _clean(raw.get("text"))
        if kind is None or not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        query = _clean(raw.get("query")) or " ".join(part for part in (anchor, text) if part)
        facets.append(Facet(id=f"f{len(facets) + 1}", kind=kind, text=text, terms=_terms(raw.get("terms")), query=query))
        if len(facets) >= MAX_FACETS:
            break
    if not facets:
        return None
    return FacetPlan(anchor=anchor, facets=facets)


async def plan_facets(
    llm: Any,  # noqa: ANN401
    question: str,
    *,
    timeout: float,
    on_usage: "Callable[[BaseMessage], None] | None" = None,
) -> FacetPlan | None:
    """The question's parts, from one bounded model call; None when it fails or finds none."""
    if not question.strip():
        return None
    payload = await complete_json(
        llm, PLAN_SYSTEM, PLAN_USER.format(question=question.strip()),
        timeout=timeout, purpose="facet plan", on_usage=on_usage,
    )
    return parse_facet_plan(payload)


_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "into", "about", "that", "this", "what", "which", "who", "when",
    "where", "why", "how", "was", "were", "are", "is", "did", "does", "do", "has", "have", "had", "our",
    "their", "its", "any", "all", "each", "per", "via", "after", "before", "during", "between", "than",
    "then", "they", "them", "there", "these", "those", "been", "being", "will", "would", "should", "could",
    "can", "not", "but", "out", "off", "over", "under", "also", "only", "same", "other", "new",
})
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_UNIT_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*-?\s*(hours?|hrs?|h|minutes?|mins?|days?|d|seconds?|secs?|s|milliseconds?|ms)\b",
)
_UNIT_CANON = {
    "hour": "h", "hours": "h", "hr": "h", "hrs": "h", "h": "h",
    "minute": "m", "minutes": "m", "min": "m", "mins": "m",
    "day": "d", "days": "d", "d": "d",
    "second": "s", "seconds": "s", "sec": "s", "secs": "s", "s": "s",
    "millisecond": "ms", "milliseconds": "ms", "ms": "ms",
}


def _normalise_units(text: str) -> str:
    return _UNIT_RE.sub(lambda m: f"{m.group(1)}{_UNIT_CANON.get(m.group(2), m.group(2))}", text)


def _stem(token: str) -> str:
    if not token.isalpha() or len(token) < 5:
        return token
    if token.endswith("ies"):
        return token[:-3] + "y"
    if token.endswith(("sses", "shes", "ches", "xes", "zes")):
        token = token[:-2]
    elif token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    elif token.endswith(("ing", "ed")) and len(token) > 5:
        token = token[:-3] if token.endswith("ing") else token[:-2]
    # "route", "routed" and "routing" all reduce to "rout".
    return token[:-1] if token.endswith("e") and len(token) > 4 else token


def tokens(text: str) -> set[str]:
    """Content tokens of ``text``, unit-normalised (``48-hour`` -> ``48h``) and
    lightly stemmed; compound identifiers are kept whole and in parts."""
    found: set[str] = set()
    for token in _TOKEN_RE.findall(_normalise_units(text.lower())):
        parts = [token, *re.split(r"[._-]", token)] if re.search(r"[._-]", token) else [token]
        for part in parts:
            if len(part) > 1 and part not in _STOPWORDS:
                found.add(_stem(part))
    return found


def _term_present(term: str, evidence_tokens: set[str]) -> bool:
    wanted = tokens(term)
    if not wanted:
        return True
    hits = len(wanted & evidence_tokens)
    return hits == len(wanted) if len(wanted) < 3 else hits >= math.ceil(len(wanted) * 2 / 3)


def facet_supported(facet: Facet, evidence_tokens: set[str]) -> bool:
    """Whether enough of the facet's terms occur in the evidence. A facet with
    no terms is not tested here."""
    if not facet.terms:
        return True
    present = sum(1 for term in facet.terms if _term_present(term, evidence_tokens))
    return present >= max(1, math.ceil(len(facet.terms) * 2 / 3))


def facet_stated(facet: Facet, evidence_tokens: set[str]) -> bool:
    """`facet_supported`, reading a facet with no terms by its own words."""
    return facet_supported(facet, evidence_tokens) if facet.terms else _term_present(facet.text, evidence_tokens)


def uncovered_facets(plan: FacetPlan | None, evidence_texts: "Iterable[str]") -> list[Facet]:
    """Facets of ``plan`` the texts do not lexically cover, in plan order."""
    if plan is None:
        return []
    evidence_tokens: set[str] = set()
    for text in evidence_texts:
        evidence_tokens |= tokens(text)
    return [facet for facet in plan.facets if not facet_supported(facet, evidence_tokens)]


__all__ = [
    "MAX_FACETS",
    "Facet",
    "FacetPlan",
    "facet_stated",
    "facet_supported",
    "parse_facet_plan",
    "plan_facets",
    "tokens",
    "uncovered_facets",
]
