"""Typed questions and answers for decision models, independent of the provider.

A decision model reads some input and answers typed questions with calibrated
probabilities instead of generated text:

- ``predicate``: how likely a condition is true (OpenAI ``predicate``, TypeSafe ``noul``);
- ``choice``: one option out of a fixed set;
- ``score``: a position on an ordered scale of levels.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

# Plain text, or structured state. Providers that only take text get it as JSON.
DecisionInput = str | dict[str, Any] | list[Any]

MAX_CHOICE_OPTIONS = 255
MIN_SCORE_LEVELS = 2
MAX_SCORE_LEVELS = 10


class _Question(BaseModel):
    name: str = Field(min_length=1, max_length=128, description="Key the answer comes back under")
    instructions: str = Field(min_length=1, description="What to decide, in plain language")


class PredicateQuestion(_Question):
    type: Literal["predicate"] = "predicate"


class DecisionOption(BaseModel):
    value: str = Field(min_length=1)
    description: str | None = None


class ChoiceQuestion(_Question):
    type: Literal["choice"] = "choice"
    options: list[DecisionOption] = Field(min_length=2, max_length=MAX_CHOICE_OPTIONS)

    @model_validator(mode="after")
    def _unique_values(self) -> ChoiceQuestion:
        values = [option.value for option in self.options]
        if len(set(values)) != len(values):
            raise ValueError(f"Choice '{self.name}' has duplicate option values")
        return self


class ScoreLevel(BaseModel):
    label: str = Field(min_length=1)
    description: str | None = None


class ScoreQuestion(_Question):
    """Levels go from lowest to highest; the answer's score is a level index."""

    type: Literal["score"] = "score"
    levels: list[ScoreLevel] = Field(min_length=MIN_SCORE_LEVELS, max_length=MAX_SCORE_LEVELS)


DecisionQuestion = Annotated[
    PredicateQuestion | ChoiceQuestion | ScoreQuestion, Field(discriminator="type")
]


class DecisionAnswer(BaseModel):
    """One answer. Only the fields of its question's type are set.

    - predicate: ``probability`` (0..1).
    - choice: ``choice`` and ``probabilities`` keyed by option value.
    - score: ``score`` (probability-weighted level index, may fall between levels) and
      ``probabilities`` keyed by level label.
    ``refused`` (the provider declined) and ``missing`` (the response had no
    readable answer for it, often a wrong endpoint or format) carry no values;
    the caller decides the fallback.
    """

    name: str
    type: Literal["predicate", "choice", "score"]
    refused: bool = False
    missing: bool = False
    probability: float | None = None
    choice: str | None = None
    score: float | None = None
    probabilities: dict[str, float] = Field(default_factory=dict)
    confidence: float | None = None


class DecisionUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None


class DecisionResult(BaseModel):
    model: str
    answers: dict[str, DecisionAnswer]
    usage: DecisionUsage = Field(default_factory=DecisionUsage)


class DecisionModelError(RuntimeError):
    """A decision request failed. ``status_code`` is the provider's HTTP status, if any."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
