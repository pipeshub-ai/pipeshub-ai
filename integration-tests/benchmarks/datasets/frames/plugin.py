"""FRAMES as a `DatasetPlugin`.

Everything Wikipedia-shaped about the benchmark lives behind this file: the
HuggingFace TSV and its pinned revision, `wiki_links` as gold refs, MediaWiki
article fetching, and the paper's auto-rater rubric.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from benchmarks.harness.datasets import CorpusSource, DatasetRevision, register_dataset
from benchmarks.datasets.frames.loader import (
    FRAMES_REVISION,
    FRAMES_TSV_SHA256,
    download_frames_tsv,
    load_questions,
)
from benchmarks.datasets.frames.urls import normalize_wiki_url
from benchmarks.datasets.frames.paths import SPLIT_FILE
from benchmarks.harness.models import Question

if TYPE_CHECKING:
    from benchmarks.harness.config import CorpusConfig, GradingConfig
    from benchmarks.harness.grading.judges import AnswerJudge
    from benchmarks.harness.services import Services


class FramesDataset:
    name = "frames"
    primary_rubric = "frames"

    def _tsv(self, services: Services) -> Path:
        if services.dataset_path_override is not None:
            return services.dataset_path_override
        return download_frames_tsv(services.cache_dir / "hf", token=services.credentials.hf_token)

    def load(self, services: Services) -> list[Question]:
        return load_questions(self._tsv(services), expected_count=services.expected_question_count)

    def revision(self, _services: Services) -> DatasetRevision:
        return DatasetRevision(revision=FRAMES_REVISION, sha256=FRAMES_TSV_SHA256)

    def split_path(self, services: Services) -> Path:
        return services.split_path_override or SPLIT_FILE

    def normalize_ref(self, ref: str) -> str:
        return normalize_wiki_url(ref).key

    def corpus_source(self, services: Services, config: CorpusConfig, corpus_dir: Path) -> CorpusSource:
        from benchmarks.harness import HARNESS_VERSION
        from benchmarks.datasets.frames.builder import CorpusBuilder

        from benchmarks.datasets.frames.mediawiki import MediaWikiClient

        factory = services.article_source_override or (lambda host: MediaWikiClient(host))
        return CorpusBuilder(
            factory, corpus_dir, snapshot=config.snapshot,
            workers=config.fetch_workers, harness_version=HARNESS_VERSION,
        )

    def judges(self, services: Services, grading: GradingConfig) -> list[AnswerJudge]:
        from benchmarks.harness.grading.judges import FramesJudge, StrictJudge

        judges: list[AnswerJudge] = [
            FramesJudge(services.llm, services.judge_model(grading.primary), "primary"),
        ]
        if grading.secondary is not None:
            judges.append(FramesJudge(services.llm, services.judge_model(grading.secondary), "secondary"))
        if grading.strict_grader:
            judges.append(StrictJudge(services.llm, services.judge_model(grading.primary), "primary"))
        return judges


register_dataset(FramesDataset.name, FramesDataset)
