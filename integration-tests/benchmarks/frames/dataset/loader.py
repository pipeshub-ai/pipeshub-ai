"""Loading the pinned FRAMES `test.tsv` from google/frames-benchmark."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from benchmarks.frames.dataset.urls import normalize_wiki_url, parse_wiki_links_cell, split_link_field
from benchmarks.frames.errors import DatasetIntegrityError
from benchmarks.frames.models import Question

FRAMES_REPO_ID = "google/frames-benchmark"
FRAMES_REVISION = "58d9fb6330f3ab1316d1eca12e5e8ef23dcc22ef"
FRAMES_FILENAME = "test.tsv"
FRAMES_TSV_SHA256 = "4255093c93b595b5b04c7c8dde290b48ec87d72ca0fb0b760d9dd02740d669ff"
EXPECTED_QUESTION_COUNT = 824

_LINK_COLUMNS = (*(f"wikipedia_link_{i}" for i in range(1, 11)), "wikipedia_link_11+")
_REQUIRED_COLUMNS = ("Prompt", "Answer", "reasoning_types", "wiki_links")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str = FRAMES_TSV_SHA256) -> None:
    actual = sha256_file(path)
    if actual != expected:
        raise DatasetIntegrityError(f"{path} sha256 {actual} != pinned {expected}")


def download_frames_tsv(cache_dir: Path, token: str | None = None) -> Path:
    from huggingface_hub import hf_hub_download

    path = Path(hf_hub_download(
        repo_id=FRAMES_REPO_ID,
        filename=FRAMES_FILENAME,
        repo_type="dataset",
        revision=FRAMES_REVISION,
        cache_dir=str(cache_dir),
        token=token,
    ))
    verify_sha256(path)
    return path


def _gold_urls(row: pd.Series) -> list[str]:
    candidates = parse_wiki_links_cell(row.get("wiki_links", ""))
    for column in _LINK_COLUMNS:
        candidates.extend(split_link_field(str(row.get(column, "") or "")))
    seen: set[str] = set()
    unique: list[str] = []
    for url in candidates:
        key = normalize_wiki_url(url).key
        if key not in seen:
            seen.add(key)
            unique.append(url)
    return unique


def _question(row: pd.Series, id_column: str) -> Question:
    return Question(
        id=str(row[id_column]),
        prompt=str(row["Prompt"]).strip(),
        answer=str(row["Answer"]).strip(),
        labels=[t.strip() for t in str(row["reasoning_types"]).split("|") if t.strip()],
        gold_refs=_gold_urls(row),
    )


def load_questions(tsv_path: Path, *, expected_count: int | None = EXPECTED_QUESTION_COUNT) -> list[Question]:
    frame = pd.read_csv(tsv_path, sep="\t", dtype=str, keep_default_na=False)
    missing = [c for c in _REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise DatasetIntegrityError(f"{tsv_path} is missing columns {missing}")
    # The index column has an empty header; pandas names it "Unnamed: 0".
    id_column = frame.columns[0]
    questions = [_question(row, id_column) for _, row in frame.iterrows()]
    if expected_count is not None and len(questions) != expected_count:
        raise DatasetIntegrityError(f"expected {expected_count} questions, got {len(questions)}")
    if len({q.id for q in questions}) != len(questions):
        raise DatasetIntegrityError("duplicate question ids")
    return sorted(questions, key=lambda q: q.id)
