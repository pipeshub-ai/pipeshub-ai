"""The coverage decision for one draft: which parts lack support, which named
references were not read, which near misses the draft concedes, and which
record a conflicting part should rest on.

Deterministic checks run first; only when one of them fires does the checker
model see the draft, so a covered draft costs nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from app.agents.agent_loop.coverage.adjudication import (
    SET_ASIDE_DOC_TYPE,
    SET_ASIDE_REVISED,
    SET_ASIDE_SCOPE,
    Ruling,
    adjudicate,
    draft_disagrees,
)
from app.agents.agent_loop.coverage.checker import (
    CheckerReport,
    check_conflicts,
    check_draft,
)
from app.agents.agent_loop.coverage.checks import (
    find_admissions,
    mentions_conflict,
    near_duplicate_reads,
    unread_references,
)
from app.agents.agent_loop.coverage.evidence import (
    Evidence,
    evidence_digest,
    record_ref,
)
from app.agents.agent_loop.coverage.facets import (
    FacetPlan,
    facet_stated,
    tokens,
    uncovered_facets,
)
from app.agents.agent_loop.coverage.state import query_key

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Mapping

    from langchain_core.messages import BaseMessage

GapKind = Literal["facet", "reference", "admission"]


@dataclass(frozen=True)
class Gap:
    kind: GapKind
    label: str
    query: str


@dataclass
class GateDecision:
    gaps: list[Gap] = field(default_factory=list[Gap])
    rulings: list[str] = field(default_factory=list[str])
    checked: bool = False
    signals: dict[str, int] = field(default_factory=dict[str, int])

    @property
    def covered(self) -> bool:
        return not self.gaps and not self.rulings


def _new_gaps(gaps: "Iterable[Gap]", searched: "Iterable[str]", limit: int) -> list[Gap]:
    done = {query_key(query) for query in searched}
    kept: list[Gap] = []
    for gap in gaps:
        key = query_key(gap.query)
        if not key or key in done:
            continue
        done.add(key)
        kept.append(gap)
        if len(kept) >= limit:
            break
    return kept


def _facets_for(sentence: str, plan: FacetPlan | None) -> list[Gap]:
    if plan is None:
        return []
    words = tokens(sentence)
    return [
        Gap("admission", sentence, facet.query)
        for facet in plan.facets
        if facet.query and words & (tokens(facet.text) | {t for term in facet.terms for t in tokens(term)})
    ]


def fallback_gaps(
    plan: FacetPlan | None, evidence: Evidence, admissions: list[str],
) -> list[Gap]:
    """Gaps from the deterministic checks alone, for when the checker gave nothing."""
    gaps = [
        Gap("facet", facet.text, facet.query)
        for facet in uncovered_facets(plan, evidence.texts()) if facet.kind != "reference"
    ]
    gaps += [Gap("reference", ref.text, ref.query or ref.text) for ref in unread_references(plan, evidence)]
    for sentence in admissions:
        gaps += _facets_for(sentence, plan)
    return gaps


def gaps_from_report(
    report: CheckerReport, plan: FacetPlan | None, cited_texts: "Iterable[str]" = (),
) -> list[Gap]:
    """Gaps the checker reports. A part it calls unsupported that the passages
    the draft cites state lexically is not a gap: the checker saw excerpts and
    can miss the cited rows."""
    cited_tokens: set[str] = set()
    for text in cited_texts:
        cited_tokens |= tokens(text)
    gaps: list[Gap] = []
    for verdict in report.facets:
        if verdict.supported:
            continue
        facet = plan.get(verdict.id) if plan else None
        if facet is not None and cited_tokens and facet_stated(facet, cited_tokens):
            continue
        query = verdict.query or (facet.query if facet else "")
        gaps.append(Gap("facet", facet.text if facet else verdict.id, query))
    gaps += [Gap("reference", ref.name, ref.query or ref.name) for ref in report.unread_references]
    gaps += [Gap("admission", item.quote, item.query) for item in report.admissions if item.query]
    return gaps


_WHY = {
    SET_ASIDE_SCOPE: "applies to another scope ({scope}), so it is not a version of this answer: leave it out",
    SET_ASIDE_REVISED: "is revised by {preferred}: give its value only as the earlier one",
    SET_ASIDE_DOC_TYPE: "is not the kind of document the question names: give it only as a labelled difference",
}


def _record_name(evidence: Evidence, label: str, name_of: "Callable[[str | None, str], str]") -> str:
    record = evidence.by_label(label)
    if record is None:
        return label
    return name_of(record.record_id, record.name)


def render_ruling(
    ruling: Ruling,
    report_conflict_scopes: dict[str, str],
    evidence: Evidence,
    facet_text: str,
    name_of: "Callable[[str | None, str], str]",
) -> str:
    preferred = ", ".join(_record_name(evidence, label, name_of) for label in ruling.preferred)
    lines = [f"For {facet_text or 'this part'}: lead with the value from {preferred}."]
    for label, why in ruling.set_aside:
        scope = report_conflict_scopes.get(label) or "not the one asked about"
        lines.append(f"{_record_name(evidence, label, name_of)} {_WHY[why].format(scope=scope, preferred=preferred)}.")
    return " ".join(lines)


def rulings_from_report(
    report: CheckerReport,
    plan: FacetPlan | None,
    evidence: Evidence,
    name_of: "Callable[[str | None, str], str]",
    new_labels: "Collection[str]" = (),
) -> list[str]:
    """Rulings the draft does not already follow; with ``new_labels`` (records
    the draft has not seen), also any ruling that sets one of them aside."""
    named = plan.document_types if plan else []
    rulings: list[str] = []
    for conflict in report.conflicts:
        ruling = adjudicate(conflict, named)
        if ruling is None:
            continue
        sets_aside_new = any(label in new_labels for label, _why in ruling.set_aside)
        if not (draft_disagrees(ruling, conflict) or sets_aside_new):
            continue
        facet = plan.get(conflict.facet) if plan else None
        scopes = {value.record: value.scope for value in conflict.values if value.scope}
        rulings.append(render_ruling(ruling, scopes, evidence, facet.text if facet else conflict.facet, name_of))
    return rulings


def _default_name(record_id: str | None, name: str) -> str:
    return record_ref(record_id, name)


async def assess_draft(
    *,
    question: str,
    draft: str,
    plan: FacetPlan | None,
    evidence: Evidence,
    cited_record_ids: list[str],
    searched: list[str],
    llm: Any,  # noqa: ANN401
    timeout: float,
    max_gaps: int,
    adjudication: bool,
    cited_blocks: "Mapping[str, Collection[int]] | None" = None,
    name_of: "Callable[[str | None, str], str]" = _default_name,
    on_usage: "Callable[[BaseMessage], None] | None" = None,
) -> GateDecision:
    admissions = find_admissions(draft)
    references = unread_references(plan, evidence)
    uncovered = uncovered_facets(plan, evidence.texts())
    lookalikes = near_duplicate_reads(evidence, cited_record_ids) if adjudication else []
    conflict_words = adjudication and mentions_conflict(draft)
    signals = {
        "admissions": len(admissions), "unread_references": len(references), "uncovered": len(uncovered),
        "lookalikes": len(lookalikes), "conflict_words": int(conflict_words),
    }
    if not (admissions or references or uncovered or lookalikes or conflict_words):
        return GateDecision(signals=signals)

    cited = _cited_map(cited_record_ids, cited_blocks)
    focus = [facet.text for facet in plan.facets] if plan else [question]
    report = await check_draft(
        llm, question=question, plan=plan, draft=draft,
        evidence_digest=evidence_digest(
            evidence, focus_terms=[*focus, *admissions], cited_record_ids=cited_record_ids, cited_blocks=cited,
        ),
        searched=searched, timeout=timeout, on_usage=on_usage,
    )
    if report is None:
        gaps = fallback_gaps(plan, evidence, admissions)
        rulings: list[str] = []
    else:
        gaps = gaps_from_report(report, plan, evidence.cited_texts(cited))
        signals["cited_overrules"] = sum(not v.supported for v in report.facets) - sum(g.kind == "facet" for g in gaps)
        rulings = rulings_from_report(report, plan, evidence, name_of) if adjudication else []
    if admissions and not (references or uncovered or lookalikes or conflict_words):
        # A caveat alone reopens neither the parts nor the values the draft
        # answered: only what it concedes and documents named but not read.
        gaps = [gap for gap in gaps if gap.kind != "facet"]
        rulings = []
    signals["checker"] = int(report is not None)
    return GateDecision(
        gaps=_new_gaps(gaps, searched, max_gaps), rulings=rulings, checked=True, signals=signals,
    )


def _cited_map(
    cited_record_ids: "Iterable[str]", cited_blocks: "Mapping[str, Collection[int]] | None",
) -> dict[str, list[int]]:
    cited = {record_id: list(indices) for record_id, indices in (cited_blocks or {}).items()}
    for record_id in cited_record_ids:
        cited.setdefault(record_id, [])
    return cited


async def adjudicate_found(
    *,
    question: str,
    draft: str,
    plan: FacetPlan | None,
    evidence: Evidence,
    new_virtual_record_ids: "Collection[str]",
    cited_record_ids: list[str],
    llm: Any,  # noqa: ANN401
    timeout: float,
    cited_blocks: "Mapping[str, Collection[int]] | None" = None,
    name_of: "Callable[[str | None, str], str]" = _default_name,
    on_usage: "Callable[[BaseMessage], None] | None" = None,
) -> list[str]:
    """Rulings over records a search run in code added after the draft: which
    to lead with where one of them gives a different value from the draft's."""
    new = set(new_virtual_record_ids)
    new_labels = {
        f"R{position}" for position, record in enumerate(evidence.records, start=1)
        if record.virtual_record_id in new
    }
    if not new_labels:
        return []
    cited = _cited_map(cited_record_ids, cited_blocks)
    focus = [facet.text for facet in plan.facets] if plan else [question]
    report = await check_conflicts(
        llm, question=question, plan=plan, draft=draft,
        evidence_digest=evidence_digest(
            evidence, focus_terms=focus, cited_record_ids=cited_record_ids, cited_blocks=cited,
            new_virtual_record_ids=new,
        ),
        timeout=timeout, on_usage=on_usage,
    )
    if report is None:
        return []
    return rulings_from_report(report, plan, evidence, name_of, new_labels=new_labels)


__all__ = [
    "Gap",
    "GateDecision",
    "adjudicate_found",
    "assess_draft",
    "fallback_gaps",
    "gaps_from_report",
    "rulings_from_report",
]
