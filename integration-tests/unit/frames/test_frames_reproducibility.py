"""Guards on the things a published number depends on."""

from __future__ import annotations

import hashlib

import pytest

from benchmarks.frames.grading.prompts import (
    ANSWER_PROMPT_PINS,
    answer_prompt_texts,
    verify_prompt_pins,
)
from benchmarks.frames.models import QuestionScore
from benchmarks.frames.report.summary import _per_question


class TestPromptPins:
    def test_every_answering_prompt_is_pinned(self) -> None:
        """Editing an answering prompt changes every number in the board; a
        version string alone does not notice."""
        assert set(answer_prompt_texts()) <= set(ANSWER_PROMPT_PINS)

    def test_pins_match_the_live_text(self) -> None:
        for version, text in answer_prompt_texts().items():
            assert hashlib.sha256(text.encode()).hexdigest() == ANSWER_PROMPT_PINS[version], version

    def test_verify_runs_clean(self) -> None:
        verify_prompt_pins()

    def test_an_edited_prompt_is_caught(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(ANSWER_PROMPT_PINS, "frames-answer-v2", "0" * 64)
        with pytest.raises(Exception, match="frames-answer-v2"):
            verify_prompt_pins()


def _score(qid: str, value: bool) -> QuestionScore:
    return QuestionScore(
        system="s", question_id=qid, repeat=0, split="dev", gold_count=1, correct=value,
    )


class TestBootstrapOrdering:
    def test_per_question_order_is_independent_of_input_order(self) -> None:
        """The bootstrap resamples by index, so insertion order would otherwise
        move the published confidence interval for identical scores."""
        forward = [_score("1", True), _score("2", False), _score("10", True)]
        assert list(_per_question(forward, lambda s: s.correct)) == list(
            _per_question(list(reversed(forward)), lambda s: s.correct),
        )

    def test_keys_are_sorted(self) -> None:
        scores = [_score("10", True), _score("2", False), _score("1", True)]
        assert list(_per_question(scores, lambda s: s.correct)) == ["1", "10", "2"]


class TestConfigHashScope:
    """The hash must cover what a run measured, and nothing about how hard it
    was driven — otherwise raising a budget to let a stalled run finish throws
    away everything already paid for."""

    def _config(self):
        from pathlib import Path

        from benchmarks.frames.config import load_config

        return load_config(Path("benchmarks/frames/configs/blog-dev-fixed2.yaml"))

    @pytest.mark.parametrize("field", ["max_cost_usd", "max_error_rate", "min_items_for_breaker"])
    def test_budget_and_breaker_do_not_change_the_hash(self, field: str) -> None:
        cfg = self._config()
        bumped = cfg.model_copy(update={"limits": cfg.limits.model_copy(update={field: 999})})
        assert cfg.config_hash() == bumped.config_hash()

    def test_concurrency_does_not_change_the_hash(self) -> None:
        cfg = self._config()
        systems = tuple(s.model_copy(update={"concurrency": s.concurrency + 1}) for s in cfg.systems)
        assert cfg.config_hash() == cfg.model_copy(update={"systems": systems}).config_hash()

    def test_bootstrap_samples_do_not_change_the_hash(self) -> None:
        cfg = self._config()
        stats = cfg.stats.model_copy(update={"bootstrap_samples": cfg.stats.bootstrap_samples + 1})
        assert cfg.config_hash() == cfg.model_copy(update={"stats": stats}).config_hash()

    def test_the_dataset_does_change_the_hash(self) -> None:
        cfg = self._config()
        other = cfg.model_copy(update={"dataset": cfg.dataset.model_copy(update={"name": "hotpotqa"})})
        assert cfg.config_hash() != other.config_hash()

    def test_the_answering_model_does_change_the_hash(self) -> None:
        cfg = self._config()
        other = cfg.model_copy(update={"answerer": cfg.answerer.model_copy(update={"model": "other-model"})})
        assert cfg.config_hash() != other.config_hash()
