"""The dataset seam: what a second benchmark has to provide, and what the
shared layers are no longer allowed to know."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import benchmarks.frames.dataset.plugin  # noqa: F401  (registers "frames")
from benchmarks.frames import stages
from benchmarks.frames.datasets import (
    DatasetPlugin,
    dataset_plugin,
    register_dataset,
    registered_datasets,
)
from benchmarks.frames.errors import ConfigError
from benchmarks.frames.models import AskItem, Question


class TestRegistry:
    def test_frames_is_registered(self) -> None:
        assert "frames" in registered_datasets()
        assert dataset_plugin("frames").name == "frames"

    def test_unknown_dataset_names_the_known_ones(self) -> None:
        with pytest.raises(ConfigError, match="frames"):
            dataset_plugin("hotpotqa")

    def test_a_second_dataset_needs_no_shared_code(self) -> None:
        """The whole point of the seam: registering a plugin is the only step."""

        class Fake:
            name = "fake"
            primary_rubric = "em"

            def load(self, services: object) -> list[Question]:
                return [Question(id="a1", prompt="q", answer="a")]

            def revision(self, services: object) -> object: ...
            def split_path(self, services: object) -> Path: ...
            def normalize_ref(self, ref: str) -> str:
                return ref
            def corpus_source(self, services: object, config: object, corpus_dir: Path) -> object: ...
            def judges(self, services: object, grading: object) -> list:
                return []

        register_dataset("fake", Fake)
        try:
            assert dataset_plugin("fake").load(None)[0].id == "a1"
        finally:
            from benchmarks.frames import datasets as registry
            registry._REGISTRY.pop("fake", None)

    def test_plugin_satisfies_the_protocol(self) -> None:
        plugin = dataset_plugin("frames")
        for name in ("load", "revision", "split_path", "normalize_ref", "corpus_source", "judges"):
            assert callable(getattr(plugin, name)), name
        assert isinstance(plugin.primary_rubric, str)


class TestSharedLayersAreDatasetNeutral:
    @pytest.mark.parametrize(
        "forbidden",
        ["normalize_wiki_url", "CorpusBuilder", "FramesJudge", "StrictJudge", "FRAMES_REVISION", "WikiRef"],
    )
    def test_stages_no_longer_import_frames_internals(self, forbidden: str) -> None:
        """`stages.py` orchestrates; it must not know what a Wikipedia URL is,
        how to fetch one, or which rubric grades the answers."""
        assert forbidden not in inspect.getsource(stages)


class TestGoldRefsAreWithheld:
    def test_only_an_adapter_that_declares_it_receives_gold(self) -> None:
        question = Question(id="1", prompt="q", answer="a", gold_refs=("http://x",))
        assert AskItem.from_question(question).gold_refs == ()
        assert AskItem.from_question(question, with_gold=True).gold_refs == ("http://x",)

    def test_oracle_is_the_one_adapter_granted_gold(self) -> None:
        from benchmarks.frames.systems.baselines.oracle import OracleAnswerer

        assert OracleAnswerer.capabilities.needs_gold_refs is True
