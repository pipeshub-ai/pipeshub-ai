"""Dataset: URL normalisation for every quirk in test.tsv, loading, split."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from frames_testkit import write_frames_tsv

from benchmarks.harness.config import DatasetConfig
from benchmarks.datasets.frames.loader import load_questions
from benchmarks.harness.dataset.split import (
    apply_split,
    load_split,
    select_questions,
    stratified_sample,
    stratified_split,
    stratum,
)
from benchmarks.datasets.frames.urls import normalize_wiki_url, parse_wiki_links_cell, split_link_field
from benchmarks.harness.errors import DatasetIntegrityError
from benchmarks.harness.models import Question
from benchmarks.datasets.frames.paths import SPLIT_FILE


class TestNormalizeWikiUrl:
    @pytest.mark.parametrize(
        ("raw", "key"),
        [
            ("https://en.wikipedia.org/wiki/James_Buchanan", "en.wikipedia.org/James_Buchanan"),
            ("https://en.m.wikipedia.org/wiki/James_Buchanan#Early_life", "en.wikipedia.org/James_Buchanan"),
            ("http://en.wikipedia.org/wiki/james_Buchanan", "en.wikipedia.org/James_Buchanan"),
            ("https://en.wikipedia.org/wiki/Harriet_Lane#:~:text=Harriet%20Rebecca", "en.wikipedia.org/Harriet_Lane"),
            ("https://en.wikipedia.org/wiki/Ender%2527s_Game", "en.wikipedia.org/Ender's_Game"),
            ("https://en.wikipedia.org/w/index.php?title=The_Oval&redirect=no", "en.wikipedia.org/The_Oval"),
            ("en.wikipedia.org/wiki/Grazia_Deledda", "en.wikipedia.org/Grazia_Deledda"),
            ("https://simple.wikipedia.org/wiki/Video_assistant_referee", "simple.wikipedia.org/Video_assistant_referee"),
            ("https://en.wikipedia.org/wiki/AC/DC", "en.wikipedia.org/AC/DC"),
        ],
    )
    def test_article_keys(self, raw: str, key: str) -> None:
        ref = normalize_wiki_url(raw)
        assert ref.kind == "article"
        assert ref.key == key

    def test_fragment_is_kept_as_metadata_only(self) -> None:
        ref = normalize_wiki_url("https://en.wikipedia.org/wiki/James_Buchanan#Presidency")
        assert ref.fragment == "Presidency"
        assert ref.canonical_url == "https://en.wikipedia.org/wiki/James_Buchanan"

    def test_special_search_becomes_a_search_ref(self) -> None:
        ref = normalize_wiki_url(
            "https://en.wikipedia.org/w/index.php?search=Polytrichum+piliferum&title=Special:Search&profile=advanced",
        )
        assert (ref.kind, ref.title) == ("search", "Polytrichum piliferum")

    def test_short_link_needs_resolution(self) -> None:
        assert normalize_wiki_url("https://w.wiki/ASFv").kind == "short_link"


class TestLinkCells:
    def test_comma_joined_urls_are_split(self) -> None:
        assert split_link_field("https://en.wikipedia.org/wiki/A, https://en.wikipedia.org/wiki/B,C") == [
            "https://en.wikipedia.org/wiki/A", "https://en.wikipedia.org/wiki/B,C",
        ]

    def test_stringified_list_with_joined_element(self) -> None:
        cell = "['https://en.wikipedia.org/wiki/A,https://en.wikipedia.org/wiki/B', 'https://en.wikipedia.org/wiki/C']"
        assert [u.rsplit("/", 1)[1] for u in parse_wiki_links_cell(cell)] == ["A", "B", "C"]


def _tsv(tmp_path: Path) -> Path:
    return write_frames_tsv(tmp_path / "test.tsv", [
        {"prompt": "Q0?", "answer": "A0", "links": ["https://en.wikipedia.org/wiki/X", "https://en.m.wikipedia.org/wiki/X#s"],
         "types": "Numerical reasoning | Tabular reasoning"},
        {"prompt": "Q1?", "answer": "A1", "links": ["https://en.wikipedia.org/wiki/Y", "https://en.wikipedia.org/wiki/Z"]},
    ])


class TestLoader:
    def test_loads_questions_and_dedupes_gold_links(self, tmp_path: Path) -> None:
        questions = load_questions(_tsv(tmp_path), expected_count=2)
        assert [q.id for q in questions] == ["0", "1"]
        assert questions[0].labels == ("Numerical reasoning", "Tabular reasoning")
        assert questions[0].gold_refs == ("https://en.wikipedia.org/wiki/X",)
        assert len(questions[1].gold_refs) == 2

    def test_wrong_count_is_an_integrity_error(self, tmp_path: Path) -> None:
        with pytest.raises(DatasetIntegrityError):
            load_questions(_tsv(tmp_path), expected_count=824)


def _questions(n: int) -> list[Question]:
    labels = ["Multiple constraints", "Numerical reasoning", "Temporal reasoning", "Tabular reasoning"]
    return [
        Question(id=str(i), prompt=f"q{i}", answer="a", labels=[labels[i % 4], labels[(i // 4) % 4]], gold_refs=[])
        for i in range(n)
    ]


class TestSplit:
    def test_deterministic_and_sized(self) -> None:
        questions = _questions(120)
        first = stratified_split(questions, dev_size=30, seed=7)
        assert first == stratified_split(questions, dev_size=30, seed=7)
        assert Counter(first.values()) == {"dev": 30, "heldout": 90}
        assert first != stratified_split(questions, dev_size=30, seed=8)

    def test_every_stratum_gets_its_floor_or_ceiling_share(self) -> None:
        questions = _questions(160)
        sizes = Counter(stratum(q) for q in questions)
        dev = set(stratified_sample(questions, 40, seed=1))
        picked = Counter(stratum(q) for q in questions if q.id in dev)
        assert sum(picked.values()) == 40
        for name, size in sizes.items():
            share = 40 * size / 160
            assert int(share) <= picked[name] <= int(share) + 1, name

    def test_limit_selects_a_stratified_subset(self) -> None:
        questions = [q.model_copy(update={"split": "dev"}) for q in _questions(80)]
        picked = select_questions(questions, DatasetConfig(split="dev", limit=8), seed=3)
        assert len(picked) == 8
        assert len({q.labels[0] for q in picked}) == 4

    def test_committed_split_covers_the_dataset(self) -> None:
        split = load_split(SPLIT_FILE)
        assert set(split.assignments) == {str(i) for i in range(824)}
        assert Counter(split.assignments.values()) == {"dev": 200, "heldout": 624}
        assert split.dataset_revision.startswith("58d9fb63")

    def test_apply_split_rejects_a_mismatched_dataset(self) -> None:
        with pytest.raises(DatasetIntegrityError):
            apply_split(_questions(3), load_split(SPLIT_FILE))
