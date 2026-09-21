"""Answer judges: the FRAMES auto-rater (headline) and the SimpleQA strict
grader (catches hedged answers the containment rubric lets through).

Both follow one template: render the pinned prompt, ask at temperature 0
(cached), parse the verdict, and re-ask once with a format reminder if it
cannot be parsed. An answer that does not exist is graded without a call.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from benchmarks.harness.grading.prompts import (
    FRAMES_AUTORATER,
    FRAMES_JUDGE_SYSTEM_PROMPT,
    SIMPLEQA_GRADER,
    PromptTemplate,
    render_frames_autorater,
    render_simpleqa_grader,
)
from benchmarks.harness.grading.verdicts import parse_frames_decision, parse_simpleqa_grade
from benchmarks.harness.llm.client import ChatMessage, LLMClient, LLMRequest, LLMResponse, ResolvedModel
from benchmarks.harness.models import Judgment, Prediction, Question, answer_fingerprint

JudgeRole = Literal["primary", "secondary"]
JUDGE_MAX_TOKENS = 1024
UNPARSEABLE = "UNPARSEABLE"


@dataclass(frozen=True)
class GradingSubject:
    system: str
    question_id: int
    repeat: int
    question: str
    gold_answer: str
    predicted: str
    answer_sha: str
    has_error: bool = False

    @classmethod
    def of(cls, prediction: Prediction, question: Question) -> GradingSubject:
        return cls(
            system=prediction.system, question_id=prediction.question_id, repeat=prediction.repeat,
            question=question.prompt, gold_answer=question.answer, predicted=prediction.answer,
            answer_sha=answer_fingerprint(prediction.answer), has_error=prediction.error is not None,
        )


class AnswerJudge(ABC):
    # Registry key, not a closed literal — see `grading/registry.py`.
    rubric: str
    template: PromptTemplate
    missing_answer_label: str
    reask_instruction: str

    def __init__(self, llm: LLMClient, model: ResolvedModel, role: JudgeRole) -> None:
        self._llm = llm
        self._model = model
        self.role = role

    @abstractmethod
    def _render(self, subject: GradingSubject) -> tuple[ChatMessage, ...]: ...

    @staticmethod
    @abstractmethod
    def _parse(text: str) -> str | None: ...

    def _ask(self, messages: tuple[ChatMessage, ...]) -> LLMResponse:
        return self._llm.complete(LLMRequest(
            model=self._model, messages=messages, temperature=0.0,
            max_tokens=JUDGE_MAX_TOKENS, prompt_version=self.template.version, cacheable=True,
        ))

    def _judgment(self, subject: GradingSubject, **fields: object) -> Judgment:
        return Judgment(
            system=subject.system, question_id=subject.question_id, repeat=subject.repeat,
            answer_sha=subject.answer_sha, rubric=self.rubric, role=self.role,
            judge_model=self._model.label, prompt_version=self.template.version, **fields,
        )

    def grade(self, subject: GradingSubject) -> Judgment:
        if subject.has_error or not subject.predicted.strip():
            return self._judgment(subject, label=self.missing_answer_label, parse_ok=True, raw="(no answer)")
        messages = self._render(subject)
        first = self._ask(messages)
        label = self._parse(first.text)
        if label is not None:
            return self._judgment(
                subject, label=label, parse_ok=True, raw=first.text,
                cache_hit=first.cached, cost_usd=first.cost_usd,
            )
        retry = self._ask((
            *messages,
            ChatMessage(role="assistant", content=first.text),
            ChatMessage(role="user", content=self.reask_instruction),
        ))
        label = self._parse(retry.text)
        return self._judgment(
            subject, label=label or UNPARSEABLE, parse_ok=label is not None, reasked=True,
            raw=f"{first.text}\n---\n{retry.text}", cache_hit=first.cached and retry.cached,
            cost_usd=(first.cost_usd or 0.0) + (retry.cost_usd or 0.0),
        )


class FramesJudge(AnswerJudge):
    rubric = "frames"
    template = FRAMES_AUTORATER
    missing_answer_label = "FALSE"
    reask_instruction = (
        "Your reply did not end with the required line. Reply again and end with exactly "
        'one line: "Decision:" "TRUE" or "Decision:" "FALSE".'
    )

    def _render(self, subject: GradingSubject) -> tuple[ChatMessage, ...]:
        prompt = render_frames_autorater(subject.question, subject.predicted, subject.gold_answer)
        return (
            ChatMessage(role="system", content=FRAMES_JUDGE_SYSTEM_PROMPT),
            ChatMessage(role="user", content=prompt),
        )

    @staticmethod
    def _parse(text: str) -> str | None:
        return parse_frames_decision(text)


class StrictJudge(AnswerJudge):
    rubric = "strict"
    template = SIMPLEQA_GRADER
    missing_answer_label = "NOT_ATTEMPTED"
    reask_instruction = 'Just return the letters "A", "B", or "C", with no text around it.'

    def _render(self, subject: GradingSubject) -> tuple[ChatMessage, ...]:
        prompt = render_simpleqa_grader(subject.question, subject.gold_answer, subject.predicted)
        return (ChatMessage(role="user", content=prompt),)

    @staticmethod
    def _parse(text: str) -> str | None:
        return parse_simpleqa_grade(text)
