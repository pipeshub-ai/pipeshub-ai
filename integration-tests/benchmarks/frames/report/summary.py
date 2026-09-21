"""Aggregating per-question scores into the board.

Accuracy is averaged over repeats per question first, then bootstrapped over
questions. Pairwise comparisons use each question's majority outcome across
repeats (ties count as wrong) in an exact McNemar test.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from itertools import combinations

from pydantic import BaseModel

from benchmarks.frames.config import StatsConfig
from benchmarks.frames.metrics.retrieval import ndcg_at_k, recall_at_k, reciprocal_rank
from benchmarks.frames.metrics.stats import bootstrap_mean, cohen_kappa, mcnemar_exact, percentile
from benchmarks.frames.models import QuestionScore, RankedList, RunMeta

RECALL_KS = (5, 10, 20, 50, 100)
GOLD_BUCKETS = ("2", "3", "4", "5+")


class Rate(BaseModel):
    value: float
    low: float
    high: float
    n: int


class SystemSummary(BaseModel):
    system: str
    questions: int
    repeats: int
    accuracy: Rate | None = None
    accuracy_secondary: Rate | None = None
    strict_accuracy: Rate | None = None
    per_repeat_accuracy: list[float] = []
    hedge_rate: float | None = None
    leniency_gap: float | None = None
    judge_agreement_kappa: float | None = None
    context_recall: Rate | None = None
    all_gold_in_context: Rate | None = None
    surfaced_recall: float | None = None
    recall_at: dict[str, float] = {}
    mrr: float | None = None
    ndcg_at_10: float | None = None
    citation_integrity: float | None = None
    cited_precision: float | None = None
    cited_recall: float | None = None
    all_hops_cited: float | None = None
    alce_recall: float | None = None
    alce_precision: float | None = None
    correct_and_grounded: float | None = None
    failures: dict[str, int] = {}
    by_reasoning_type: dict[str, float] = {}
    by_gold_count: dict[str, float] = {}
    error_rate: float = 0.0
    policy_violations: int = 0
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    cost_usd: float | None = None
    cost_per_correct_usd: float | None = None
    cost_per_question_usd: float | None = None
    # Share of answered predictions whose token usage was reported; cost is
    # only shown when this is 1.0, so a partial sum never reads as the total.
    usage_coverage: float | None = None
    input_tokens_per_question: float | None = None
    output_tokens_per_question: float | None = None
    llm_calls_per_question: float | None = None


class PairwiseTest(BaseModel):
    a: str
    b: str
    a_only: int
    b_only: int
    p_value: float


class RunSummary(BaseModel):
    run_id: str
    valid: bool
    violations: list[str]
    systems: list[SystemSummary]
    pairwise: list[PairwiseTest]
    meta: RunMeta | None = None


def _mean(values: Iterable[float | None]) -> float | None:
    kept = [v for v in values if v is not None]
    return sum(kept) / len(kept) if kept else None


def _per_question(scores: Sequence[QuestionScore], value: Callable[[QuestionScore], float | bool | None]) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for score in scores:
        v = value(score)
        if v is not None:
            grouped[score.question_id].append(float(v))
    # Sorted so the bootstrap's index sampling is independent of insertion order.
    return {qid: sum(vs) / len(vs) for qid, vs in sorted(grouped.items())}


def _rate(by_question: dict[str, float], stats: StatsConfig, seed: int) -> Rate | None:
    ordered = [by_question[qid] for qid in sorted(by_question)]
    interval = bootstrap_mean(ordered, samples=stats.bootstrap_samples, confidence=stats.confidence, seed=seed)
    return Rate(value=interval.value, low=interval.low, high=interval.high, n=len(by_question)) if interval else None


def _gold_bucket(count: int) -> str:
    return "5+" if count >= 5 else str(count)


def _breakdown(scores: Sequence[QuestionScore], key: Callable[[QuestionScore], Iterable[str]]) -> dict[str, float]:
    correct = _per_question(scores, lambda s: s.correct)
    groups: dict[str, list[float]] = defaultdict(list)
    seen: set[tuple[str, int]] = set()
    for score in scores:
        for label in key(score):
            if score.question_id in correct and (label, score.question_id) not in seen:
                seen.add((label, score.question_id))
                groups[label].append(correct[score.question_id])
    return {label: sum(v) / len(v) for label, v in sorted(groups.items())}


def _ranking_metrics(rankings: Sequence[RankedList], gold_for: Callable[[int], list[str]]) -> tuple[dict[str, float], float | None, float | None]:
    if not rankings:
        return {}, None, None
    recall_at = {str(k): _mean(recall_at_k(r.ranked_refs, gold_for(r.question_id), k) for r in rankings) for k in RECALL_KS}
    mrr = _mean(reciprocal_rank(r.ranked_refs, gold_for(r.question_id)) for r in rankings)
    ndcg = _mean(ndcg_at_k(r.ranked_refs, gold_for(r.question_id), 10) for r in rankings)
    return {k: v for k, v in recall_at.items() if v is not None}, mrr, ndcg


def _judge_quality(scores: Sequence[QuestionScore]) -> tuple[float | None, float | None, float | None]:
    """(hedge rate, leniency gap, primary-vs-secondary kappa)."""
    strict = [s for s in scores if s.strict_label is not None]
    hedge = _mean(1.0 if s.strict_label == "NOT_ATTEMPTED" else 0.0 for s in strict)
    lenient = [s for s in strict if s.correct]
    gap = _mean(1.0 if s.strict_label == "INCORRECT" else 0.0 for s in lenient)
    paired = [s for s in scores if s.correct is not None and s.secondary_correct is not None]
    kappa = cohen_kappa([bool(s.correct) for s in paired], [bool(s.secondary_correct) for s in paired])
    return hedge, gap, kappa


def _cost(scores: Sequence[QuestionScore]) -> dict[str, float | None]:
    answered = [s for s in scores if s.error_kind is None]
    coverage = _mean(1.0 if s.input_tokens is not None else 0.0 for s in answered)
    usage = {
        "usage_coverage": coverage,
        "input_tokens_per_question": _mean(s.input_tokens for s in answered),
        "output_tokens_per_question": _mean(s.output_tokens for s in answered),
        "llm_calls_per_question": _mean(s.llm_calls for s in answered),
    }
    if coverage != 1.0 or any(s.cost_usd is None for s in answered):
        return {**usage, "cost_usd": None, "cost_per_correct_usd": None, "cost_per_question_usd": None}
    # Failed predictions still spent tokens; count whatever they reported.
    total = sum(s.cost_usd or 0.0 for s in scores)
    correct = sum(1 for s in scores if s.correct)
    return {
        **usage, "cost_usd": total, "cost_per_question_usd": total / len(scores),
        "cost_per_correct_usd": total / correct if correct else None,
    }


def summarize_system(
    system: str, scores: Sequence[QuestionScore], rankings: Sequence[RankedList],
    gold_for: Callable[[int], list[str]], stats: StatsConfig, seed: int,
) -> SystemSummary:
    hedge, gap, kappa = _judge_quality(scores)
    recall_at, mrr, ndcg = _ranking_metrics(rankings, gold_for)
    repeats = sorted({s.repeat for s in scores})
    latencies = [float(s.latency_ms) for s in scores]
    return SystemSummary(
        system=system, questions=len({s.question_id for s in scores}), repeats=len(repeats),
        accuracy=_rate(_per_question(scores, lambda s: s.correct), stats, seed),
        accuracy_secondary=_rate(_per_question(scores, lambda s: s.secondary_correct), stats, seed),
        strict_accuracy=_rate(_per_question(scores, lambda s: None if s.strict_label is None else s.strict_label == "CORRECT"), stats, seed),
        per_repeat_accuracy=[_mean(float(bool(s.correct)) for s in scores if s.repeat == r) or 0.0 for r in repeats],
        hedge_rate=hedge, leniency_gap=gap, judge_agreement_kappa=kappa,
        context_recall=_rate(_per_question(scores, lambda s: s.context_recall), stats, seed),
        all_gold_in_context=_rate(_per_question(scores, lambda s: s.all_gold_in_context), stats, seed),
        surfaced_recall=_mean(s.surfaced_recall for s in scores),
        recall_at=recall_at, mrr=mrr, ndcg_at_10=ndcg,
        citation_integrity=_mean(None if s.citation_integrity is None else float(s.citation_integrity) for s in scores),
        cited_precision=_mean(s.cited_precision for s in scores),
        cited_recall=_mean(s.cited_recall for s in scores),
        all_hops_cited=_mean(None if s.all_hops_cited is None else float(s.all_hops_cited) for s in scores),
        alce_recall=_mean(s.alce_recall for s in scores),
        alce_precision=_mean(s.alce_precision for s in scores),
        correct_and_grounded=_mean(None if s.grounded is None else float(s.grounded) for s in scores),
        failures=dict(Counter(s.failure for s in scores if s.failure)),
        by_reasoning_type=_breakdown(scores, lambda s: s.labels),
        by_gold_count=_breakdown(scores, lambda s: [_gold_bucket(s.gold_count)]),
        error_rate=_mean(1.0 if s.error_kind else 0.0 for s in scores) or 0.0,
        policy_violations=sum(1 for s in scores if s.policy_violation),
        latency_p50_ms=percentile(latencies, 50), latency_p95_ms=percentile(latencies, 95),
        **_cost(scores),
    )


def _majority(scores: Sequence[QuestionScore]) -> dict[str, bool]:
    return {qid: value > 0.5 for qid, value in _per_question(scores, lambda s: s.correct).items()}


def pairwise_tests(by_system: dict[str, list[QuestionScore]]) -> list[PairwiseTest]:
    majorities = {system: _majority(scores) for system, scores in by_system.items()}
    tests = []
    for a, b in combinations(sorted(majorities), 2):
        common = sorted(set(majorities[a]) & set(majorities[b]))
        result = mcnemar_exact([majorities[a][q] for q in common], [majorities[b][q] for q in common])
        tests.append(PairwiseTest(a=a, b=b, a_only=result.a_only, b_only=result.b_only, p_value=result.p_value))
    return tests


def summarize(
    run_id: str, scores: Sequence[QuestionScore], rankings: Sequence[RankedList],
    gold_for: Callable[[int], list[str]], stats: StatsConfig, seed: int,
    violations: Sequence[str] = (), meta: RunMeta | None = None,
) -> RunSummary:
    by_system: dict[str, list[QuestionScore]] = defaultdict(list)
    for score in scores:
        by_system[score.system].append(score)
    rankings_by_system: dict[str, list[RankedList]] = defaultdict(list)
    for ranking in rankings:
        rankings_by_system[ranking.system].append(ranking)
    systems = [
        summarize_system(system, by_system[system], rankings_by_system.get(system, []), gold_for, stats, seed)
        for system in sorted(by_system)
    ]
    return RunSummary(
        run_id=run_id, valid=not violations, violations=list(violations),
        systems=systems, pairwise=pairwise_tests(by_system), meta=meta,
    )
