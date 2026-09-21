"""Command line: `python -m benchmarks.frames <command> --config configs/<x>.yaml`.

Exit codes: 0 valid run, 1 error, 2 run completed but INVALID (a disallowed
tool was called — see guard.py).
"""

from __future__ import annotations

import argparse
import faulthandler
import logging
import signal
from collections.abc import Callable, Sequence
from pathlib import Path

from benchmarks.frames.config import FRAMES_SNAPSHOT, RunConfig, load_config
from benchmarks.frames.credentials import Credentials
from benchmarks.frames.dataset.loader import FRAMES_REVISION, load_questions
from benchmarks.frames.dataset.split import stratified_split, write_split
from benchmarks.frames.errors import FramesError
from benchmarks.frames.paths import INTEGRATION_TESTS_DIR, REPORTS_DIR, SPLIT_FILE
from benchmarks.frames.pipeline import Stage, run_pipeline
from benchmarks.frames.services import RunContext, Services
from benchmarks.frames.stages import (
    AskStage,
    CorpusStage,
    DatasetStage,
    GradeStage,
    LoadPreparedStage,
    PrepareStage,
    ReportStage,
    ScoreStage,
    SearchStage,
)
# Importing a dataset module registers it; add new datasets here.
import benchmarks.frames.dataset.plugin  # noqa: F401
from benchmarks.frames.datasets import dataset_plugin
from benchmarks.frames.store import RunStore

logger = logging.getLogger("benchmarks.frames")

EXIT_OK, EXIT_ERROR, EXIT_INVALID = 0, 1, 2

PIPELINES: dict[str, Callable[[], list[Stage]]] = {
    "corpus": lambda: [DatasetStage(), CorpusStage()],
    "run": lambda: [
        DatasetStage(), CorpusStage(reuse_existing=True), PrepareStage(), AskStage(), SearchStage(),
        GradeStage(), ScoreStage(), ReportStage(),
    ],
    "grade": lambda: [
        DatasetStage(), CorpusStage(load_only=True), LoadPreparedStage(), GradeStage(), ScoreStage(), ReportStage(),
    ],
    "score": lambda: [DatasetStage(), CorpusStage(load_only=True), LoadPreparedStage(), ScoreStage(), ReportStage()],
    "report": lambda: [DatasetStage(), CorpusStage(load_only=True), LoadPreparedStage(), ReportStage()],
}
_NEEDS_RESUME = {"grade", "score", "report"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.frames", description=__doc__)
    parser.add_argument("--log-level", default="INFO")
    commands = parser.add_subparsers(dest="command", required=True)
    split = commands.add_parser("write-split", help="regenerate data/split_v1.json")
    split.add_argument("--dataset-path", type=Path)
    split.add_argument("--seed", type=int, default=RunConfig.model_fields["seed"].default)
    logs = commands.add_parser("backend-logs", help="group backend warnings/errors logged during a run")
    logs.add_argument("--resume", metavar="RUN_ID", required=True)
    logs.add_argument("--container", default="pipeshub-ai-pipeshub-ai-1")
    for name in (*PIPELINES, "seed-models"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--dataset-path", type=Path, help="local test.tsv (sha256-verified) instead of the HF download")
        command.add_argument("--resume", metavar="RUN_ID")
        command.add_argument("--retry-errors", action="store_true", help="re-ask questions whose prediction failed")
    return parser


def _write_split(args: argparse.Namespace) -> int:
    services = Services(RunConfig.model_construct(), Credentials.from_env(), dataset_path=args.dataset_path)
    questions = load_questions(services.dataset_tsv())
    split = write_split(SPLIT_FILE, stratified_split(questions, seed=args.seed), seed=args.seed, dataset_revision=FRAMES_REVISION)
    logger.info("wrote %s: %d dev / %d held-out", SPLIT_FILE, split.dev_size, len(questions) - split.dev_size)
    return EXIT_OK


def _backend_logs(args: argparse.Namespace) -> int:
    import json

    from benchmarks.frames.models import Prediction
    from benchmarks.frames.report.backend_logs import collect, render_issues

    run_dir = REPORTS_DIR / args.resume
    latest: dict[tuple[str, int, int], Prediction] = {}
    for line in (run_dir / "predictions.jsonl").read_text().splitlines():
        prediction = Prediction.model_validate_json(line)
        latest[prediction.key] = prediction
    issues, per_question = collect(list(latest.values()), args.container)
    (run_dir / "backend_issues.md").write_text(render_issues(issues))
    (run_dir / "backend_issues_by_question.json").write_text(json.dumps(per_question, indent=1))
    logger.info("%d backend issue templates -> %s", len(issues), run_dir / "backend_issues.md")
    return EXIT_OK


def _seed_models(config: RunConfig, services: Services) -> int:
    from benchmarks.frames.systems.pipeshub.seed import ensure_models

    selectors = [config.answerer, config.grading.primary]
    if config.grading.secondary is not None:
        selectors.append(config.grading.secondary)
    for model in ensure_models(services.session, services.resolver, selectors, services.credentials, default_selector=config.answerer):
        logger.info("ready: %s (%s)", model.label, model.model_key)
    return EXIT_OK


def _run_pipeline(args: argparse.Namespace, config: RunConfig, services: Services) -> int:
    if args.command in _NEEDS_RESUME and not args.resume:
        raise FramesError(f"`{args.command}` re-processes an existing run; pass --resume RUN_ID")
    store = RunStore.resume(REPORTS_DIR, args.resume, config) if args.resume else RunStore.create(REPORTS_DIR, config)
    logger.info("run %s (%s)", store.run_id, store.run_dir)
    ctx = RunContext(
        config=config, store=store, services=services,
        dataset=dataset_plugin(config.dataset.name), retry_errors=args.retry_errors,
    )
    run_pipeline(PIPELINES[args.command](), ctx)
    if ctx.summary is not None and not ctx.summary.valid:
        logger.error("run is INVALID: %d policy violations", len(ctx.summary.violations))
        return EXIT_INVALID
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # LiteLLM logs one INFO line per call; at ~10k embedding calls that buries
    # the run's own progress.
    for noisy in ("LiteLLM", "litellm", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # `kill -USR1 <pid>` dumps every thread's stack: a run that stops making
    # progress can be diagnosed without attaching a debugger.
    faulthandler.register(signal.SIGUSR1, all_threads=True)
    from helper.env_loading import load_test_env

    load_test_env(INTEGRATION_TESTS_DIR)
    try:
        if args.command == "write-split":
            return _write_split(args)
        if args.command == "backend-logs":
            return _backend_logs(args)
        config = load_config(args.config)
        services = Services(config, Credentials.from_env(), dataset_path=args.dataset_path)
        if args.command == "seed-models":
            return _seed_models(config, services)
        return _run_pipeline(args, config, services)
    except FramesError as exc:
        logger.error("%s: %s", type(exc).__name__, exc)
        return EXIT_ERROR


__all__ = ["FRAMES_SNAPSHOT", "main"]
