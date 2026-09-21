"""Turning one prediction plus its judgments into a `QuestionScore`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from benchmarks.harness.corpus.view import CorpusView
from benchmarks.harness.metrics.citations import alce_scores, cited_vs_gold, citation_integrity
from benchmarks.harness.metrics.failures import Failure, FailureEvidence, classify
from benchmarks.harness.metrics.mapping import ArticleResolver
from benchmarks.harness.metrics.retrieval import recall, tool_counts
from benchmarks.harness.models import ClaimSupport, Judgment, Prediction, Question, QuestionScore
from benchmarks.harness.systems.base import AdapterCapabilities

JudgmentSlot = tuple[str, str]  # (kind, role)


def _usage(p: Prediction) -> dict[str, int | None]:
    if not p.llm_calls:
        return {}
    return {
        "input_tokens": sum(c.input_tokens for c in p.llm_calls),
        "output_tokens": sum(c.output_tokens for c in p.llm_calls),
        "cached_tokens": sum(c.cached_tokens for c in p.llm_calls),
        "llm_calls": len(p.llm_calls),
    }


@dataclass(frozen=True)
class ScoringInputs:
    question: Question
    prediction: Prediction
    judgments: Mapping[JudgmentSlot, Judgment]
    claims: Sequence[ClaimSupport]


class Scorer:
    def __init__(
        self, resolver: ArticleResolver, corpus: CorpusView, capabilities: AdapterCapabilities,
        fuzzy_threshold: int, primary_rubric: str = "frames",
    ) -> None:
        self._resolver = resolver
        self._corpus = corpus
        self._capabilities = capabilities
        self._fuzzy_threshold = fuzzy_threshold
        # Which rubric's verdict is the headline `correct`. A dataset graded by
        # EM or F1 registers its own and names it here.
        self._primary_rubric = primary_rubric

    def score(self, inputs: ScoringInputs) -> QuestionScore:
        p, q = inputs.prediction, inputs.question
        gold = self._resolver.gold(q.gold_refs)
        primary = inputs.judgments.get((self._primary_rubric, "primary"))
        secondary = inputs.judgments.get((self._primary_rubric, "secondary"))
        strict = inputs.judgments.get(("strict", "primary"))
        n_calls, n_searches, n_fetches, _failed = tool_counts(p.trace)
        base = QuestionScore(
            system=p.system, question_id=q.id, repeat=p.repeat, split=q.split,
            labels=q.labels, gold_count=len(gold),
            correct=primary.correct if primary else None,
            secondary_correct=secondary.correct if secondary else None,
            strict_label=strict.label if strict else None,
            n_tool_calls=n_calls, n_searches=n_searches, n_fetches=n_fetches,
            tool_waves=p.trace.tool_waves if p.trace else 0,
            latency_ms=p.latency_ms, cost_usd=p.cost_usd,
            **_usage(p),
            error_kind=p.error.kind if p.error else None, policy_violation=bool(p.policy_violations),
        )
        update = {**self._retrieval(p, gold), **self._citations(p, gold, inputs.claims, base.correct)}
        scored = base.model_copy(update=update)
        return scored.model_copy(update={"failure": self._failure(p, scored, gold)})

    def _context(self, p: Prediction) -> tuple[set[str], set[str]]:
        if self._capabilities.retrieval_trace and p.trace is not None:
            events = p.trace.retrieval_events
            return self._resolver.context_urls(events), self._resolver.surfaced_urls(events)
        context = set(p.context_urls)
        return context, context

    def _retrieval(self, p: Prediction, gold: list[str]) -> dict[str, Any]:
        context, surfaced = self._context(p)
        context_recall = recall(context, gold)
        return {
            "context_recall": context_recall,
            "all_gold_in_context": None if context_recall is None else context_recall == 1.0,
            "surfaced_recall": recall(surfaced, gold),
            "missing_gold": [url for url in gold if url not in context],
        }

    def _citations(
        self, p: Prediction, gold: list[str], claims: Sequence[ClaimSupport], correct: bool | None,
    ) -> dict[str, Any]:
        if not self._capabilities.citations or p.error is not None:
            return {}
        cited = self._resolver.citation_urls(p.citations)
        precision, cited_recall, all_hops = cited_vs_gold(cited, gold)
        alce_recall, alce_precision = alce_scores(claims)
        return {
            "citation_integrity": citation_integrity(
                p.answer, p.citations, known_record_ids=self._resolver.known_record_ids,
                url_for=lambda c: self._resolver.url_for(c.record_id, c.record_name),
                text_for=self._corpus.text, threshold=self._fuzzy_threshold,
            ),
            "cited_precision": precision, "cited_recall": cited_recall, "all_hops_cited": all_hops,
            "alce_recall": alce_recall, "alce_precision": alce_precision,
            "grounded": None if correct is None or alce_recall is None else bool(correct and alce_recall == 1.0),
        }

    def _linked_from_context(self, missing: set[str], context: set[str]) -> frozenset[str]:
        linked: set[str] = set()
        for url in context:
            document = self._corpus.document(url)
            if document is not None:
                linked |= missing & set(document.outlinks)
        return frozenset(linked)

    def _failure(self, p: Prediction, score: QuestionScore, gold: list[str]) -> str | None:
        if not self._capabilities.retrieval_trace:
            return Failure.SYSTEM_ERROR.value if p.error is not None else None
        events = p.trace.retrieval_events if p.trace else []
        context, surfaced = self._context(p)
        missing = set(score.missing_gold)
        verdict = classify(FailureEvidence(
            correct=bool(score.correct), has_error=p.error is not None,
            policy_violation=score.policy_violation, strict_label=score.strict_label,
            context_recall=score.context_recall, tool_waves=score.tool_waves,
            hit_turn_cap=bool(p.trace and p.trace.run_stats and p.trace.run_stats.hit_turn_cap),
            failed_tool_calls=tool_counts(p.trace)[3], missing_gold=frozenset(missing),
            surfaced=frozenset(surfaced), unrendered_fetches=frozenset(self._resolver.unrendered_fetch_urls(events)),
            linked_from_context=self._linked_from_context(missing, context),
        ))
        return verdict.value if verdict else None
