"""The dataset seam.

A benchmark dataset is three things the harness cannot know: where its
questions come from, what its gold refs mean, and how an answer is judged
correct. Everything else — asking, retrieving, grading mechanics, scoring,
reporting, resume — is the same whatever the dataset.

Adding one means writing a `DatasetPlugin` and registering it. Nothing in
`stages.py`, `metrics/` or `report/` should need to change; if it does, the
seam is in the wrong place.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from benchmarks.frames.errors import ConfigError
from benchmarks.frames.models import CorpusManifest, Question

if TYPE_CHECKING:
    from benchmarks.frames.config import CorpusConfig, GradingConfig
    from benchmarks.frames.grading.judges import AnswerJudge
    from benchmarks.frames.services import Services


@dataclass(frozen=True)
class DatasetRevision:
    """What pins the question set, recorded in `RunMeta` so a published number
    can be traced to the exact rows it was computed from."""

    revision: str
    sha256: str


class CorpusSource(Protocol):
    """Builds the document set the systems under test will search.

    Implementations own their own resumability: `build` is called on every run
    and is expected to return quickly when the corpus is already on disk.
    """

    def build(
        self,
        refs: Sequence[str],
        *,
        tier: str,
        distractor_count: int,
        seed: int,
        max_failed_gold_ratio: float,
    ) -> CorpusManifest: ...


class DatasetPlugin(Protocol):
    """One benchmark dataset."""

    name: str

    def load(self, services: Services) -> list[Question]:
        """Every question, before splitting."""

    def revision(self, services: Services) -> DatasetRevision: ...

    def split_path(self, services: Services) -> Path:
        """The committed dev/held-out assignment for this dataset."""

    def normalize_ref(self, ref: str) -> str:
        """A gold ref reduced to the key the corpus is indexed by.

        Two refs that name the same document must normalize equal; a dataset
        whose refs are already corpus keys returns them unchanged.
        """

    def corpus_source(self, services: Services, config: CorpusConfig, corpus_dir: Path) -> CorpusSource:
        """Builds into `corpus_dir` — the harness owns cache layout, the
        dataset owns what a document is and where it comes from."""

    def judges(self, services: Services, grading: GradingConfig) -> list[AnswerJudge]:
        """The rubrics this dataset is graded by; the first is the primary."""

    @property
    def primary_rubric(self) -> str:
        """Which rubric's verdict becomes the headline `QuestionScore.correct`."""


_REGISTRY: dict[str, Callable[[], DatasetPlugin]] = {}


def register_dataset(name: str, factory: Callable[[], DatasetPlugin]) -> None:
    """Lazy factory, not an instance: importing the registry must not drag in
    every dataset's dependencies."""
    _REGISTRY[name] = factory


def dataset_plugin(name: str) -> DatasetPlugin:
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "none registered"
        raise ConfigError(f"unknown dataset {name!r} (known: {known})")
    return _REGISTRY[name]()


def registered_datasets() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))
