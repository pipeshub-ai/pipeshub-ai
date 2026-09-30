"""`combine`: one board from runs that split a full board between them."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.harness.combine import combine
from benchmarks.harness.config import StatsConfig
from benchmarks.harness.models import QuestionScore


def _score(system: str, qid: str, split: str, *, correct: bool = True, refused: bool = False) -> QuestionScore:
    return QuestionScore(
        system=system, question_id=qid, repeat=0, split=split, labels=(), gold_count=2,
        correct=correct and not refused, error_kind="llm" if refused else None, provider_refusal=refused,
    )


def _run(root: Path, name: str, scores: list[QuestionScore]) -> Path:
    run = root / name
    run.mkdir(parents=True)
    (run / "scores.jsonl").write_text("".join(s.model_dump_json() + "\n" for s in scores))
    (run / "report.md").write_text(f"report of {name}")
    return run


def test_pools_runs_into_three_views(tmp_path: Path) -> None:
    rag = _run(tmp_path, "run-rag", [
        _score("oracle", "1", "dev"), _score("oracle", "2", "heldout"), _score("oracle", "3", "heldout"),
        _score("naive_rag", "1", "dev"), _score("naive_rag", "2", "heldout", refused=True),
        _score("naive_rag", "3", "heldout", correct=False),
    ])
    agent = _run(tmp_path, "run-agent", [
        _score("oracle", "1", "dev", correct=False), _score("oracle", "2", "heldout"), _score("oracle", "3", "heldout"),
        _score("pipeshub", "1", "dev"), _score("pipeshub", "2", "heldout"), _score("pipeshub", "3", "heldout"),
    ])
    out = tmp_path / "results"

    views = combine([rag, agent], out, stats=StatsConfig(bootstrap_samples=200))

    board = {s.system: s for s in views["all"].systems}
    assert set(board) == {"oracle", "naive_rag", "pipeshub"}
    assert board["oracle"].accuracy.value == 1.0  # the first run's oracle, not the second's
    assert board["naive_rag"].accuracy.value == 1 / 3
    no_refusals = {s.system: s for s in views["no-refusals"].systems}
    assert no_refusals["naive_rag"].questions == 2 and no_refusals["pipeshub"].questions == 2
    heldout = {s.system: s for s in views["heldout"].systems}
    assert heldout["pipeshub"].questions == 2
    sources = json.loads((out / "sources.json").read_text())
    assert sources["systems"]["oracle"] == "run-rag" and sources["refused_anywhere"] == ["2"]
    assert (out / "run-agent" / "report.md").read_text() == "report of run-agent"
    text = (out / "board.md").read_text()
    assert "questions no system was refused on (1 excluded)" in text and "held-out split" in text
