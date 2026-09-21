"""FRAMES as a `DatasetPlugin`.

Everything Wikipedia-shaped about the benchmark lives behind this file: the
HuggingFace TSV and its pinned revision, `wiki_links` as gold refs, MediaWiki
article fetching, and the paper's auto-rater rubric.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from benchmarks.frames.datasets import CorpusSource, DatasetRevision, register_dataset
from benchmarks.frames.dataset.loader import (
    FRAMES_REVISION,
    FRAMES_TSV_SHA256,
)
from benchmarks.frames.dataset.urls import normalize_wiki_url
from benchmarks.frames.models import Question

if TYPE_CHECKING:
    from benchmarks.frames.config import CorpusConfig, GradingConfig
    from benchmarks.frames.grading.judges import AnswerJudge
    from benchmarks.frames.services import Services


class FramesDataset:
    name = "frames"
    primary_rubric = "frames"

    def load(self, services: Services) -> list[Question]:
        return services.load_questions()

    def revision(self, _services: Services) -> DatasetRevision:
        return DatasetRevision(revision=FRAMES_REVISION, sha256=FRAMES_TSV_SHA256)

    def split_path(self, services: Services) -> Path:
        return services.split_path

    def normalize_ref(self, ref: str) -> str:
        return normalize_wiki_url(ref).key

    def corpus_source(self, services: Services, config: CorpusConfig, corpus_dir: Path) -> CorpusSource:
        from benchmarks.frames import HARNESS_VERSION
        from benchmarks.frames.corpus.builder import CorpusBuilder

        return CorpusBuilder(
            services.article_source, corpus_dir, snapshot=config.snapshot,
            workers=config.fetch_workers, harness_version=HARNESS_VERSION,
        )

    def judges(self, services: Services, grading: GradingConfig) -> list[AnswerJudge]:
        from benchmarks.frames.grading.judges import FramesJudge, StrictJudge

        judges: list[AnswerJudge] = [
            FramesJudge(services.llm, services.judge_model(grading.primary), "primary"),
        ]
        if grading.secondary is not None:
            judges.append(FramesJudge(services.llm, services.judge_model(grading.secondary), "secondary"))
        if grading.strict_grader:
            judges.append(StrictJudge(services.llm, services.judge_model(grading.primary), "primary"))
        return judges


register_dataset(FramesDataset.name, FramesDataset)
