"""Seeded, stratified dev / held-out split and question selection.

All iteration happens on the dev slice; held-out is scored only at milestones.
Strata are the sorted reasoning-type combination, allocated by the largest
remainder method so every stratum is represented proportionally.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from benchmarks.harness.config import DatasetConfig
from benchmarks.harness.errors import DatasetIntegrityError
from benchmarks.harness.models import Question, Split
from benchmarks.harness.store import atomic_write_text

DEV_SIZE = 200
SPLIT_VERSION = 1


class SplitFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int
    seed: int
    dataset_revision: str
    dev_size: int
    assignments: dict[str, Split]


def stratum(question: Question) -> str:
    return " | ".join(sorted(question.labels)) or "none"


def _largest_remainder(sizes: dict[str, int], target: int) -> dict[str, int]:
    total = sum(sizes.values())
    raw = {key: target * size / total for key, size in sizes.items()}
    quotas = {key: int(value) for key, value in raw.items()}
    shortfall = target - sum(quotas.values())
    by_remainder = sorted(raw, key=lambda key: (-(raw[key] - quotas[key]), key))
    for key in by_remainder[:shortfall]:
        quotas[key] += 1
    return quotas


def stratified_sample(questions: Sequence[Question], size: int, seed: int) -> list[int]:
    """`size` question ids, proportional across strata, deterministic in `seed`."""
    size = min(size, len(questions))
    groups: dict[str, list[int]] = defaultdict(list)
    for question in questions:
        groups[stratum(question)].append(question.id)
    quotas = _largest_remainder({key: len(ids) for key, ids in groups.items()}, size)
    rng = random.Random(seed)
    chosen: list[int] = []
    for key in sorted(groups):
        ids = sorted(groups[key])
        rng.shuffle(ids)
        chosen.extend(ids[:quotas[key]])
    return sorted(chosen)


def stratified_split(questions: Sequence[Question], *, dev_size: int = DEV_SIZE, seed: int) -> dict[str, Split]:
    dev = set(stratified_sample(questions, dev_size, seed))
    return {q.id: ("dev" if q.id in dev else "heldout") for q in sorted(questions, key=lambda q: q.id)}


def write_split(path: Path, assignments: dict[str, Split], *, seed: int, dataset_revision: str) -> SplitFile:
    split = SplitFile(
        version=SPLIT_VERSION, seed=seed, dataset_revision=dataset_revision,
        dev_size=sum(1 for s in assignments.values() if s == "dev"), assignments=assignments,
    )
    atomic_write_text(path, split.model_dump_json(indent=1) + "\n")
    return split


def load_split(path: Path) -> SplitFile:
    if not path.exists():
        raise DatasetIntegrityError(f"split file {path} is missing; run `dataset --write-split`")
    return SplitFile.model_validate(json.loads(path.read_text()))


def split_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_split(questions: Iterable[Question], split: SplitFile) -> list[Question]:
    questions = list(questions)
    if {q.id for q in questions} != set(split.assignments):
        raise DatasetIntegrityError("split file does not cover exactly the dataset's question ids")
    return [q.model_copy(update={"split": split.assignments[q.id]}) for q in questions]


def select_questions(questions: Sequence[Question], config: DatasetConfig, seed: int) -> list[Question]:
    pool = [q for q in questions if config.split == "all" or q.split == config.split]
    if config.question_ids is not None:
        wanted = set(config.question_ids)
        pool = [q for q in pool if q.id in wanted]
    if config.limit is not None and config.limit < len(pool):
        keep = set(stratified_sample(pool, config.limit, seed))
        pool = [q for q in pool if q.id in keep]
    return sorted(pool, key=lambda q: q.id)
