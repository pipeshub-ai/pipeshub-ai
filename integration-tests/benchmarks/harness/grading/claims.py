"""ALCE-style claim support (Gao et al., 2023) with LongCite's 3-level scale.

The answer is split into statements; each statement is judged against the
concatenation of the blocks it cites. For a fully supported statement with
several citations, each citation is also tested for necessity: it is
irrelevant iff it supports nothing alone AND the rest still fully support the
statement (ALCE's precision definition).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import pysbd

from benchmarks.harness.grading.prompts import CLAIM_SUPPORT, render_claim_support
from benchmarks.harness.grading.verdicts import parse_support
from benchmarks.harness.llm.client import ChatMessage, LLMClient, LLMRequest, ResolvedModel
from benchmarks.harness.citation_markers import cited_indices, strip_markers
from benchmarks.harness.models import Citation, ClaimSupport, Prediction, answer_fingerprint

_MIN_CLAIM_CHARS = 20
_EVIDENCE_CHARS = 4000
_segmenter = pysbd.Segmenter(language="en", clean=False)


@dataclass(frozen=True)
class Claim:
    index: int
    text: str
    cited: tuple[int, ...]


def split_claims(answer: str, max_claims: int) -> list[Claim]:
    claims: list[Claim] = []
    for sentence in _segmenter.segment(answer or ""):
        cited = cited_indices(sentence)
        text = strip_markers(sentence)
        if len(text) >= _MIN_CLAIM_CHARS:
            claims.append(Claim(index=len(claims), text=text, cited=cited))
        if len(claims) == max_claims:
            break
    return claims


class ClaimSupportJudge:
    def __init__(self, llm: LLMClient, model: ResolvedModel, title_for: Callable[[Citation], str]) -> None:
        self._llm = llm
        self._model = model
        self._title_for = title_for
        # Reset per `judge()`; initialised here so `_support` is callable alone.
        self._spent = 0.0

    def _support(self, statement: str, evidence: Sequence[Citation]) -> float:
        rendered = "\n\n".join(
            f"[{i}] Title: {self._title_for(c)}\n{c.content[:_EVIDENCE_CHARS]}" for i, c in enumerate(evidence, 1)
        )
        response = self._llm.complete(LLMRequest(
            model=self._model,
            messages=(ChatMessage(role="user", content=render_claim_support(rendered, statement)),),
            temperature=0.0, max_tokens=512, prompt_version=CLAIM_SUPPORT.version, cacheable=True,
        ))
        self._spent += response.cost_usd or 0.0
        return parse_support(response.text) or 0.0

    def _necessity(self, statement: str, evidence: Sequence[Citation]) -> list[bool]:
        necessary = []
        for position, citation in enumerate(evidence):
            rest = [c for i, c in enumerate(evidence) if i != position]
            irrelevant = self._support(statement, [citation]) == 0.0 and self._support(statement, rest) == 1.0
            necessary.append(not irrelevant)
        return necessary

    def judge(self, prediction: Prediction, max_claims: int) -> list[ClaimSupport]:
        self._spent = 0.0
        by_index = {c.display_index: c for c in prediction.citations if c.display_index is not None}
        answer_sha = answer_fingerprint(prediction.answer)
        results = []
        for claim in split_claims(prediction.answer, max_claims):
            evidence = [by_index[n] for n in claim.cited if n in by_index]
            support = self._support(claim.text, evidence) if evidence else 0.0
            if support == 1.0 and len(evidence) > 1:
                necessary = self._necessity(claim.text, evidence)
            else:
                necessary = [support > 0] * len(evidence)
            results.append(ClaimSupport(
                system=prediction.system, question_id=prediction.question_id, repeat=prediction.repeat,
                answer_sha=answer_sha, claim_index=claim.index,
                claim=claim.text, cited_display_indices=list(claim.cited), support=support,
                necessary=necessary, judge_model=self._model.label,
            ))
        # Attributed to the first claim: the calls are per-answer, not per-claim.
        if results:
            results[0] = results[0].model_copy(update={"cost_usd": self._spent})
        return results
