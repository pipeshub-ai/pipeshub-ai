"""Evidence support: was a correct answer derived from the context its system
showed the answering model, or from the model's training data?

Every system that retrieves is told to answer only from its sources; this is
where that is checked. It runs only on answers the primary judge marked
correct — a wrong answer needs no provenance — with the configured judge
(`grading.evidence_support.judge`) at temperature 0, cached like every other
judgment.

Evidence larger than the budget (`grading.evidence_support.max_evidence_tokens`)
is cut deterministically: split into paragraph-sized segments, scored by BM25
against the question and the answer, and the best segments are kept together
with the segment before and after each, in their original order.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from benchmarks.harness.grading.judges import JUDGE_MAX_TOKENS, UNPARSEABLE
from benchmarks.harness.grading.prompts import EVIDENCE_SUPPORT, render_evidence_support
from benchmarks.harness.grading.verdicts import parse_evidence_support, parse_reason
from benchmarks.harness.llm.client import ChatMessage, LLMClient, LLMRequest, LLMResponse, ResolvedModel
from benchmarks.harness.models import (
    NO_EVIDENCE,
    UNSUPPORTED,
    Evidence,
    EvidencePassage,
    EvidenceSelection,
    SupportJudgment,
    render_passages,
)
from benchmarks.harness.systems.baselines.bm25 import tokenize

CHARS_PER_TOKEN = 4
_SEGMENT_CHARS = 600
_REASK = (
    "Your reply did not end with the required line. Reply again and end with exactly one line: "
    '"Evidence support: SUPPORTED", "Evidence support: PARTIAL" or "Evidence support: UNSUPPORTED".'
)
_JUDGEABLE = frozenset({"captured", "reconstructed"})


def _segments(text: str) -> list[str]:
    """Paragraphs packed up to `_SEGMENT_CHARS`; a longer paragraph is cut."""
    pieces: list[str] = []
    for paragraph in (p for p in text.split("\n") if p.strip()):
        pieces.extend(paragraph[i:i + _SEGMENT_CHARS] for i in range(0, len(paragraph), _SEGMENT_CHARS))
    segments: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + 1 + len(piece) > _SEGMENT_CHARS:
            segments.append(current)
            current = piece
        else:
            current = f"{current}\n{piece}" if current else piece
    if current:
        segments.append(current)
    return segments


def select_passages(
    passages: Sequence[EvidencePassage], question: str, answer: str, budget_tokens: int,
) -> tuple[list[EvidencePassage], EvidenceSelection]:
    """The passages to show the judge, and a record of what was cut."""
    budget = budget_tokens * CHARS_PER_TOKEN
    total = len(render_passages(list(passages)))
    if total <= budget:
        return list(passages), EvidenceSelection(
            selected=False, budget_tokens=budget_tokens, segments_total=len(passages),
            segments_kept=len(passages), chars_total=total, chars_kept=total,
        )
    units = [(i, j, text) for i, p in enumerate(passages) for j, text in enumerate(_segments(p.text))]
    position = {(i, j): n for n, (i, j, _text) in enumerate(units)}
    scores = BM25Okapi([tokenize(text) or ["_"] for _i, _j, text in units]).get_scores(tokenize(f"{question} {answer}"))
    order = sorted(range(len(units)), key=lambda n: (-scores[n], n))

    def cost(n: int) -> int:
        # +3 covers the separator a kept segment adds when merged or joined.
        return len(units[n][2]) + len(passages[units[n][0]].header) + 3

    kept: set[int] = set()
    used = 0
    for n in order:
        if n in kept:
            continue
        i, j, _text = units[n]
        window = [m for m in (position.get((i, j - 1)), n, position.get((i, j + 1))) if m is not None and m not in kept]
        for group in (window, [n]):
            size = sum(cost(m) for m in group)
            if used + size <= budget:
                kept.update(group)
                used += size
                break
    groups: list[tuple[int, list[str]]] = []
    previous: tuple[int, int] | None = None
    for n in sorted(kept):
        index, segment, text = units[n]
        if previous is not None and previous[0] == index:
            groups[-1][1].append(("\n" if previous[1] == segment - 1 else "\n…\n") + text)
        else:
            groups.append((index, [text]))
        previous = (index, segment)
    selected = [EvidencePassage(header=passages[i].header, text="".join(parts)) for i, parts in groups]
    return selected, EvidenceSelection(
        selected=True, budget_tokens=budget_tokens, segments_total=len(units), segments_kept=len(kept),
        chars_total=total, chars_kept=len(render_passages(selected)),
    )


@dataclass(frozen=True)
class SupportSubject:
    system: str
    question_id: str
    repeat: int
    answer_sha: str
    question: str
    answer: str
    evidence: Evidence | None
    verifier: str
    passages: tuple[EvidencePassage, ...] = ()
    selection: EvidenceSelection | None = None

    @classmethod
    def of(
        cls, *, system: str, question_id: str, repeat: int, answer_sha: str, question: str, answer: str,
        evidence: Evidence | None, verifier: str, budget_tokens: int,
    ) -> SupportSubject:
        passages: tuple[EvidencePassage, ...] = ()
        selection = None
        if evidence is not None and evidence.status in _JUDGEABLE:
            chosen, selection = select_passages(evidence.passages, question, answer, budget_tokens)
            passages = tuple(chosen)
        return cls(
            system=system, question_id=question_id, repeat=repeat, answer_sha=answer_sha, question=question,
            answer=answer, evidence=evidence, verifier=verifier, passages=passages, selection=selection,
        )

    @property
    def evidence_sha(self) -> str:
        return self.evidence.sha256 if self.evidence is not None else ""

    @property
    def key(self) -> tuple[str, str, int, str, str, str]:
        return (self.system, self.question_id, self.repeat, self.answer_sha, self.evidence_sha, self.verifier)

    @property
    def needs_call(self) -> bool:
        return bool(self.passages)

    def prompt(self) -> str:
        return render_evidence_support(self.question, self.answer, render_passages(list(self.passages)))


def verifier_id(judge_model: str, budget_tokens: int) -> str:
    return f"{EVIDENCE_SUPPORT.version}:{judge_model}:{budget_tokens}"


class EvidenceSupportJudge:
    def __init__(self, llm: LLMClient, model: ResolvedModel | None) -> None:
        self._llm = llm
        self._model = model

    def _ask(self, messages: tuple[ChatMessage, ...]) -> LLMResponse:
        assert self._model is not None
        return self._llm.complete(LLMRequest(
            model=self._model, messages=messages, temperature=0.0, max_tokens=JUDGE_MAX_TOKENS,
            prompt_version=EVIDENCE_SUPPORT.version, cacheable=True,
        ))

    def _judgment(self, subject: SupportSubject, **fields: object) -> SupportJudgment:
        evidence = subject.evidence
        return SupportJudgment(
            system=subject.system, question_id=subject.question_id, repeat=subject.repeat,
            answer_sha=subject.answer_sha, evidence_sha=subject.evidence_sha, verifier=subject.verifier,
            evidence_status=evidence.status if evidence is not None else "missing",
            selection=subject.selection, **fields,
        )

    def judge(self, subject: SupportSubject) -> SupportJudgment:
        evidence = subject.evidence
        if evidence is None:
            return self._judgment(subject, label=NO_EVIDENCE, reason="no evidence was recorded for this answer")
        if evidence.status == "unavailable":
            return self._judgment(subject, label=NO_EVIDENCE, reason=evidence.reason or "evidence unavailable")
        if not subject.needs_call:
            return self._judgment(subject, label=UNSUPPORTED, reason="the system was shown no context")
        messages = (ChatMessage(role="user", content=subject.prompt()),)
        first = self._ask(messages)
        label = parse_evidence_support(first.text)
        spent = _paid(first)
        raw, last, reasked, cache_hit = first.text, first.text, False, first.cached
        if label is None:
            retry = self._ask((
                *messages,
                ChatMessage(role="assistant", content=first.text),
                ChatMessage(role="user", content=_REASK),
            ))
            label = parse_evidence_support(retry.text)
            spent += _paid(retry)
            raw, last, reasked, cache_hit = f"{first.text}\n---\n{retry.text}", retry.text, True, first.cached and retry.cached
        assert self._model is not None
        return self._judgment(
            subject, label=label or UNPARSEABLE, reason=parse_reason(last) or parse_reason(raw),
            judged=True, judge_model=self._model.label, prompt_version=EVIDENCE_SUPPORT.version,
            parse_ok=label is not None, reasked=reasked, raw=raw, cache_hit=cache_hit, cost_usd=spent,
        )


def _paid(response: LLMResponse) -> float:
    """A cache hit was paid for by an earlier run; counting it again would
    trip the run's cost limit on a free re-grade."""
    return 0.0 if response.cached else (response.cost_usd or 0.0)
