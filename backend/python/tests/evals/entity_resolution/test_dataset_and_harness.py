"""Structural checks on the resolution evaluation: the dataset is well
formed, and the harness runs the real pipeline end to end without a model
or an embedding download (hash embeddings, no merge model)."""
from __future__ import annotations

import json
import re

import pytest

from tests.evals.entity_resolution.run import DATASET, HashEmbeddings, evaluate


def _dataset() -> dict:
    return json.loads(DATASET.read_text())


def test_every_mention_has_one_gold_entity_and_no_record_repeats_one() -> None:
    data = _dataset()
    assert data["records"]
    for record in data["records"]:
        assert set(record["gold"]) == set(record["topics"]), record["id"]
        assert len(set(record["gold"].values())) == len(record["topics"]), record["id"]


def test_every_entity_appears_in_several_spellings() -> None:
    spellings: dict[str, set[str]] = {}
    for record in _dataset()["records"]:
        for raw, entity in record["gold"].items():
            spellings.setdefault(entity, set()).add(raw)
    assert all(len(names) >= 2 for names in spellings.values()), spellings


async def test_without_a_model_nothing_is_merged_wrongly() -> None:
    """Exact and alias matches only: every merge is right (precision 1), and
    spellings the model would have joined stay apart (recall below 1)."""
    report = await evaluate(_dataset(), embeddings=HashEmbeddings(), llm=None)
    assert report["mentions"] == sum(len(r["topics"]) for r in _dataset()["records"])
    assert report["dropped"] == 0
    assert report["pairwise"]["precision"] == 1.0
    assert report["bcubed"]["precision"] == 1.0
    assert report["bcubed"]["recall"] < 1.0


async def test_the_oracle_only_merges_right_and_beats_no_model() -> None:
    """The gold oracle answers every merge question correctly, so whatever it
    misses is the candidate search's doing (one offered winner per name)."""
    from tests.evals.entity_resolution.run import GoldOracle

    baseline = await evaluate(_dataset(), embeddings=HashEmbeddings(), llm=None)
    report = await evaluate(_dataset(), embeddings=HashEmbeddings(), llm=None, oracle=GoldOracle())
    assert report["bcubed"]["precision"] == 1.0
    assert report["bcubed"]["recall"] > baseline["bcubed"]["recall"]


async def test_a_failing_oracle_fails_the_run_instead_of_scoring_as_no_model(monkeypatch) -> None:
    from tests.evals.entity_resolution import run

    monkeypatch.setattr(run, "_PROMPT_ITEMS", re.compile(r"(?!)"))
    with pytest.raises(RuntimeError, match="oracle"):
        await evaluate(_dataset(), embeddings=HashEmbeddings(), llm=None, oracle=run.GoldOracle())
