"""``harness-diff`` and ``scrub`` for recordings on disk.

python -m harness_core.testing.replay diff expected.json actual.json [--context]
python -m harness_core.testing.replay scrub in.json out.json [--canary TEXT ...]
python -m harness_core.testing.replay capture export.json case.json --case ID [--run RUN] [--incident ID] [--canary TEXT ...]
python -m harness_core.testing.replay check case.json [case.json ...]

``capture`` turns a run's exported journal into a redacted golden case (EV-OPS-01; ``golden.py`` says what is kept);
it writes nothing unless the case is proven clean and replays as the original did (exit 1 with the reasons).
``check`` re-proves committed cases.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harness_core.testing.replay.diff import diff_recordings
from harness_core.testing.replay.recording import Recording
from harness_core.testing.replay.scrub import SecretLeftBehind, scrub_recording
from harness_core.testing.replay.trace_redactor import (
    GoldenCase,
    RedactionNotProven,
    capture,
    load_export,
    verify,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness-diff")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("diff", help="report the first step where two recordings differ")
    d.add_argument("expected", type=Path)
    d.add_argument("actual", type=Path)
    d.add_argument(
        "--context",
        action="store_true",
        help="treat a request-context change as a divergence",
    )
    s = sub.add_parser("scrub", help="scrub secrets and personal data from a recording")
    s.add_argument("source", type=Path)
    s.add_argument("target", type=Path)
    s.add_argument("--canary", action="append", default=[])
    c = sub.add_parser("capture", help="capture a run's exported journal as a redacted golden case")
    c.add_argument("export", type=Path)
    c.add_argument("target", type=Path)
    c.add_argument("--case", required=True)
    c.add_argument("--run")
    c.add_argument("--incident")
    c.add_argument("--canary", action="append", default=[], help="text that must not survive (a customer's name)")
    k = sub.add_parser("check", help="re-prove committed golden cases")
    k.add_argument("cases", type=Path, nargs="+")
    args = parser.parse_args(argv)

    if args.command == "capture":
        return _capture(args)
    if args.command == "check":
        return _check(args.cases)
    if args.command == "diff":
        report = diff_recordings(
            Recording.load(args.expected),
            Recording.load(args.actual),
            compare_context=args.context,
        )
        sys.stdout.write(report.render() + "\n")
        return 0 if report.identical else 1
    try:
        scrubbed = scrub_recording(Recording.load(args.source), canaries=args.canary)
    except SecretLeftBehind as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    scrubbed.save(args.target)
    return 0


def _capture(args: argparse.Namespace) -> int:
    try:
        golden = capture(
            load_export(args.export.read_text(encoding="utf-8")),
            case=args.case,
            run_id=args.run,
            incident=args.incident,
            canaries=args.canary,
        )
    except RedactionNotProven as refused:
        sys.stderr.write(f"{refused}\n")
        return 1
    golden.save(args.target)
    sys.stdout.write(f"wrote {args.target} ({len(golden.events)} events, run {golden.manifest.run_id})\n")
    return 0


def _check(paths: list[Path]) -> int:
    failed = 0
    for path in paths:
        problems = verify(GoldenCase.load(path))
        if problems:
            failed += 1
            sys.stderr.write(f"{path}: " + "; ".join(problems[:10]) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
