"""Timing of the emit phase on x86 CI: the worker wall added per table page at 1 and 2 worker threads (the environment a lane
worker gets), and the model's seconds per table. The figures are printed and written to the job summary. With
``TABLE_EMIT_WALL_BAR_S`` set, the added wall per table page at 2 threads must stay under it."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from app.modules.parsers.parse_pool_lanes import worker_thread_env


def run_timing(weights: str, threads: int) -> dict:
    env = {**os.environ, **worker_thread_env(threads, {"PDF_TABLE_MODEL_THREADS": ""})}
    out = subprocess.run(
        [sys.executable, "-m", "tests.table_model.emit_timing_run", weights, "3"], capture_output=True, text=True, env=env, timeout=900, check=False
    )
    lines = [x for x in out.stdout.splitlines() if x.startswith("TIMING ")]
    assert lines, out.stdout[-2000:] + out.stderr[-2000:]
    return json.loads(lines[-1][len("TIMING "):])


@pytest.fixture(scope="module")
def figures(weights) -> dict[int, dict]:
    return {n: run_timing(weights, n) for n in (1, 2)}


def test_the_figures_are_measured_and_reported(figures: dict[int, dict]) -> None:
    lines = ["| worker threads | table pages | added wall per table page (median / max, s) | cpu added (s) | model s per table (median / mean) |", "|---|---|---|---|---|"]
    for n, f in figures.items():
        assert f["table_pages"] >= 5 and f["tables_timed"] >= 8
        lines.append(f"| {n} | {f['table_pages']} | {f['added_per_page_median']:.3f} / {f['added_per_page_max']:.3f} | {f['cpu_added_per_page_median']:.3f} | {f['model_seconds_per_table_median']:.3f} / {f['model_seconds_per_table_mean']:.3f} |")
    text = "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write("### Hybrid emit timing (x86 runner)\n\n" + text + "\n")


def test_added_wall_per_table_page_stays_under_the_bar_at_two_threads(figures: dict[int, dict]) -> None:
    bar = os.environ.get("TABLE_EMIT_WALL_BAR_S")
    if not bar:
        pytest.skip("no bar set")  # a local run; the workflow sets it and the job fails, never skips, without it
    assert figures[2]["added_per_page_median"] <= float(bar), figures[2]
