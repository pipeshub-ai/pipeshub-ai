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
from app.agents.agent_loop.coverage.checker import CheckerReport, check_draft
from app.agents.agent_loop.coverage.checks import (
    find_admissions,
    mentions_conflict,
    near_duplicate_reads,
    unread_references,
)
from app.agents.agent_loop.coverage.evidence import Evidence, evidence_digest
from app.agents.agent_loop.coverage.facets import FacetPlan, tokens, uncovered_facets
from app.agents.agent_loop.coverage.state import query_key

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from langchain_core.messages import BaseMessage

GapKind = Literal["facet", "reference", "admission"]


@dataclass(frozen=True)
class Gap:
    kind: GapKind
    label: str
    query: str


@dataclass
class GateDecision:
    gaps: list[Gap] = field(default_factory=list)
    rulings: list[str] = field(default_factory=list)
    checked: bool = False
    signals: dict[str, int] = field(default_factory=dict)

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


def gaps_from_report(report: CheckerReport, plan: FacetPlan | None) -> list[Gap]:
    gaps: list[Gap] = []
    for verdict in report.facets:
        if verdict.supported:
            continue
        facet = plan.get(verdict.id) if plan else None
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
) -> list[str]:
    named = plan.document_types if plan else []
    rulings: list[str] = []
    for conflict in report.conflicts:
        ruling = adjudicate(conflict, named)
        if ruling is None or not draft_disagrees(ruling, conflict):
            continue
        facet = plan.get(conflict.facet) if plan else None
        scopes = {value.record: value.scope for value in conflict.values if value.scope}
        rulings.append(render_ruling(ruling, scopes, evidence, facet.text if facet else conflict.facet, name_of))
    return rulings


def _default_name(record_id: str | None, name: str) -> str:
    return f"{name} ({record_id})" if name and record_id else name or record_id or "a record"


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

    focus = [facet.text for facet in plan.facets] if plan else [question]
    report = await check_draft(
        llm, question=question, plan=plan, draft=draft,
        evidence_digest=evidence_digest(evidence, focus_terms=[*focus, *admissions], cited_record_ids=cited_record_ids),
        searched=searched, timeout=timeout, on_usage=on_usage,
    )
    if report is None:
        gaps = fallback_gaps(plan, evidence, admissions)
        rulings: list[str] = []
    else:
        gaps = gaps_from_report(report, plan)
        rulings = rulings_from_report(report, plan, evidence, name_of) if adjudication else []
    signals["checker"] = int(report is not None)
    return GateDecision(
        gaps=_new_gaps(gaps, searched, max_gaps), rulings=rulings, checked=True, signals=signals,
    )


__all__ = [
    "Gap",
    "GateDecision",
    "assess_draft",
    "fallback_gaps",
    "gaps_from_report",
    "rulings_from_report",
]
