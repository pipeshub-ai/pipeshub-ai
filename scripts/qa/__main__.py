"""python3 scripts/qa <command> — see docs/qa-engine/README.md."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    # Run as `python3 scripts/qa`: put scripts/ (not scripts/qa/) on the path so
    # module names here never shadow the standard library.
    sys.path[0] = str(Path(__file__).resolve().parent.parent)

from qa import (
    catalog,
    collect,
    command,
    config,
    culprit,
    history,
    impact,
    paths,
    planner,
    report,
    run,
    score,
    validate,
)


def _out(args) -> Path:
    out = Path(args.out)
    if not out.is_absolute():
        out = paths.tree_root() / out
    out.mkdir(parents=True, exist_ok=True)
    return out


def _dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(cfg: config.Config, out: Path, refresh: bool = False) -> list[dict]:
    """The full catalog for this checkout, cached per run directory."""
    cache = out / "catalog-rows.json"
    if cache.is_file() and not refresh:
        return _load(cache)
    rows = catalog.build(cfg)
    _dump(cache, rows)
    return rows


def _history(args) -> dict:
    store = Path(args.history) if getattr(args, "history", None) else None
    if store and store.is_file():
        return {"failing_on_main": history.failing_on_main(history.load(store))}
    return {}


# --- commands --------------------------------------------------------------------

def cmd_catalog(args) -> int:
    cfg = config.load()
    if args.action in ("build", "check"):
        rows = catalog.build(cfg, [s["id"] for s in catalog.committed_suites(cfg)])
        n = catalog.homeless(cfg, rows)
        if n:
            print(f"warning: {n} tests have no committed catalog (no suite for their edition)", file=sys.stderr)
    if args.action == "build":
        for p in catalog.write(cfg, rows):
            print(f"wrote {p.relative_to(cfg.root)}")
        return 0
    if args.action == "check":
        stale = catalog.stale(cfg, rows)
        for p in stale:
            print(f"stale: {p.relative_to(cfg.root)} (run: python3 scripts/qa catalog build)", file=sys.stderr)
        return 1 if stale else 0
    rows = catalog.build(cfg, args.suite or None)
    if args.action == "lint":
        errors, warnings = catalog.lint(cfg, rows, strict=args.strict)
        for w in warnings[: args.max_warnings]:
            print(f"warning: {w}")
        if len(warnings) > args.max_warnings:
            print(f"... {len(warnings) - args.max_warnings} more warnings")
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        print(f"{len(rows)} tests, {len(errors)} errors, {len(warnings)} warnings")
        return 1 if errors else 0
    for r in rows:
        print(json.dumps(r, sort_keys=True))
    return 0


def _changeset(cfg, args) -> tuple[dict, str]:
    if args.since:
        base, head, mode = collect.parent_or_empty(cfg.root, collect.rev(cfg.root, args.since)), args.head, "range"
    elif args.range:
        a, _, b = args.range.partition("..")
        base, head, mode = collect.rev(cfg.root, a), b or args.head, "range"
    elif args.base:
        base, head, mode = collect.rev(cfg.root, args.base), args.head, args.mode or "pr"
    else:
        base, head, mode = collect.resolve_base(cfg.root, args.target), args.head, "pr"
    return collect.collect(cfg, base, head, mode=args.mode or mode, include_worktree=args.worktree)


def cmd_collect(args) -> int:
    cfg = config.load()
    out = _out(args)
    changeset, diff = _changeset(cfg, args)
    _dump(out / "changeset.json", changeset)
    (out / "changeset.diff").write_text(diff, encoding="utf-8")
    print(f"{len(changeset['files'])} files, {len(changeset['commits'])} commits, {changeset['base'][:10]}..{changeset['head'][:10]}")
    return 0


def cmd_impact(args) -> int:
    cfg = config.load()
    if args.edition not in cfg.editions():
        print(f"unknown edition {args.edition!r}; known: {', '.join(cfg.editions())}", file=sys.stderr)
        return 2
    out = _out(args)
    changeset = _load(out / "changeset.json")
    rows = _rows(cfg, out, refresh=True)
    plan_input = impact.compute(cfg, changeset, rows, profile=args.profile, scope=args.edition, history=_history(args))
    _dump(out / "plan-input.json", plan_input)
    for line in impact.summarize(plan_input):
        print(line)
    return 0


def cmd_plan(args) -> int:
    cfg = config.load()
    out = _out(args)
    plan_input = _load(out / "plan-input.json")
    skipped = plan_input.get("planner") == "none"
    adapter = "none" if skipped else args.adapter or planner.default_adapter()
    status = planner.run(cfg.root, adapter, out / "plan-input.json", out / "plan-llm.json", timeout=args.timeout)
    if skipped:
        print(f"planner none: skipped ({plan_input.get('planner_reason') or 'not needed'})")
        return 0
    print(f"planner {adapter}: {status['status']}" + (f" ({status.get('error')})" if status.get("error") else ""))
    return 0


def cmd_validate(args) -> int:
    cfg = config.load()
    out = _out(args)
    plan_input = _load(out / "plan-input.json")
    llm_path = out / "plan-llm.json"
    llm = _load(llm_path) if llm_path.is_file() else None
    rows = catalog.index(_rows(cfg, out))
    plan = validate.merge(cfg, plan_input, llm, rows)
    _dump(out / "plan.json", plan)
    model = sum(1 for t in plan["tests"] if t["source"] == "model")
    print(f"plan: {len(plan['tests'])} tests ({model} from the model), profile {plan['profile']}, "
          f"{len(plan['dropped'])} dropped, {len(plan['budget_trimmed'])} trimmed")
    if args.emit_gh_outputs and os.environ.get("GITHUB_OUTPUT"):
        families = {rows[t["id"]]["family"] for t in plan["tests"] if t["id"] in rows}
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"tests={len(plan['tests'])}\n")
            fh.write(f"needs_stack={'true' if plan['stack']['needed'] else 'false'}\n")
            fh.write(f"graph_db={json.dumps(plan['stack']['graph_db'])}\n")
            fh.write(f"families={json.dumps(sorted(families))}\n")
            fh.write(f"matrix={json.dumps(plan['matrix'])}\n")
            fh.write(f"has_python={'true' if families & {'pytest', 'unittest'} else 'false'}\n")
            fh.write(f"has_node={'true' if families & {'mocha', 'vitest', 'npm-script', 'playwright'} else 'false'}\n")
    return 0


def _plan_from_tests(cfg, ids: list[str], rows: dict) -> dict:
    tests = [{"id": i, "source": "floor", "why": ["requested"], "grep": None} for i in ids]
    plan_input = {"edition": "all", "profile": "standard", "mode": "manual", "floor": {"items": {}},
                  "change": {"base": "", "head": collect.rev(cfg.root, "HEAD")}, "stack": {}}
    plan = validate.merge(cfg, plan_input, None, rows)
    plan["tests"] = tests
    plan["stack"] = validate._stack(cfg, plan_input, "standard", [rows[i] for i in ids if i in rows])
    return plan


def cmd_run(args) -> int:
    cfg = config.load()
    out = _out(args)
    rows = catalog.index(_rows(cfg, out))
    if args.tests or args.ids:
        ids = list(args.ids or [])
        if args.tests:
            ids += [line.strip() for line in Path(args.tests).read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
        plan = _plan_from_tests(cfg, ids, rows)
        _dump(out / "plan.json", plan)
    else:
        plan = _load(Path(args.plan) if args.plan else out / "plan.json")
    if args.graph_db:
        plan["stack"]["graph_db"] = [args.graph_db] if args.graph_db != "both" else ["neo4j", "arango"]
    try:
        stack_mode = args.stack or os.environ.get("QA_STACK", "none")
        result = run.run(cfg, plan, rows, out, stack_mode=stack_mode, base_url=args.base_url,
                         build=args.build, dry_run=args.dry_run, retry=not args.no_retry, scope=args.scope)
    finally:
        # The upload steps run even when the run raises, so the logs it already wrote must be clean first.
        report.redact_files(out)
    if args.dry_run:
        text = run.describe_dry_run(result)
        (out / "run-dry.txt").write_text(text, encoding="utf-8")
        print(text)
        return 0
    report.redact_results(result)
    _dump(out / ("results.json" if args.scope == "all" else f"results-{args.scope}.json"), result)
    from collections import Counter
    counts = Counter(t["outcome"] for t in result["tests"])
    print("results: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + f" ({result['seconds']}s)")
    if args.history:
        history.record(Path(args.history), plan, result)
    return 0


def _allowed(args) -> frozenset[str]:
    if args.strict:
        return frozenset()
    kinds = frozenset(k for k in args.allow_not_run.split(",") if k)
    unknown = kinds - set(report.NOT_RUN_KINDS)
    if unknown:
        raise SystemExit(f"--allow-not-run: unknown kind(s) {', '.join(sorted(unknown))}; use {', '.join(report.NOT_RUN_KINDS)}")
    return kinds


def cmd_report(args) -> int:
    cfg = config.load()
    allowed = _allowed(args)
    out = _out(args)
    plan = _load(out / "plan.json")
    parts = [_load(p) for p in sorted(out.glob("results*.json"))]
    results = report.merge_results(parts, [t["id"] for t in plan["tests"]]) if parts else None
    body = report.render(cfg, plan, results, allowed=allowed, run_url=args.run_url)
    (out / "report.md").write_text(body, encoding="utf-8")
    if args.summary:
        report.write_summary(body)
    if args.comment:
        repo = args.repo or os.environ.get("GITHUB_REPOSITORY")
        if not (repo and args.pr):
            print("--comment needs --pr and --repo (or GITHUB_REPOSITORY)", file=sys.stderr)
            return 2
        print(report.post_comment(repo, int(args.pr), body, plan["edition"]))
    if not (args.summary or args.comment or args.quiet):
        print(body)
    if args.gate:
        verdict, blocking = report.verdict(cfg, results, allowed)
        print(f"gate: {verdict}" + (f" ({len(blocking)} blocking)" if blocking else ""))
        return 1 if verdict == "fail" else 0
    return 0


def cmd_go(args) -> int:
    """collect -> impact -> plan -> validate -> run -> report in one command."""
    if getattr(args, "commit", None):
        return _go_at_commit(args)
    steps = [cmd_collect, cmd_impact, cmd_plan, cmd_validate]
    for step in steps:
        rc = step(args)
        if rc:
            return rc
    if not args.plan_only:
        args.plan = None
        args.tests = args.ids = None
        rc = cmd_run(args)
        if rc or args.dry_run:
            return rc
    args.summary = bool(os.environ.get("GITHUB_STEP_SUMMARY"))
    args.comment = False
    args.quiet = True
    rc = cmd_report(args)
    print(f"report: {Path(_out(args)) / 'report.md'}")
    return rc


def _on_base_branch(root: Path, sha: str, target: str | None) -> bool:
    branch = target or os.environ.get("GITHUB_BASE_REF") or "main"
    return any(
        subprocess.run(["git", "merge-base", "--is-ancestor", sha, ref], cwd=root, capture_output=True).returncode == 0
        for ref in (f"origin/{branch}", branch)
    )


def _go_at_commit(args) -> int:
    """Run go against a detached worktree at --commit. Rules and hooks stay the
    invoking engine's; results land in the caller's run directory."""
    args.out = str(_out(args))
    root = paths.tree_root()
    try:
        sha = collect.rev(root, args.commit)
    except subprocess.CalledProcessError:
        print(f"unknown commit {args.commit}", file=sys.stderr)
        return 2
    args.commit = None
    args.head, args.build, args.stack = sha, True, args.stack or "managed"
    if not (args.since or args.range or args.base) and _on_base_branch(root, sha, args.target):
        # A merge-base default would be the commit itself: test the commit's own change instead.
        try:
            args.base = collect.rev(root, f"{sha}^")
        except subprocess.CalledProcessError:
            print(f"note: {sha[:12]} is a root commit with no parent; running the full profile", file=sys.stderr)
            args.profile = "full"
    saved = os.environ.get("QA_ROOT")
    with collect.worktree(root, sha) as tree:
        os.environ["QA_ROOT"] = str(tree)
        try:
            return cmd_go(args)
        finally:
            if saved is None:
                os.environ.pop("QA_ROOT", None)
            else:
                os.environ["QA_ROOT"] = saved


def cmd_history(args) -> int:
    entries = history.load(Path(args.store))
    if args.action == "failing":
        for t in history.failing_on_main(entries, args.branch):
            print(t)
    elif args.action == "flaky":
        for tid, rate in sorted(history.flaky_rates(entries).items(), key=lambda kv: -kv[1]):
            print(f"{rate:.3f} {tid}")
    else:
        out = _out(args)
        history.record(Path(args.store), _load(out / "plan.json"), _load(out / "results.json"))
    return 0


def cmd_command(args) -> int:
    try:
        parsed = command.parse(args.body, args.permission, args.admin_team)
    except command.CommandError as err:
        print(json.dumps({"ok": False, "error": str(err)}))
        return 1
    print(json.dumps({"ok": True, **parsed}))
    return 0


def cmd_bisect(args) -> int:
    cfg = config.load()
    a, _, b = args.range.partition("..")
    ids = [line.strip() for line in Path(args.tests).read_text(encoding="utf-8").splitlines() if line.strip()]

    def run_ids(root: Path, test_ids: list[str]) -> dict[str, str]:
        wcfg = config.load(root)
        out = Path(args.out).resolve() / "bisect" / root.name
        out.mkdir(parents=True, exist_ok=True)
        rows = catalog.index(catalog.build(wcfg))
        plan = _plan_from_tests(wcfg, [i for i in test_ids if i in rows], rows)
        res = run.run(wcfg, plan, rows, out, stack_mode=args.stack, base_url=args.base_url, retry=False)
        return {t["id"]: t["outcome"] for t in res["tests"]}

    result = culprit.search(cfg, collect.rev(cfg.root, a), b or "HEAD", ids, culprit.worktree_probe(run_ids), max_steps=args.max_steps)
    print(json.dumps(result, indent=2))
    return 0


def cmd_score(args) -> int:
    cfg = config.load()
    result = score.run(cfg, Path(args.cases), args.adapter, args.profile)
    print(json.dumps(result, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="qa", description="Select, run and report the tests a change needs.")
    p.add_argument("--out", default=os.environ.get("QA_OUT", paths.DEFAULT_OUT), help="run directory (default qa-out)")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("catalog", help="build, check or lint the test catalog")
    c.add_argument("action", choices=["build", "check", "lint", "show"])
    c.add_argument("--suite", action="append")
    c.add_argument("--strict", action="store_true")
    c.add_argument("--max-warnings", type=int, default=20)
    c.set_defaults(fn=cmd_catalog)

    def change_args(sp):
        g = sp.add_mutually_exclusive_group()
        g.add_argument("--since", help="this commit and everything after it")
        g.add_argument("--range", help="A..B")
        g.add_argument("--base", help="explicit base ref")
        sp.add_argument("--head", default=None, help="head ref (default HEAD)")
        sp.add_argument("--target", default=None, help="PR target branch for the merge-base (default GITHUB_BASE_REF or main)")
        sp.add_argument("--worktree", action="store_true", help="include uncommitted changes")
        sp.add_argument("--mode", choices=["pr", "range", "full", "missing", "plan"], default=None)

    def impact_args(sp):
        sp.add_argument("--profile", default=os.environ.get("QA_PROFILE", "standard"), choices=impact.PROFILE_ORDER)
        sp.add_argument("--edition", default=os.environ.get("QA_EDITION", "all"))
        sp.add_argument("--history", default=os.environ.get("QA_HISTORY"))

    def run_args(sp):
        sp.add_argument("--stack", default=None, choices=["none", "managed", "external"])
        sp.add_argument("--base-url", default=None)
        sp.add_argument("--graph-db", choices=["neo4j", "arango", "both"])
        sp.add_argument("--build", action="store_true", help="build images before compose up (managed)")
        sp.add_argument("--dry-run", action="store_true")
        sp.add_argument("--no-retry", action="store_true")
        sp.add_argument("--scope", choices=["all", "unit", "stack"], default="all",
                        help="unit: tests that need no stack; stack: integration and e2e only")

    s = sub.add_parser("collect", help="what changed")
    change_args(s)
    s.set_defaults(fn=cmd_collect)

    s = sub.add_parser("impact", help="compute the floor")
    impact_args(s)
    s.set_defaults(fn=cmd_impact)

    s = sub.add_parser("plan", help="ask a model for additions")
    s.add_argument("--adapter", choices=sorted(planner.ADAPTERS))
    s.add_argument("--timeout", type=int, default=600)
    s.set_defaults(fn=cmd_plan)

    s = sub.add_parser("validate", help="merge floor and additions into plan.json")
    s.add_argument("--emit-gh-outputs", action="store_true")
    s.set_defaults(fn=cmd_validate)

    s = sub.add_parser("run", help="execute a plan or a list of test ids")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--plan")
    g.add_argument("--tests", help="file with one catalog id per line")
    g.add_argument("--ids", nargs="+")
    run_args(s)
    s.add_argument("--history", default=os.environ.get("QA_HISTORY"))
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("report", help="render the report and gate")
    s.add_argument("--comment", action="store_true")
    s.add_argument("--pr")
    s.add_argument("--repo")
    s.add_argument("--summary", action="store_true")
    s.add_argument("--gate", action="store_true")
    s.add_argument("--strict", action="store_true", help="every not_run blocks")
    s.add_argument("--allow-not-run", default=",".join(sorted(report.DEFAULT_ALLOWED)), metavar="KINDS", help="not_run kinds that do not block (comma list)")
    s.add_argument("--run-url")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("go", help="collect, impact, plan, validate, run and report")
    change_args(s)
    impact_args(s)
    run_args(s)
    s.add_argument("--adapter", choices=sorted(planner.ADAPTERS))
    s.add_argument("--timeout", type=int, default=600)
    s.add_argument("--commit", metavar="SHA",
                   help="run in a detached worktree at SHA (implies --build; --stack defaults to managed)")
    s.add_argument("--plan-only", action="store_true")
    s.add_argument("--strict", action="store_true")
    s.add_argument("--allow-not-run", default=",".join(sorted(report.DEFAULT_ALLOWED)), metavar="KINDS")
    s.add_argument("--run-url")
    s.add_argument("--gate", action="store_true")
    s.set_defaults(fn=cmd_go, emit_gh_outputs=False, pr=None, repo=None)
    go_parser = s

    s = sub.add_parser("history", help="run history")
    s.add_argument("action", choices=["record", "failing", "flaky"])
    s.add_argument("--store", default=os.environ.get("QA_HISTORY", "qa-out/history.jsonl"))
    s.add_argument("--branch", default="main")
    s.set_defaults(fn=cmd_history)

    s = sub.add_parser("command", help="parse a /qa PR comment")
    s.add_argument("--body", required=True)
    s.add_argument("--permission", required=True)
    s.add_argument("--admin-team", action="store_true")
    s.set_defaults(fn=cmd_command)

    s = sub.add_parser("bisect", help="find the commit that broke tests in a range")
    s.add_argument("--range", required=True)
    s.add_argument("--tests", required=True)
    s.add_argument("--max-steps", type=int, default=8)
    s.add_argument("--stack", default="none", choices=["none", "managed", "external"])
    s.add_argument("--base-url")
    s.set_defaults(fn=cmd_bisect)

    s = sub.add_parser("score", help="score a planner adapter on past changes")
    s.add_argument("--cases", default=str(paths.REPO / "scripts/qa/score/cases"))
    s.add_argument("--adapter", default="none", choices=sorted(planner.ADAPTERS))
    s.add_argument("--profile", default="standard", choices=impact.PROFILE_ORDER)
    s.set_defaults(fn=cmd_score)

    args = p.parse_args(argv)
    if getattr(args, "commit", None) and args.head:
        go_parser.error("--commit and --head cannot be used together (--commit sets the head)")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
