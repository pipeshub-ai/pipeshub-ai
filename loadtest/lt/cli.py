"""Command line: run | compare | doctor | sources."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import compare as compare_mod
from . import report as report_mod
from . import sources as sources_mod
from .client import Client
from .runner import RunError, Runner
from .scenario import Scenario, ScenarioError
from .target import Target

HERE = Path(__file__).resolve().parent.parent
DEFAULT_RUNS = HERE / "runs"
DEFAULT_SCENARIOS = HERE / "scenarios"


def _load_dotenv() -> None:
    """Read loadtest/.env if present. Deliberately minimal — this is credentials
    plumbing, not a config system, and a hard python-dotenv dependency for it
    would be out of proportion."""
    env_file = HERE / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _resolve_scenario(value: str) -> Path:
    path = Path(value)
    if path.exists():
        return path
    for suffix in ("", ".yaml", ".yml", ".json"):
        candidate = DEFAULT_SCENARIOS / f"{value}{suffix}"
        if candidate.exists():
            return candidate
    raise SystemExit(f"scenario not found: {value} (looked in {DEFAULT_SCENARIOS})")


def cmd_run(args: argparse.Namespace) -> int:
    scenario = Scenario.load(_resolve_scenario(args.scenario))
    for override in ("units", "scale", "repeat", "warmup", "seed"):
        value = getattr(args, override, None)
        if value is not None:
            setattr(scenario, override, value)
    if args.no_profile:
        scenario.profile = False
    if getattr(args, "chaos", False):
        scenario.chaos = True
    scenario.validate()

    source = sources_mod.build(scenario.source, scenario.source_options)
    base_url = args.base_url or os.getenv("PIPESHUB_BASE_URL") or "http://localhost:3000"
    client = Client(base_url)
    target = Target(base_url=base_url, container=args.container)

    runner = Runner(scenario, source, client, target, Path(args.out), label=args.label)
    try:
        document = runner.run()
    except RunError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    path = report_mod.write(document, runner.dir)
    summary = document.get("summary") or {}
    print()
    print(f"report:  {path}")
    print(f"results: {runner.dir / 'result.json'}")
    if summary:
        rate = summary["records_per_s"]
        if not summary.get("cv_known", True):
            verdict = "spread unknown — single iteration, cannot qualify an A/B"
        elif summary["reproducible"]:
            verdict = f"cv {rate['cv']:.1%}, reproducible"
        else:
            verdict = f"cv {rate['cv']:.1%}, TOO NOISY TO A/B"
        print(f"throughput: {rate['median']:.2f} rec/s ({verdict})")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    baseline, candidate = Path(args.baseline), Path(args.candidate)
    out = Path(args.out) if args.out else candidate / "compare.html"
    path = compare_mod.write(baseline, candidate, out)
    call = compare_mod.verdict(compare_mod.load(baseline), compare_mod.load(candidate))
    print(f"{call['state'].upper()}: {call['reason']}")
    print(f"report: {path}")
    return 0 if call["state"] in ("better", "inconclusive") else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    base_url = args.base_url or os.getenv("PIPESHUB_BASE_URL") or "http://localhost:3000"
    target = Target(base_url=base_url, container=args.container)
    problems = target.preflight()

    print(f"base url:  {base_url}")
    print(f"container: {args.container}")
    if not problems:
        found = target.discover_processes()
        for pid, service in sorted(found.items(), key=lambda kv: kv[1]):
            print(f"  process  {service:<10} pid {pid}")
        print(f"  stateful {', '.join(target.stateful_containers()) or 'none found'}")

    try:
        client = Client(base_url)
        client.auth_headers()
        print(f"  auth     ok (org {client.org_id})")
    except Exception as exc:
        problems.append(f"cannot authenticate against {base_url}: {exc}")

    if args.source:
        source = sources_mod.build(args.source, {})
        problems += source.preflight()

    if problems:
        print("\nproblems:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nready")
    return 0


def cmd_rereport(args: argparse.Namespace) -> int:
    """Rebuild report.html from result.json + on-disk containers.jsonl."""
    import json

    runs_root = Path(args.runs)
    targets = [Path(p) for p in args.run] if args.run else sorted(runs_root.glob("*"))
    done = 0
    for run_dir in targets:
        result_path = run_dir / "result.json"
        if not result_path.is_file():
            continue
        doc = json.loads(result_path.read_text(encoding="utf-8"))
        report_mod.enrich_containers(doc, run_dir)
        result_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        path = report_mod.write(doc, run_dir)
        print(f"wrote {path}")
        done += 1
    if not done:
        print("no runs found", file=sys.stderr)
        return 1
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    from . import bootstrap

    base_url = args.base_url or os.getenv("PIPESHUB_BASE_URL") or "http://localhost:3000"
    try:
        result = bootstrap.run(
            base_url,
            HERE / ".env",
            email=args.email,
            password=args.password,
            org_name=args.org_name,
        )
    except bootstrap.BootstrapError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print("org created" if result["org_created"] else "org already existed — reused it")
    print(f"  user:      {result['email']}")
    print(f"  client id: {result['client_id']}")
    print(f"  written:   {result['env_path']}")
    print("\nnext: python loadtest/run.py doctor --source minio")
    return 0


def cmd_ledger(args: argparse.Namespace) -> int:
    from . import ledger

    rows = ledger.collect(Path(args.runs))
    print(ledger.render_text(rows))
    pairs = ledger.render_pairs(rows)
    if pairs:
        print(pairs)
    return 0


def cmd_sources(_: argparse.Namespace) -> int:
    for name, cls in sorted(sources_mod.registry.sources.items()):
        flag = "deterministic" if cls.deterministic else "NOT comparable between runs"
        compose = f"compose/{cls.compose_file}" if cls.compose_file else "no backing service"
        print(f"{name:<12} {cls.connector_type:<14} {flag:<28} {compose}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lt", description=__doc__)
    parser.add_argument("--base-url", help="Pipeshub base URL (default $PIPESHUB_BASE_URL or localhost:3000)")
    parser.add_argument("--container", default=os.getenv("LT_CONTAINER", "pipeshub-ai"),
                        help="name of the container running the services")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="execute a scenario and write a report")
    run.add_argument("scenario", help="scenario name or path")
    run.add_argument("--out", default=str(DEFAULT_RUNS), help="run output root")
    run.add_argument("--label", help="run id (default: <scenario>-<timestamp>)")
    run.add_argument("--units", type=int, help="override concurrent connectors")
    run.add_argument("--scale", type=int, help="override records per connector")
    run.add_argument("--repeat", type=int, help="override measured iterations")
    run.add_argument("--warmup", type=int, help="override discarded warmup iterations")
    run.add_argument("--seed", type=int, help="override corpus seed")
    run.add_argument("--no-profile", action="store_true", help="skip the profiled iteration")
    run.add_argument(
        "--chaos",
        action="store_true",
        help="fire every sync trigger twice ~200ms apart to force overlapping "
             "syncs; record counts must still come out identical",
    )
    run.set_defaults(func=cmd_run)

    cmp_ = sub.add_parser("compare", help="A/B two run directories")
    cmp_.add_argument("baseline")
    cmp_.add_argument("candidate")
    cmp_.add_argument("--out", help="output html (default: <candidate>/compare.html)")
    cmp_.set_defaults(func=cmd_compare)

    doc = sub.add_parser("doctor", help="check the environment is ready to run")
    doc.add_argument("--source", help="also preflight this load source")
    doc.set_defaults(func=cmd_doctor)

    boot = sub.add_parser(
        "bootstrap", help="create the org + OAuth client on a fresh stack and write .env"
    )
    boot.add_argument("--email", help=f"admin email (default {bootstrap_default('email')})")
    boot.add_argument("--password", help="admin password (must satisfy the backend policy)")
    boot.add_argument("--org-name", help="registered org name")
    boot.set_defaults(func=cmd_bootstrap)

    led = sub.add_parser("ledger", help="roll all completed runs into one table")
    led.add_argument("--runs", default=str(DEFAULT_RUNS), help="run output root")
    led.set_defaults(func=cmd_ledger)

    rr = sub.add_parser(
        "rereport",
        help="rebuild report.html (adds Neo4j charts from containers.jsonl)",
    )
    rr.add_argument("--runs", default=str(DEFAULT_RUNS), help="run output root")
    rr.add_argument("run", nargs="*", help="specific run directories (default: all)")
    rr.set_defaults(func=cmd_rereport)

    src = sub.add_parser("sources", help="list available load sources")
    src.set_defaults(func=cmd_sources)
    return parser


def bootstrap_default(key: str) -> str:
    from .bootstrap import DEFAULTS

    return DEFAULTS[key]


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ScenarioError, KeyError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
