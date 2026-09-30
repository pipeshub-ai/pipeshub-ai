"""One board from several runs on the same questions and index.

A run asks its systems one after another, so a full board is split across runs
that share a stack. `combine` pools their per-question scores and summarizes
them with the same code a run's own report uses, in three views:

- every question;
- only questions no system was refused on by the provider's content filter,
  since which system is refused depends on the text it happened to retrieve;
- the held-out split alone, which no tuning could have seen.

It also copies each run's small artefacts next to the board, so the folder is
a self-contained record of what was measured.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Sequence
from pathlib import Path

from benchmarks.harness.config import StatsConfig
from benchmarks.harness.models import QuestionScore
from benchmarks.harness.report.markdown import render_report
from benchmarks.harness.report.summary import RunSummary, summarize

logger = logging.getLogger(__name__)

_RUN_ARTEFACTS = ("summary.json", "report.md", "config.resolved.yaml", "run_meta.json", "failures.csv")


def load_scores(run_dirs: Sequence[Path]) -> tuple[list[QuestionScore], dict[str, str]]:
    """Scores from every run, keeping each system from the first run that has
    it (a control such as the oracle may ride along in several runs)."""
    scores: list[QuestionScore] = []
    source: dict[str, str] = {}
    for run_dir in run_dirs:
        path = run_dir / "scores.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"{path} is missing; score the run first")
        run_scores = [QuestionScore.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()]
        for system in sorted({s.system for s in run_scores}):
            if system in source:
                logger.info("%s: keeping it from %s, ignoring %s", system, source[system], run_dir.name)
                continue
            source[system] = run_dir.name
            scores.extend(s for s in run_scores if s.system == system)
    return scores, source


def _qid_key(qid: str) -> tuple[int, str]:
    return (len(qid), qid)


def refused_anywhere(scores: Sequence[QuestionScore]) -> set[str]:
    return {s.question_id for s in scores if s.provider_refusal}


def _view(name: str, scores: Sequence[QuestionScore], stats: StatsConfig, seed: int) -> RunSummary:
    return summarize(name, scores, [], lambda _q: [], stats, seed)


def combine(run_dirs: Sequence[Path], out: Path, *, stats: StatsConfig | None = None, seed: int = 0) -> dict[str, RunSummary]:
    stats = stats or StatsConfig()
    scores, source = load_scores(run_dirs)
    refused = refused_anywhere(scores)
    views = {
        "all": _view("all questions", scores, stats, seed),
        "no-refusals": _view(
            f"questions no system was refused on ({len(refused)} excluded)",
            [s for s in scores if s.question_id not in refused], stats, seed,
        ),
        "heldout": _view("held-out split", [s for s in scores if s.split == "heldout"], stats, seed),
    }
    out.mkdir(parents=True, exist_ok=True)
    board = [
        "# Combined FRAMES board",
        "",
        "Pooled from: " + ", ".join(f"`{d.name}`" for d in run_dirs) + ".",
        "",
        "| System | From run |",
        "|---|---|",
        *[f"| {system} | `{run}` |" for system, run in sorted(source.items())],
        "",
        f"Questions refused by the provider for at least one system: {', '.join(sorted(refused, key=_qid_key)) or 'none'}.",
        "",
    ]
    for key, summary in views.items():
        board += [f"# View: {summary.run_id}", "", render_report(summary), ""]
        (out / f"board-{key}.json").write_text(summary.model_dump_json(indent=1))
    (out / "board.md").write_text("\n".join(board))
    (out / "sources.json").write_text(json.dumps({"runs": [d.name for d in run_dirs], "systems": source, "refused_anywhere": sorted(refused, key=_qid_key)}, indent=1))
    for run_dir in run_dirs:
        target = out / run_dir.name
        target.mkdir(exist_ok=True)
        for name in _RUN_ARTEFACTS:
            if (run_dir / name).exists():
                shutil.copy2(run_dir / name, target / name)
    logger.info("combined %d systems from %d runs into %s", len(source), len(run_dirs), out)
    return views
