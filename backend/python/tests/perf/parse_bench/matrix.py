"""Run the driver for several mixes and repeats, then summarize medians.

Two jobs:

* ``run``: for each mix and repeat, start the driver in a fresh process (cold
  caches, models loaded per run as in a restart) and keep its JSON. Used by
  ``run_tiers.sh`` inside each container, and by hand for the emulated baseline.
* ``summarize``: read the JSON files of one or more tier directories and print
  the median-per-mix table and the filled targets table. Needs only the stdlib.

    python -m tests.perf.parse_bench.matrix run --corpus C --out-dir O --label T-small --repeats 3
    python -m tests.perf.parse_bench.matrix summarize --out-dir O --tier T-small=4 --tier T-laptop=8
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from tests.perf.parse_bench import report

DEFAULT_MIXES = ("cpu-heavy", "io-heavy", "laptop-typical")
PRIMARY_MIX = "cpu-heavy"
BUSY = 3  # the driver's exit code for "host over its limit, not started"


def is_complete(result: Any) -> bool:  # noqa: ANN401
    """A finished run that left output unchanged; anything else is run again, not skipped."""
    return isinstance(result, dict) and result.get("status") == "ok" and not result.get("golden_diffs")


def is_complete_file(path: Path) -> bool:
    try:
        return is_complete(json.loads(path.read_text()))
    except (OSError, ValueError):
        return False


def driver_command(args: argparse.Namespace, mix: str, repeat: int, out: Path) -> list[str]:
    cmd = [
        sys.executable, "-m", "tests.perf.parse_bench.driver",
        "--corpus", str(args.corpus), "--mix", mix, "--label", args.label,
        "--passes", str(args.passes), "--out", str(out), "--max-host-pct", str(args.max_host_pct),
    ]
    if args.cpus:
        cmd += ["--cpus", str(args.cpus)]
    if args.mem_limit_gb:
        cmd += ["--mem-limit-gb", str(args.mem_limit_gb)]
    if args.golden_check and repeat == 1:
        cmd += ["--golden-check", str(args.golden_check)]
    return cmd + list(args.driver_args)


def run_all(args: argparse.Namespace) -> int:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    worst = 0
    for mix in args.mixes:
        for repeat in range(1, args.repeats + 1):
            out = args.out_dir / f"{args.label}-{mix}-r{repeat}.json"
            if is_complete_file(out) and not args.force:
                print(f"skip {out.name} (exists)")
                continue
            for attempt in range(args.busy_retries + 1):
                code = subprocess.run(driver_command(args, mix, repeat, out), check=False).returncode
                if code != BUSY or attempt == args.busy_retries:
                    break
                time.sleep(args.busy_wait)
            worst = max(worst, code)
            if code not in (0, 5):  # 5 is a golden diff: keep going, report at the end
                print(f"{out.name}: driver exited {code}", file=sys.stderr)
    return worst


def load_results(out_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(out_dir.glob("*.json")) if p.name != "summary.json"]


def medians(results: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """{(label, mix): the median-throughput run of the finished runs}."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        if result["status"] == "ok":
            groups[(result["config"]["label"], result["config"]["mix"])].append(result)
    return {key: report.median_run(runs) for key, runs in groups.items()}


def summarize(out_dirs: list[Path], limits_gb: dict[str, float]) -> str:
    results = [r for d in out_dirs for r in load_results(d)]
    chosen = medians(results)
    counts = defaultdict(int)
    for result in results:
        counts[(result["config"]["label"], result["config"]["mix"])] += 1
    rows = [report.run_row(run) for _, run in sorted(chosen.items())]
    arms = sorted({report.arm_label(r) for r in results})
    finished: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        if result["status"] == "ok":
            finished[(result["config"]["label"], result["config"]["mix"])].append(result)
    spread_rows = [report.cell_row(runs) for _, runs in sorted(finished.items())]
    text = [
        "Arm: " + "; ".join(arms),
        "",
        "## Median run per tier and mix",
        "",
        report.markdown_table(report.HEADER, rows),
        "",
        "## Spread over the runs of each cell: median (min-max)",
        "",
        "Loop-lag p99 is the third-worst of a few hundred samples on a short run, so read its range, not one value.",
        "",
        report.markdown_table(report.CELL_HEADER, spread_rows),
        "",
    ]
    text.append("Runs per cell: " + ", ".join(f"{k[0]}/{k[1]}={v}" for k, v in sorted(counts.items())))
    unfinished = [r["config"]["label"] + "/" + r["config"]["mix"] + ": " + r["status"]
                  for r in results if r["status"] != "ok"]
    unfinished += [f"{r['config']['label']}/{r['config']['mix']}: golden diff ({len(r['golden_diffs'])} files)"
                   for r in results if r["status"] == "ok" and r.get("golden_diffs")]
    if unfinished:
        text += ["", "Not finished: " + "; ".join(unfinished)]
    primary = {label: run for (label, mix), run in chosen.items() if mix == PRIMARY_MIX}
    if primary:
        limits = {label: gb * 1024**3 for label, gb in limits_gb.items()}
        text += ["", f"## Targets table ({PRIMARY_MIX} mix, median)", "", report.targets_table(primary, limits)]
    return "\n".join(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--corpus", type=Path, required=True)
    run.add_argument("--out-dir", type=Path, required=True)
    run.add_argument("--label", required=True)
    run.add_argument("--mixes", nargs="+", default=list(DEFAULT_MIXES))
    run.add_argument("--repeats", type=int, default=3)
    run.add_argument("--passes", type=int, default=1)
    run.add_argument("--cpus", type=int, default=0, help="emulation only; leave 0 inside a container")
    run.add_argument("--mem-limit-gb", type=float, default=0.0, help="emulation only")
    run.add_argument("--max-host-pct", type=float, default=85.0)
    run.add_argument("--golden-check", type=Path, default=None)
    run.add_argument("--force", action="store_true")
    run.add_argument("--busy-retries", type=int, default=60, help="times to wait for a quiet host before giving up on a run")
    run.add_argument("--busy-wait", type=float, default=60.0)
    run.add_argument("driver_args", nargs="*", help="extra driver arguments after --")
    summ = sub.add_parser("summarize")
    summ.add_argument("--out-dir", type=Path, action="append", required=True)
    summ.add_argument("--tier", action="append", default=[], metavar="LABEL=MEM_GB")
    done = sub.add_parser("done", help="exit 0 if the result file is a finished run with unchanged output")
    done.add_argument("result", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "done":
        return 0 if is_complete_file(args.result) else 1
    if args.cmd == "run":
        return run_all(args)
    limits = {t.split("=")[0]: float(t.split("=")[1]) for t in args.tier}
    print(summarize(args.out_dir, limits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
