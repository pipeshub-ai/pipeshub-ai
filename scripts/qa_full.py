#!/usr/bin/env python3
"""QA-AUTO: the full QA pass in one command, with one HTML and JSON report.

    scripts/qa_full.sh --out <dir>                      every lane
    scripts/qa_full.sh --out <dir> --lanes unit,frontend only these lanes
    scripts/qa_full.sh --list                           the lanes, and why any cannot run here
    scripts/qa_full.sh --out <dir> --update-ux-baselines   take the UX captures as the new baselines
    scripts/qa_final.sh --out <dir> [--sample]          QA-FINAL: every lane, then the matrix's summary (final.md)

Every lane runs its suites, reads their JUnit or JSON results, and reruns each failed test once, alone: a test that
passes on the rerun is labelled flaky, one that fails again is real. A rerun only classifies; it never turns a lane
green (test plan 06 §4: no retries). The report is `<dir>/report.json` and `<dir>/index.html`: pass/fail counts per
lane, the failures with their label, the perf rig's SLO verdicts (SLO.md, `slo_gates.py`), the browser latency
suite's p50/p95 against its budgets (SLO.md "Browser latency"), and the UX screenshot gallery with each capture's
pixel diff against its baseline.

The final pass (`--final`, scripts/qa_final.sh) adds the lanes that bring their own stacks up (MCP-E2E modules on
hub-on stacks, the options matrix's ST-1 rows, the PostgreSQL, no-route-out and key-rotation stacks, the fault
suite) and writes `final.md`/`final.json`: each row of docs/test-plan/QA-FINAL.md marked by this run (qa_matrix.py).
`--sample` runs each lane on a small selection of its suites, to prove the lanes work in well under the hours a
full pass takes; a sample judges no row.

Exit status: 0 when every lane passed, 1 when a real failure (or a lane error) happened, 3 when the only failures
were flaky.

Lanes need what the host provides: Docker (heavy steps run through the wrapper `$PXH_LANE` names, when set, e.g. one
that admits a heavy lane only while CPU and RAM are under 80%), a MongoDB replica set and Redis for the store itests and
the perf rig at `$MONGO_IT_URI` and `$REDIS_IT_URL` (as the itests read them; the rig also needs the replica set's
container in `$QA_MONGO_CONTAINER`, where it drops its database), PostgreSQL 17 and PgBouncer under `$QA_PG_ROOT` (migrations from the venv at `$QA_PG_MIGRATE_VENV`), the sandbox image. The e2e stack the browser
lanes share runs its own MongoDB and Redis (`E2E_STACK=dev-harness` unless set), so no neighbour's writes reach the
latency samples. No lane makes a paid or live model call: the stacks run the
scripted model, and the human-test stack's smoke pins it.

Standard library only (the scripts job runs its unit tests, scripts/test_qa_full.py, with no venv).
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
PY_DIR = ROOT / "backend" / "python"
NODE_DIR = ROOT / "backend" / "nodejs" / "apps"
FE_DIR = ROOT / "frontend"
IT_DIR = ROOT / "integration-tests"
RUN_SH = ROOT / "deployment" / "e2e" / "run.sh"
LATENCY_BUDGETS = FE_DIR / "tests" / "e2e" / "qa" / "latency-budgets.json"
UX_MANIFEST = FE_DIR / "tests" / "e2e" / "qa" / "ux-captures.json"

PASSED, FAILED, SKIPPED = "passed", "failed", "skipped"
FLAKY, REAL = "flaky", "real"


# ---- results -------------------------------------------------------------------------------------------------


@dataclass
class Case:
    """One test as its runner reported it. `rerun` is the outcome of running it again alone, when it failed."""

    id: str
    name: str
    status: str
    file: str = ""
    line: int = 0
    message: str = ""
    seconds: float = 0.0
    rerun: str = ""

    @property
    def label(self) -> str:
        if self.status != FAILED:
            return ""
        return FLAKY if self.rerun == PASSED else REAL


@dataclass
class Step:
    name: str
    status: str = "pending"  # passed, failed, flaky, error, skipped
    seconds: float = 0.0
    log: str = ""
    note: str = ""
    exit_code: int | None = None
    cases: list[Case] = field(default_factory=list)
    skips: list[dict[str, str]] = field(default_factory=list)
    # Passed tests the report counts but does not list (a run has some 180,000; report.json keeps the rest).
    passed_unlisted: int = 0

    def counts(self) -> dict[str, int]:
        c = {"tests": len(self.cases) + self.passed_unlisted, PASSED: self.passed_unlisted, FAILED: 0, SKIPPED: 0, FLAKY: 0, REAL: 0}
        for case in self.cases:
            c[case.status] = c.get(case.status, 0) + 1
            if case.label:
                c[case.label] += 1
        return c


@dataclass
class Lane:
    id: str
    title: str
    status: str = "pending"
    seconds: float = 0.0
    reason: str = ""
    steps: list[Step] = field(default_factory=list)


def judge_step(step: Step) -> str:
    """A step's status from its exit code and its cases: a non-zero exit with no failed case is an error (the
    runner crashed, or a check that is not a test failed); failures that all passed on their rerun are flaky."""
    if step.status == SKIPPED:
        return SKIPPED
    failed = [c for c in step.cases if c.status == FAILED]
    if failed:
        return FLAKY if all(c.label == FLAKY for c in failed) else FAILED
    if step.exit_code not in (0, None):
        return "error"
    return PASSED


def judge_lane(lane: Lane) -> str:
    statuses = {s.status for s in lane.steps}
    if not lane.steps or statuses == {SKIPPED}:
        return SKIPPED
    for worst in ("error", FAILED, FLAKY):
        if worst in statuses:
            return worst
    return PASSED


def exit_status(lanes: list[Lane]) -> int:
    statuses = {lane.status for lane in lanes}
    if statuses & {"error", FAILED}:
        return 1
    if FLAKY in statuses:
        return 3
    return 0


# ---- result readers ----------------------------------------------------------------------------------------


def read_junit(path: Path) -> list[Case]:
    """Cases of a JUnit file (pytest, vitest, Playwright). Missing or unreadable: no cases."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return []
    cases: list[Case] = []
    for tc in root.iter("testcase"):
        classname, name = tc.get("classname", ""), tc.get("name", "")
        status, message = PASSED, ""
        bad = tc.find("failure")
        if bad is None:
            bad = tc.find("error")
        if bad is not None:
            status = FAILED
            message = ((bad.get("message") or "") + "\n" + (bad.text or "")).strip()
        elif tc.find("skipped") is not None:
            status = SKIPPED
            skip = tc.find("skipped")
            reason = " ".join((p.get("value") or "").strip() for p in tc.iter("property") if p.get("name") in ("skip", "fixme"))
            message = (reason or (skip.get("message") if skip is not None else "") or "").strip()
        cases.append(Case(id=f"{classname}::{name}", name=name, status=status, file=tc.get("file", "") or classname,
                          message=message[:4000], seconds=float(tc.get("time") or 0)))
    return cases


def pytest_node_id(classname: str, name: str, base: Path) -> str | None:
    """A JUnit classname (dotted path, then classes) and name as a pytest node id, from the files under `base`."""
    parts = classname.split(".")
    for i in range(len(parts), 0, -1):
        path = "/".join(parts[:i]) + ".py"
        if (base / path).is_file():
            return "::".join([path, *parts[i:], name])
    return None


def read_mocha_json(path: Path) -> list[Case]:
    """Cases of mocha's JSON reporter (`--reporter json --reporter-option output=<path>`)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    failed = {(t.get("file"), t.get("fullTitle")) for t in data.get("failures", [])}
    pending = {(t.get("file"), t.get("fullTitle")) for t in data.get("pending", [])}
    cases: list[Case] = []
    seen: set[tuple[str, str]] = set()
    for t in data.get("tests", []) + data.get("failures", []):
        key = (t.get("file") or "", t.get("fullTitle") or "")
        if key in seen:
            continue
        seen.add(key)
        err = t.get("err") or {}
        status = FAILED if key in failed else SKIPPED if key in pending else PASSED
        cases.append(Case(id=f"{key[0]}::{key[1]}", name=key[1], status=status, file=key[0],
                          message=str(err.get("message") or "")[:4000], seconds=(t.get("duration") or 0) / 1000))
    return cases


def read_playwright_json(path: Path) -> list[Case]:
    """Cases of Playwright's JSON reporter, with each test's file and line (what a rerun names)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    cases: list[Case] = []

    def walk(suite: dict[str, Any], titles: list[str]) -> None:
        here = [*titles, suite["title"]] if suite.get("title") and not suite.get("file") == suite.get("title") else titles
        for spec in suite.get("specs", []):
            for test in spec.get("tests", []):
                results = test.get("results") or []
                last = results[-1] if results else {}
                outcome = test.get("status")  # expected, unexpected, flaky, skipped
                status = {"expected": PASSED, "unexpected": FAILED, "flaky": FAILED, "skipped": SKIPPED}.get(outcome, FAILED)
                errors = last.get("errors") or ([last["error"]] if last.get("error") else [])
                message = "\n".join(_strip_ansi(str(e.get("message") or "")) for e in errors)
                if status == SKIPPED:
                    message = " ".join(str(a.get("description") or "") for a in test.get("annotations", []) if a.get("type") in ("skip", "fixme"))
                title = " › ".join([*here, spec.get("title", "")])
                cases.append(Case(id=f"{test.get('projectName', '')}::{spec.get('file', '')}::{title}", name=title,
                                  status=status, file=spec.get("file", ""), line=int(spec.get("line") or 0),
                                  message=message.strip()[:4000], seconds=sum(r.get("duration", 0) for r in results) / 1000))
        for child in suite.get("suites", []):
            walk(child, here)

    for suite in data.get("suites", []):
        walk(suite, [])
    return cases


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


# ---- latency and perf ----------------------------------------------------------------------------------------


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile (the rank rounded up), as the latency budgets are written."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-int(q * 100) * len(ordered) // 100))
    return ordered[min(rank, len(ordered)) - 1]


def judge_latency(samples: dict[str, list[float]], budgets: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per budgeted metric: its p50/p95 against the budget; a metric with no samples is `missing`."""
    rows = []
    for metric, budget in budgets.get("metrics", {}).items():
        values = samples.get(metric, [])
        p50, p95 = percentile(values, 0.50), percentile(values, 0.95)
        if not values:
            status = "missing"
        elif (p50 is not None and p50 > budget["p50_ms"]) or (p95 is not None and p95 > budget["p95_ms"]):
            status = FAILED
        else:
            status = PASSED
        rows.append({"metric": metric, "title": budget.get("title", metric), "samples": len(values),
                     "p50_ms": p50, "p95_ms": p95, "budget_p50_ms": budget["p50_ms"], "budget_p95_ms": budget["p95_ms"],
                     "lane": budget.get("lane", ""), "status": status})
    return rows


def read_latency_samples(folder: Path) -> dict[str, list[float]]:
    """Every `latency-*.json` the latency specs wrote: `{"metric": name, "ms": [..]}` per file, merged by metric."""
    samples: dict[str, list[float]] = {}
    for path in sorted(folder.glob("**/latency-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for metric, values in (data.get("samples") or {}).items():
            samples.setdefault(metric, []).extend(float(v) for v in values)
    return samples


# ---- running -----------------------------------------------------------------------------------------------


@dataclass
class Ctx:
    out: Path
    python: str
    env: dict[str, str]
    lane_cmd: list[str]
    e2e_run: str
    state_dir: Path
    update_ux: bool
    soak_minutes: float
    rig_samples: int
    keep_stack: bool
    reuse_stack: bool
    # QA-FINAL --sample: each lane on a small selection of its own suites, which proves the lane's wiring (commands,
    # stacks, readers, report) in minutes instead of its hours; never a verdict on the rows.
    sample: bool = False
    stack_up: bool = False
    stack_failed: str = ""
    log: Callable[[str], None] = lambda line: print(line, flush=True)

    @property
    def work(self) -> Path:
        return self.state_dir / self.e2e_run

    @property
    def pw_dir(self) -> Path:
        return self.work / "pw"


def run_cmd(ctx: Ctx, argv: list[str], *, cwd: Path, log: Path, env: dict[str, str] | None = None,
            heavy: bool = False, timeout: float | None = None) -> tuple[int, float]:
    """Runs one command with its output in `log`; a heavy one through the host's lane admission."""
    full = [*ctx.lane_cmd, *argv] if heavy and ctx.lane_cmd else argv
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as handle:
        handle.write(f"$ (cd {cwd} && {shlex.join(full)})\n")
        handle.flush()
        try:
            done = subprocess.run(full, cwd=cwd, env={**ctx.env, **(env or {})}, stdout=handle, stderr=subprocess.STDOUT,
                                  timeout=timeout, check=False)
            code = done.returncode
        except subprocess.TimeoutExpired:
            handle.write(f"\nqa_full: timed out after {timeout} s\n")
            code = 124
    return code, time.monotonic() - started


def rel(ctx: Ctx, path: Path) -> str:
    try:
        return str(path.relative_to(ctx.out))
    except ValueError:
        return str(path)


def pytest_step(ctx: Ctx, lane: str, name: str, args: list[str], *, env: dict[str, str] | None = None,
                heavy: bool = True, timeout: float = 7200, cwd: Path = PY_DIR) -> Step:
    """A pytest run with a JUnit file, then each failed test again alone, under the same bounds. `cwd` is the
    suite's rootdir (backend/python; the options matrix and the fault suite have their own)."""
    step = Step(name)
    junit = ctx.out / "lanes" / lane / f"{slug(name)}.xml"
    log = junit.with_suffix(".log")
    argv = [ctx.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml", str(junit), *args]
    step.exit_code, step.seconds = run_cmd(ctx, argv, cwd=cwd, log=log, env=env, heavy=heavy, timeout=timeout)
    step.log = rel(ctx, log)
    step.cases = read_junit(junit)
    failed = [c for c in step.cases if c.status == FAILED]
    ids: dict[str, Case] = {}
    for case in failed:
        classname = case.id.rsplit("::", 1)[0]
        node = pytest_node_id(classname, case.name, cwd)
        if node:
            ids[node] = case
            case.id = node
    if ids:
        rerun = junit.with_name(junit.stem + "-rerun.xml")
        code, _ = run_cmd(ctx, [ctx.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
                                *rerun_pytest_options(args), "--junitxml", str(rerun), *ids], cwd=cwd,
                          log=rerun.with_suffix(".log"), env=env, heavy=heavy, timeout=timeout)
        again = {pytest_node_id(c.id.rsplit("::", 1)[0], c.name, cwd): c for c in read_junit(rerun)}
        for node, case in ids.items():
            case.rerun = again[node].status if node in again else FAILED
    for case in failed:
        case.rerun = case.rerun or FAILED
    step.status = judge_step(step)
    return step


def mocha_step(ctx: Ctx, lane: str, name: str, base: list[str], *, env: dict[str, str] | None = None,
               timeout: float = 5400) -> Step:
    """Mocha (`base` is the command line up to its own options: an npm script that ends in mocha, then `--`), with
    mocha's JSON reporter; failed tests rerun by their full title."""
    step = Step(name)
    report = ctx.out / "lanes" / lane / f"{slug(name)}.json"
    log = report.with_suffix(".log")
    argv = [*base, "--reporter", "json", "--reporter-option", f"output={report}"]
    step.exit_code, step.seconds = run_cmd(ctx, argv, cwd=NODE_DIR, log=log, env=env, heavy=True, timeout=timeout)
    step.log = rel(ctx, log)
    step.cases = read_mocha_json(report)
    failed = [c for c in step.cases if c.status == FAILED]
    if failed:
        rerun = report.with_name(report.stem + "-rerun.json")
        grep = "^(" + "|".join(re.escape(c.name) for c in failed) + ")$"
        argv = [*base, "--reporter", "json", "--reporter-option", f"output={rerun}", "--grep", grep]
        run_cmd(ctx, argv, cwd=NODE_DIR, log=rerun.with_suffix(".log"), env=env, heavy=True, timeout=timeout)
        again = {c.name: c.status for c in read_mocha_json(rerun)}
        for case in failed:
            case.rerun = again.get(case.name, FAILED)
    step.status = judge_step(step)
    return step


def npm_mocha(script: str) -> list[str]:
    """An npm script that ends in mocha: what follows `--` reaches mocha."""
    return ["npm", "run", "--silent", script, "--"]


# `npm test` without its coverage floor (run as its own step): the mocha run, under c8 as the floor reads it.
NODE_UNIT = ["npx", "cross-env", "TS_NODE_PROJECT=tsconfig.test.json", "c8", "mocha", "--parallel", "--jobs", "4"]


def mocharc_requires(path: Path = NODE_DIR / ".mocharc.yaml") -> list[str]:
    """The `require:` list of .mocharc.yaml (a flat YAML list; read by hand, the scripts job has no YAML parser)."""
    out, inside = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if re.match(r"^require:\s*$", line):
            inside = True
            continue
        item = re.match(r"^\s+-\s*['\"]?([^'\"]+?)['\"]?\s*$", line)
        if inside and item:
            out.append(item.group(1))
        elif inside and line.strip():
            inside = False
    return out


def node_files(files: list[str]) -> list[str]:
    """Mocha over these files only. `.mocharc.yaml`'s `spec` glob would add the whole suite to any file named on the
    command line, so the config is left out and its requires (the global mocks every test expects) named again."""
    requires = [arg for item in mocharc_requires() for arg in ("--require", item)]
    return ["npx", "cross-env", "TS_NODE_PROJECT=tsconfig.test.json", "mocha", "--no-config", *requires,
            "--extension", "ts", "--timeout", "10000", "--exit", *files]


def npm_script_files(script: str, files: list[str]) -> list[str]:
    """An npm script that ends in mocha, over `files` instead of its own quoted globs (a sample run). Its c8 wrapper
    goes too: a coverage floor over a few files would fail on the files it was never meant to read."""
    scripts = json.loads((NODE_DIR / "package.json").read_text(encoding="utf-8"))["scripts"]
    words = [w for w in shlex.split(scripts[script]) if "*" not in w]
    if "c8" in words:
        del words[words.index("c8"):words.index("mocha")]
    if "--reporter" in words:
        at = words.index("--reporter")
        del words[at:at + 2]
    return ["npx", *words, *files]


def plain_step(ctx: Ctx, lane: str, name: str, argv: list[str], *, cwd: Path, env: dict[str, str] | None = None,
               heavy: bool = False, timeout: float = 3600) -> Step:
    """A check with no per-test results (a type check, a lint, a coverage floor): its exit code is its result."""
    step = Step(name)
    log = ctx.out / "lanes" / lane / f"{slug(name)}.log"
    step.exit_code, step.seconds = run_cmd(ctx, argv, cwd=cwd, log=log, env=env, heavy=heavy, timeout=timeout)
    step.log = rel(ctx, log)
    step.status = PASSED if step.exit_code == 0 else "error"
    return step


def vitest_step(ctx: Ctx, lane: str, name: str = "vitest", files: list[str] | None = None) -> Step:
    """vitest over `files` (paths under frontend/), or the whole suite; failed files run again alone."""
    step = Step(name)
    junit = ctx.out / "lanes" / lane / f"{slug(name)}.xml"
    log = junit.with_suffix(".log")
    argv = ["npx", "vitest", "run", "--reporter=default", "--reporter=junit", f"--outputFile.junit={junit}", *(files or [])]
    step.exit_code, step.seconds = run_cmd(ctx, argv, cwd=FE_DIR, log=log, heavy=True, timeout=3600)
    step.log = rel(ctx, log)
    step.cases = read_junit(junit)
    failed = [c for c in step.cases if c.status == FAILED]
    files = sorted({c.file for c in failed if c.file and (FE_DIR / c.file).is_file()})
    if files:
        rerun = junit.with_name(f"{junit.stem}-rerun.xml")
        run_cmd(ctx, ["npx", "vitest", "run", "--reporter=junit", f"--outputFile.junit={rerun}", *files], cwd=FE_DIR,
                log=rerun.with_suffix(".log"), heavy=True, timeout=3600)
        again = {c.id: c.status for c in read_junit(rerun)}
        for case in failed:
            case.rerun = again.get(case.id, FAILED)
    for case in failed:
        case.rerun = case.rerun or FAILED
    step.status = judge_step(step)
    return step


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ---- the e2e stacks ---------------------------------------------------------------------------------------------


def e2e_env(ctx: Ctx, **extra: str) -> dict[str, str]:
    return {"E2E_RUN": ctx.e2e_run, "E2E_STATE_DIR": str(ctx.state_dir), "E2E_KEEP_WORK": "1",
            "E2E_SANDBOX_IMAGE": ctx.env.get("E2E_SANDBOX_IMAGE", "pxh-sandbox:pr1"),
            "E2E_SITE_PACKAGES": ctx.env["E2E_SITE_PACKAGES"],
            # A fixed admin, so the UX captures do not change with the run's id.
            "E2E_ADMIN_EMAIL_WANT": "qa-admin@e2etest.pipeshub.local",
            "PXH_QA_RESULTS": "/work/pw/qa", "PXH_UX_BASELINES": "/work/pw/ux-baselines", **extra}


def ensure_stack(ctx: Ctx, lane: Lane) -> bool:
    """The harness lane's stack, with indexing (the cite journey reads indexed records), brought up once."""
    if ctx.stack_up:
        return True
    if ctx.stack_failed:
        lane.reason = f"the e2e stack did not come up: {ctx.stack_failed}"
        return False
    if ctx.reuse_stack:
        ctx.stack_up = (ctx.work / "seed.json").is_file()
        if not ctx.stack_up:
            ctx.stack_failed = f"no stack {ctx.e2e_run} is up under {ctx.state_dir}"
        return ctx.stack_up
    step = Step("stack up")
    log = ctx.out / "lanes" / "stack" / "up.log"
    step.exit_code, step.seconds = run_cmd(ctx, ["bash", str(RUN_SH), "up"], cwd=ROOT, log=log,
                                           env=e2e_env(ctx, E2E_INDEXING="1"), heavy=True, timeout=3600)
    step.log = rel(ctx, log)
    step.status = PASSED if step.exit_code == 0 else "error"
    lane.steps.append(step)
    if step.exit_code != 0:
        ctx.stack_failed = f"run.sh up exited {step.exit_code} (log {step.log})"
        return False
    ctx.stack_up = True
    return True


def stack_down(ctx: Ctx) -> Step | None:
    if not ctx.stack_up or ctx.keep_stack or ctx.reuse_stack:
        return None
    step = Step("stack down")
    log = ctx.out / "lanes" / "stack" / "down.log"
    step.exit_code, step.seconds = run_cmd(ctx, ["bash", str(RUN_SH), "down", ctx.e2e_run], cwd=ROOT, log=log,
                                           env=e2e_env(ctx), timeout=900)
    step.log = rel(ctx, log)
    step.status = PASSED if step.exit_code == 0 else "error"
    ctx.stack_up = False
    keep_run_output(ctx, "stack", ctx.state_dir / f"kept-{ctx.e2e_run}", "shared")
    return step


def pw_step(ctx: Ctx, lane: str, name: str, project: str, *, skips_allowed: bool, extra: list[str] | None = None,
            env: dict[str, str] | None = None, timeout: float = 5400, rerun: bool = True, run: str = "") -> Step:
    """A Playwright project on the stack (run.sh pw), with JSON and JUnit. A failed test's whole spec file runs
    again (a serial spec skips what follows a failure, and its first tests make the state the failed one reads); a
    test skipped with no reason, behind a failure, takes its rerun's outcome."""
    step = Step(name)
    run = run or ctx.e2e_run
    pw_dir = ctx.state_dir / run / "pw"
    tag = f"qa-{slug(name)}"
    log = ctx.out / "lanes" / lane / f"{slug(name)}.log"
    argv = ["bash", str(RUN_SH), "pw", run, "--project", project, "--reporter", "list,junit,json", *(extra or [])]
    step.exit_code, step.seconds = run_cmd(ctx, argv, cwd=ROOT, log=log,
                                           env={**e2e_env(ctx, E2E_PW_TAG=tag, E2E_RUN=run), **(env or {})},
                                           heavy=True, timeout=timeout)
    step.log = rel(ctx, log)
    step.cases = [c for c in read_playwright_json(pw_dir / f"results-{tag}.json") if not _is_setup(c)]
    _copy_pw(ctx, lane, tag, pw_dir)
    failed = [c for c in step.cases if c.status == FAILED and c.file]
    if failed and rerun:
        rtag = f"{tag}-rerun"
        files = sorted({f"tests/e2e/{c.file}" for c in failed})
        run_cmd(ctx, ["bash", str(RUN_SH), "pw", run, "--project", project, *rerun_options(extra),
                      "--workers", "1", "--reporter", "list,json", *files], cwd=ROOT,
                log=log.with_name(f"{slug(name)}-rerun.log"),
                env={**e2e_env(ctx, E2E_PW_TAG=rtag, E2E_RUN=run), **(env or {})}, heavy=True, timeout=timeout)
        _copy_pw(ctx, lane, rtag, pw_dir)
        apply_rerun(step.cases, read_playwright_json(pw_dir / f"results-{rtag}.json"), {c.file for c in failed})
    for case in step.cases:
        if case.status == FAILED:
            case.rerun = case.rerun or FAILED
        elif case.status == SKIPPED:
            step.skips.append({"test": case.name, "reason": case.message or "NO REASON GIVEN"})
    if not skips_allowed and step.skips:
        step.note = f"{len(step.skips)} skipped on a real-service lane, which fails it (test plan 00 Q10)"
        step.exit_code = step.exit_code or 1
    if step.exit_code and not step.cases:
        step.note = step.note or "Playwright reported no test: see the log"
    step.status = judge_step(step)
    if step.note and step.status == PASSED:
        step.status = FAILED
    return step


def rerun_pytest_options(args: list[str]) -> list[str]:
    """The first run's own options a rerun of named tests keeps (`--mcp-hub`, `--stacks=…`, `--broker=…`, a timeout):
    every `--` option, written with `=`; the selection (`-m`, `-k`, paths) and the worker count are the rerun's."""
    return [a for a in args if a.startswith("--")]


def rerun_options(extra: list[str] | None) -> list[str]:
    """The first run's options a rerun keeps (`--update-snapshots`, a grep), without its worker count."""
    kept, skip = [], False
    files = {a for a in extra or [] if a.startswith("tests/e2e/")}
    for item in extra or []:
        if item in files:
            continue
        if skip:
            skip = False
            continue
        if item == "--workers":
            skip = True
            continue
        if not item.startswith("--workers="):
            kept.append(item)
    return kept


def apply_rerun(cases: list[Case], again: list[Case], files: set[str]) -> None:
    """A failed test is flaky when its rerun passed; a test the first run skipped with no reason (behind a failure
    in a serial spec) counts as its rerun ran it. Tests of other files are left as they were."""
    outcome = {c.id: c for c in again}
    for case in cases:
        if case.file not in files:
            continue
        rerun = outcome.get(case.id)
        if case.status == FAILED:
            case.rerun = rerun.status if rerun else FAILED
        elif case.status == SKIPPED and not case.message and rerun is not None:
            case.status, case.message = rerun.status, rerun.message
            if rerun.status == FAILED:
                case.rerun = FAILED


def _is_setup(case: Case) -> bool:
    return case.file.startswith("setup/")


def _copy_pw(ctx: Ctx, lane: str, tag: str, pw_dir: Path | None = None) -> None:
    """The run's Playwright output (traces of failures, report) into the report folder."""
    dest = ctx.out / "lanes" / lane / "pw"
    for item in (f"results-{tag}", f"report-{tag}", f"results-{tag}.json", f"junit-{tag}.xml"):
        src = (pw_dir or ctx.pw_dir) / item
        if src.is_dir():
            shutil.copytree(src, dest / item, dirs_exist_ok=True)
        elif src.is_file():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / item)


# ---- lanes ---------------------------------------------------------------------------------------------------


PY_NOT_SLOW = ["-m", "not docker and not slow"]
E2E_ROWS_DIR = IT_DIR / "harness" / "e2e"
FAULT_DIR = IT_DIR / "harness" / "fault"

# What each lane runs under --sample: one small, fast part of each of its suites (chosen to need nothing a full run
# does not), so the sample exercises every command, stack and reader the lane has.
SAMPLE = {
    "python unit": ["tests/unit/harness/assembly/test_answer_contract_skip.py"],
    "python cohesion": ["tests/cohesion/test_r25ui_human_stack_public_url.py"],
    "node unit": ["tests/modules/agent-harness/cardfail-idempotent-answer.http.test.ts"],
    "python contracts": ["tests/contracts/test_journal_folds.py"],
    "harness replay": ["tests/harness_replay/test_dur3_turn_limit.py"],
    "python integration": ["tests/integration/test_agent_turn_limit_e2e.py"],
    "vitest": ["app/components/harness-jobs"],
    "node mongo and redis itests": ["tests/modules/agent-harness/secret-key-rotation.mongo.itest.ts",
                                    "tests/modules/agent-harness/secret-key-rotation-kv.redis.itest.ts"],
    "node postgresql harness stores": ["tests/modules/agent-harness/conversation-erasure.pg.itest.ts"],
    "node postgresql stores": ["tests/stores/pg-conversation.itest.ts"],
    "node docstore-migrate itests": ["tests/scripts/docstore-migrate/coverage.itest.ts"],
    "ux-shots": ["--grep", "UX setup|UX chat|UX the agent builder|UX settings|UX cleanup"],
    "latency": ["--grep", "QA-LAT-1"],
    "harness project": ["tests/e2e/harness/mcpnote-notice.spec.ts"],
    "authenticated project": ["tests/e2e/chat/chat-job-reports.spec.ts"],
    "mcphub": ["test_mcpuse_sources.py", "-k", "test_l1"],
    "matrix": ["test_pairwise.py", "-k", "ST-1-PW-012"],
    "docker pytest": ["tests/unit/agent_loop_lib/sandbox/test_docker_live.py"],
    "engine spec": ["tests/e2e/harness/answer-contract-engine.spec.ts", "--grep", "AC-E6"],
    "ops spec": ["tests/e2e/harness/mcpnote-notice.spec.ts"],
    "fault": ["test_crash_points.py", "-k", "redis-drain_mid_tool"],
}


def sampled(ctx: Ctx, key: str, full: list[str]) -> list[str]:
    return SAMPLE[key] if ctx.sample else full


def sample_skip(name: str, why: str) -> Step:
    return Step(name, status=SKIPPED, note=f"sample run: {why}")


def lane_regressions(ctx: Ctx, lane: Lane) -> None:
    """The reported cases (QA-FINAL): each case's regression tests that need no stack, from the matrix's data, so
    a tree that brings one back fails in minutes. The stack-level ones run in their own lanes (e2e, mcphub, docker)."""
    import qa_matrix  # noqa: PLC0415 - scripts/ is on the path when qa_full runs

    tests = qa_matrix.regression_tests(qa_matrix.load())
    lane.steps.append(pytest_step(ctx, lane.id, "reported cases: python", ["-n", "4", *PY_NOT_SLOW, *tests["python"]]))
    lane.steps.append(mocha_step(ctx, lane.id, "reported cases: node", node_files(tests["node"])))
    lane.steps.append(vitest_step(ctx, lane.id, "reported cases: vitest", tests["vitest"]))
    if tests["other"]:
        lane.steps.append(Step("reported cases: other", status="error",
                               note=f"no runner for {tests['other']} (a test outside backend/python, backend/nodejs/apps, frontend)"))


def lane_unit(ctx: Ctx, lane: Lane) -> None:
    lane.steps.append(pytest_step(ctx, lane.id, "python unit", ["-n", "4", *PY_NOT_SLOW, *sampled(
        ctx, "python unit", ["tests/unit", "--ignore=tests/unit/containers"])]))
    lane.steps.append(pytest_step(ctx, lane.id, "python cohesion", ["-n", "4", *PY_NOT_SLOW,
                                                                    *sampled(ctx, "python cohesion", ["tests/cohesion"])]))
    if ctx.sample:
        lane.steps.append(mocha_step(ctx, lane.id, "node unit", node_files(SAMPLE["node unit"])))
        lane.steps.append(sample_skip("node coverage floor (domain, application)", "the floor reads the whole suite's coverage"))
    else:
        lane.steps.append(mocha_step(ctx, lane.id, "node unit", NODE_UNIT))
        lane.steps.append(plain_step(ctx, lane.id, "node coverage floor (domain, application)",
                                     ["npm", "run", "--silent", "coverage:logic"], cwd=NODE_DIR))
    lane.steps.append(plain_step(ctx, lane.id, "node typecheck", ["npx", "tsc", "--noEmit", "-p", "."], cwd=NODE_DIR,
                                 heavy=True))


def lane_contracts(ctx: Ctx, lane: Lane) -> None:
    lane.steps.append(pytest_step(ctx, lane.id, "python contracts", ["-n", "4", *PY_NOT_SLOW,
                                                                     *sampled(ctx, "python contracts", ["tests/contracts"])]))


def lane_replay(ctx: Ctx, lane: Lane) -> None:
    lane.steps.append(pytest_step(ctx, lane.id, "harness replay", ["-n", "4", *PY_NOT_SLOW,
                                                                   *sampled(ctx, "harness replay", ["tests/harness_replay"])]))


def lane_integration(ctx: Ctx, lane: Lane) -> None:
    lane.steps.append(pytest_step(ctx, lane.id, "python integration (no infra)",
                                  ["-n", "4", *PY_NOT_SLOW, *sampled(ctx, "python integration", ["tests/integration"]),
                                   "--timeout=300"]))


def lane_frontend(ctx: Ctx, lane: Lane) -> None:
    lane.steps.append(vitest_step(ctx, lane.id, files=SAMPLE["vitest"] if ctx.sample else None))
    lane.steps.append(plain_step(ctx, lane.id, "typecheck", ["npm", "run", "--silent", "typecheck"], cwd=FE_DIR, heavy=True))
    lane.steps.append(plain_step(ctx, lane.id, "lint", ["npx", "next", "lint", "--quiet"], cwd=FE_DIR, heavy=True))
    lane.steps.append(plain_step(ctx, lane.id, "locale parity and keys",
                                 ["bash", "-c", "node scripts/check-i18n-parity.mjs && node scripts/check-i18n-keys.mjs"],
                                 cwd=FE_DIR))


def it_stores(ctx: Ctx) -> tuple[str, str] | str:
    """The replica set and Redis of the store itests and the perf rig, from the variables the itests read; what to set
    when either is missing."""
    missing = [k for k in ("MONGO_IT_URI", "REDIS_IT_URL") if not ctx.env.get(k)]
    if missing:
        return f"set {' and '.join(missing)} to a MongoDB replica set and a Redis this host reaches"
    return ctx.env["MONGO_IT_URI"], ctx.env["REDIS_IT_URL"]


def with_database(uri: str, db: str, **query: str) -> str:
    """``uri`` naming database ``db``: each run writes a database of its own, so a neighbour's data is never read."""
    parts = urllib.parse.urlsplit(uri)
    params = dict(urllib.parse.parse_qsl(parts.query))
    params.update(query)
    return urllib.parse.urlunsplit(parts._replace(path=f"/{db}", query=urllib.parse.urlencode(params)))


def lane_stores(ctx: Ctx, lane: Lane) -> None:
    stores = it_stores(ctx)
    if isinstance(stores, str):
        lane.steps.append(Step("node mongo and redis itests", status=SKIPPED, note=stores))
    else:
        db = f"pxhqa_{ctx.e2e_run}"
        uri = with_database(stores[0], db)
        itest = (npm_script_files("test:itest", SAMPLE["node mongo and redis itests"]) if ctx.sample
                 else npm_mocha("test:itest"))
        lane.steps.append(mocha_step(ctx, lane.id, "node mongo and redis itests", itest,
                                     env={"MONGO_IT_URI": uri, "REDIS_IT_URL": stores[1]}))
        if container := ctx.env.get("QA_MONGO_CONTAINER"):
            subprocess.run(["docker", "exec", container, "mongosh", "--quiet", uri, "--eval", "db.dropDatabase()"],
                           capture_output=True, check=False, timeout=120)
    pg = pg_env(ctx)
    if pg is None:
        lane.steps.append(Step("node postgresql itests", status=SKIPPED,
                               note="no PostgreSQL 17 and PgBouncer under $QA_PG_ROOT"))
        return
    # the move's itests: the script names its files through a second script, which a sample run takes instead
    for name, script, sample in (("node postgresql harness stores", "test:pg-harness", "test:pg-harness"),
                                 ("node postgresql stores", "test:pg-stores", "test:pg-stores"),
                                 ("node docstore-migrate itests", "test:docstore-migrate", "test:docstore-migrate-files")):
        argv = npm_script_files(sample, SAMPLE[name]) if ctx.sample else npm_mocha(script)
        lane.steps.append(mocha_step(ctx, lane.id, name, argv, env=pg))


def pg_env(ctx: Ctx) -> dict[str, str] | None:
    root = Path(ctx.env.get("QA_PG_ROOT", "/nonexistent"))
    bins, bouncer = root / "usr/lib/postgresql/17/bin", root / "sbin/pgbouncer"
    migrate = Path(ctx.env.get("QA_PG_MIGRATE_VENV", "/nonexistent")) / "bin" / "python"
    if not (bins.is_dir() and bouncer.is_file() and migrate.is_file()):
        return None
    # A socket path over 107 bytes stops PostgreSQL: the work folder stays short.
    work = Path(ctx.env.get("TMPDIR", "/tmp")) / "pg"
    work.mkdir(parents=True, exist_ok=True)
    libs = ":".join(str(p) for p in (root / "lib", root / "usr/lib/x86_64-linux-gnu", root / "lib/x86_64-linux-gnu"))
    return {"PG_TEST_BINS": str(bins), "PG_TEST_PGBOUNCER": str(bouncer), "PG_TEST_MIGRATE_PYTHON": str(migrate),
            "PG_TEST_WORK": str(work), "LD_LIBRARY_PATH": libs}


def lane_ux(ctx: Ctx, lane: Lane) -> None:
    if ensure_stack(ctx, lane):
        lane.steps.append(ux_shots(ctx, lane.id, "ux screenshots", "ux-shots", ctx.e2e_run))
    # QAFINAL-FU-UX: an engine job's card and panel need engine runs, which only run.sh durable-up's stack has; its
    # captures go to the same baselines and gallery.
    # No indexing: the captures read no records, and the stack is up beside the shared one.
    lane.steps.extend(own_stack(ctx, lane.id, "ux screenshots, engine jobs", "durable-up", suffix="u",
                                env={"E2E_INDEXING": "0"}, specs=lambda run: ux_shots(ctx, lane.id, "ux screenshots, engine jobs",
                                                           "ux-shots-engine", run)))


def ux_shots(ctx: Ctx, lane: str, name: str, project: str, run: str) -> Step:
    """One UX capture project on a stack: the baselines copied in, the captures compared (or taken as the new
    baselines), and the gallery, axe findings and diffs copied into the report."""
    pw_dir = ctx.state_dir / run / "pw"
    baselines = ux_baselines_dir(ctx)
    inside = pw_dir / "ux-baselines"
    if inside.exists():
        shutil.rmtree(inside)
    if baselines.is_dir():
        shutil.copytree(baselines, inside)
    else:
        inside.mkdir(parents=True)
    extra = (["--update-snapshots"] if ctx.update_ux else []) + (SAMPLE.get(project, []) if ctx.sample else [])
    # Baselines are taken from one pass on a fresh stack: a rerun would capture the state the first pass left.
    step = pw_step(ctx, lane, name, project, skips_allowed=False, extra=extra, rerun=not ctx.update_ux, run=run)
    if ctx.update_ux:
        baselines.mkdir(parents=True, exist_ok=True)
        for png in inside.glob("*.png"):
            shutil.copy2(png, baselines / png.name)
        step.note = f"baselines updated in {baselines}"
    shots = pw_dir / "qa" / "ux"
    if shots.is_dir():
        shutil.copytree(shots, ctx.out / "ux", dirs_exist_ok=True)
    for diff in (pw_dir / f"results-qa-{slug(name)}").glob("**/*-diff.png"):
        (ctx.out / "ux" / "diffs").mkdir(parents=True, exist_ok=True)
        shutil.copy2(diff, ctx.out / "ux" / "diffs" / diff.name)
    return step


def ux_baselines_dir(ctx: Ctx) -> Path:
    """Out of git (each Playwright image renders fonts its own way, and 200 PNGs would bloat the history): one folder
    per capture set and Playwright version, named in ux-captures.json, so a version bump starts new baselines instead
    of failing, and the classic set never overwrites another branch's captures of the same name."""
    manifest = json.loads(UX_MANIFEST.read_text(encoding="utf-8")) if UX_MANIFEST.is_file() else {}
    folder = f"{manifest.get('set', 'ux')}-pw-{manifest.get('playwright', 'unknown')}"
    return Path(ctx.env.get("PXH_UX_BASELINES_DIR") or HOME / ".cache" / "pxh-qa" / "ux-baselines" / folder)


def lane_latency(ctx: Ctx, lane: Lane) -> None:
    if not ensure_stack(ctx, lane):
        return
    # One worker: two measuring tests at once would each time the other's load.
    lane.steps.append(pw_step(ctx, lane.id, "browser latency", "qa-latency", skips_allowed=False,
                              extra=["--workers", "1", *(SAMPLE["latency"] if ctx.sample else [])]))
    copy_latency(ctx.pw_dir / "qa", ctx.out / "lanes" / lane.id / "qa")
    # An upgrade in place (run.sh rebuild: the same tree built again, the app recreated on its data) times the
    # downtime; a stack this run did not bring up is someone else's to restart.
    if ctx.sample or ctx.reuse_stack:
        lane.steps.append(sample_skip("upgrade in place (run.sh rebuild)", "the rebuild takes minutes") if ctx.sample
                          else Step("upgrade in place (run.sh rebuild)", status=SKIPPED, note="--reuse-stack: not this run's stack"))
    else:
        step = Step("upgrade in place (run.sh rebuild)")
        log = ctx.out / "lanes" / lane.id / "rebuild.log"
        step.exit_code, step.seconds = run_cmd(ctx, ["bash", str(RUN_SH), "rebuild", ctx.e2e_run], cwd=ROOT, log=log,
                                               env=e2e_env(ctx, E2E_INDEXING="1"), heavy=True, timeout=3600)
        step.log = rel(ctx, log)
        step.status = PASSED if step.exit_code == 0 else FAILED
        lane.steps.append(step)
    record_stack_timings(ctx.work, ctx.out / "lanes" / "stack")


# R30-LAT: the stack's own timings (run.sh writes them under its work folder), each a budgeted metric with
# `source: run.sh` in latency-budgets.json: an install's time to ready and an upgrade's downtime.
STACK_TIMINGS = {"stack_ready_ms": "up-seconds", "upgrade_ready_ms": "upgrade-seconds"}


def record_stack_timings(work: Path, dest: Path) -> dict[str, list[float]]:
    """The stack's timings that exist, in ms, written where `read_latency_samples` finds them."""
    samples: dict[str, list[float]] = {}
    for metric, name in STACK_TIMINGS.items():
        try:
            samples[metric] = [float((work / name).read_text(encoding="utf-8").strip()) * 1000]
        except (OSError, ValueError):
            continue
    if samples:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "latency-stack.json").write_text(json.dumps({"samples": samples}, indent=1) + "\n", encoding="utf-8")
    return samples


def copy_latency(src: Path, dest: Path) -> None:
    """The latency specs' samples, from the stack's run folder into the report's (the report reads only its own)."""
    for sample in src.glob("latency-*.json"):
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sample, dest / sample.name)


def lane_e2e(ctx: Ctx, lane: Lane) -> None:
    if not ensure_stack(ctx, lane):
        return
    workers = ctx.env.get("E2E_PW_WORKERS", "2")
    lane.steps.append(pw_step(ctx, lane.id, "harness (harness project)", "harness", skips_allowed=False,
                              extra=["--workers", "1", *(SAMPLE["harness project"] if ctx.sample else [])]))
    lane.steps.append(pw_step(ctx, lane.id, "classic interface (authenticated project)", "authenticated",
                              skips_allowed=True, extra=["--workers", workers,
                                                         *(SAMPLE["authenticated project"] if ctx.sample else [])]))
    lane.steps.append(pw_step(ctx, lane.id, "journeys on indexed records (qa-indexed project)", "qa-indexed",
                              skips_allowed=False, extra=["--workers", "1"]))
    # EV-15/PF-17: a 10,000-step run's chat and job panel, served by the spec itself (request counts, render
    # budgets); it needs no stack, only the lane's browser image.
    lane.steps.append(pw_step(ctx, lane.id, "large runs in the browser (perf project)", "perf", skips_allowed=False,
                              extra=["--workers", "1"]))


def lane_docker(ctx: Ctx, lane: Lane) -> None:
    lane.steps.append(pytest_step(ctx, lane.id, "sandbox and engines in Docker",
                                  ["-n", "2", "-m", "docker and not slow", *sampled(
                                      ctx, "docker pytest", ["tests/unit", "tests/cohesion", "--ignore=tests/unit/containers"])],
                                  env={"SANDBOX_TEST_IMAGE": ctx.env.get("E2E_SANDBOX_IMAGE", "pxh-sandbox:pr1"),
                                       "PXH_REQUIRE_DOCKER": "1"}, timeout=10800))
    # A real engine run on the durable lane's own stack (opencode in a broker sandbox): one stack at a time.
    down = stack_down(ctx)
    if down:
        lane.steps.append(down)
    if ctx.sample:
        lane.steps.extend(own_stack_sample(ctx, lane.id, "real engine run in the browser (durable-up, one spec)",
                                           "durable-up", "harness-engine", SAMPLE["engine spec"], suffix="d"))
        return
    lane.steps.append(whole_stack_step(ctx, lane.id, "real engine runs in the browser (run.sh durable)", "durable",
                                       suffix="d", timeout=5400))
    lane.steps.extend(other_engine_jobs(ctx, lane.id))


# QAFINAL-FU-ENG: what a code job looks like and how long it takes to start, on every other release engine.
ENGINE_JOB_SPECS = ["tests/e2e/harness/classic-jobs-engine.spec.ts", "tests/e2e/qa/latency-engine.spec.ts"]


def _e2e_engines() -> Any:  # deployment/e2e/engines.py, loaded by its path
    spec = importlib.util.spec_from_file_location("e2e_engines", RUN_SH.parent / "engines.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def other_engine_jobs(ctx: Ctx, lane: str) -> list[Step]:
    """The engine job specs on each release engine but the default (whose whole lane ran above), each on its own
    durable stack (E2E_ENGINE), one at a time. An engine whose run image this host lacks is a skip that says so:
    Claude Code's image is built by the customer's admin from their own binary (D29). Their job-start samples reach
    the report from each stack's kept `qa` folder, under the one budget."""
    engines = _e2e_engines()
    steps: list[Step] = []
    for index, kind in enumerate(engines.kinds()[1:]):
        name = f"{kind}: engine jobs and job start in the browser"
        image = engines.image(kind)
        if subprocess.run(["docker", "image", "inspect", image], capture_output=True, check=False).returncode != 0:
            steps.append(Step(name, status=SKIPPED, note=f"its run image {image} is not on this host"))
            continue
        steps.extend(own_stack_sample(ctx, lane, name, "durable-up", "harness-engine", ENGINE_JOB_SPECS,
                                      suffix=f"e{index}", env={"E2E_ENGINE": kind}))
    return steps


def whole_stack_step(ctx: Ctx, lane: str, name: str, command: str, *, suffix: str, timeout: float,
                     env: dict[str, str] | None = None) -> Step:
    """One `run.sh <command>` that brings its own stack up, runs its specs and takes the stack down: its cases are
    every JUnit file the run kept (none for a command that only checks, which its exit code judges)."""
    run = f"{ctx.e2e_run}{suffix}"
    step = Step(name)
    log = ctx.out / "lanes" / lane / f"{slug(command)}.log"
    step.exit_code, step.seconds = run_cmd(ctx, ["bash", str(RUN_SH), command], cwd=ROOT, log=log,
                                           env={**e2e_env(ctx), "E2E_RUN": run, **(env or {})}, heavy=True,
                                           timeout=timeout)
    step.log = rel(ctx, log)
    kept = ctx.state_dir / f"kept-{run}"
    for junit in sorted((kept / "pw").glob("junit-*.xml")):
        step.cases += [c for c in read_junit(junit) if not c.file.startswith("setup")]
    for case in step.cases:
        if case.status == FAILED:
            # The stack is gone once its command ends; its failures are not rerun, so they count as real.
            case.rerun = FAILED
    keep_run_output(ctx, lane, kept, command)
    step.status = judge_step(step)
    return step


def keep_run_output(ctx: Ctx, lane: str, kept: Path, label: str) -> None:
    """A finished stack's Playwright output and logs into the report folder; the kept work folder then goes."""
    for item in kept.glob("pw/*"):
        if item.is_dir() and (item.name == "qa" or item.name.startswith(("results-", "report-"))):
            shutil.copytree(item, ctx.out / "lanes" / lane / label / item.name, dirs_exist_ok=True)
    if kept.is_dir():
        if (kept / "logs").is_dir():
            shutil.copytree(kept / "logs", ctx.out / "lanes" / lane / f"{label}-logs", dirs_exist_ok=True)
        shutil.rmtree(kept, ignore_errors=True)


def own_stack_sample(ctx: Ctx, lane: str, name: str, up: str, project: str, spec: list[str], *, suffix: str,
                     env: dict[str, str] | None = None) -> list[Step]:
    """--sample for a lane that brings its own stack: that stack up (`run.sh <up>`), one spec on it, the stack down."""
    return own_stack(ctx, lane, name, up, suffix=suffix, env=env, specs=lambda run: pw_step(
        ctx, lane, name, project, skips_allowed=False, extra=["--workers", "1", *spec], env=env, run=run))


def own_stack(ctx: Ctx, lane: str, name: str, up: str, *, suffix: str, specs: Callable[[str], Step],
              env: dict[str, str] | None = None) -> list[Step]:
    """A stack of the step's own (`run.sh <up>`, run id the run's plus `suffix`), `specs(run)` on it, then down."""
    run = f"{ctx.e2e_run}{suffix}"
    extra = {"E2E_RUN": run, **(env or {})}
    up_step = Step(f"{name}: stack up")
    log = ctx.out / "lanes" / lane / f"{suffix}-up.log"
    up_step.exit_code, up_step.seconds = run_cmd(ctx, ["bash", str(RUN_SH), up], cwd=ROOT, log=log,
                                                 env={**e2e_env(ctx), **extra}, heavy=True, timeout=3600)
    up_step.log = rel(ctx, log)
    up_step.status = PASSED if up_step.exit_code == 0 else "error"
    steps = [up_step]
    if up_step.exit_code == 0:
        steps.append(specs(run))
    down = Step(f"{name}: stack down")
    log = ctx.out / "lanes" / lane / f"{suffix}-down.log"
    down.exit_code, down.seconds = run_cmd(ctx, ["bash", str(RUN_SH), "down", run], cwd=ROOT, log=log,
                                           env={**e2e_env(ctx), **extra}, timeout=900)
    down.log = rel(ctx, log)
    down.status = PASSED if down.exit_code == 0 else "error"
    keep_run_output(ctx, lane, ctx.state_dir / f"kept-{run}", f"{suffix}-stack")
    return [*steps, down]


def rows_pytest(ctx: Ctx, lane: str, name: str, args: list[str], *, timeout: float) -> Step:
    """The options-matrix runner (integration-tests/harness/e2e, its own rootdir): each module or row brings its own
    stack up and down; one result line per row in the report folder."""
    results = ctx.out / "lanes" / lane / f"{slug(name)}-rows.txt"
    results.parent.mkdir(parents=True, exist_ok=True)
    return pytest_step(ctx, lane, name, [f"--results={results}", *args], cwd=E2E_ROWS_DIR, timeout=timeout,
                       env={"E2E_STATE_DIR": str(ctx.state_dir)})


def lane_mcphub(ctx: Ctx, lane: Lane) -> None:
    # Every module marked mcp_hub, each on its own hub-on stack; the slow and measurement modules stay opt-in.
    lane.steps.append(rows_pytest(ctx, lane.id, "MCP-E2E modules (hub on)", ["--mcp-hub", *sampled(
        ctx, "mcphub", ["-m", "mcp_hub and not slow and not measure", "."])], timeout=4 * 3600))


def lane_matrix(ctx: Ctx, lane: Lane) -> None:
    lane.steps.append(rows_pytest(ctx, lane.id, "options matrix, ST-1 rows", ["--stacks=ST-1", *sampled(
        ctx, "matrix", ["test_pairwise.py", "test_threeway_core.py"])], timeout=3 * 3600))


def lane_ops(ctx: Ctx, lane: Lane) -> None:
    """The whole flow where an install differs: the PostgreSQL document store behind PgBouncer (with its rows,
    erasure and the org's deletion), networks with no route out, and SECRET_KEY rotated by the runbook on a stack."""
    if ctx.sample:
        lane.steps.extend(own_stack_sample(ctx, lane.id, "PostgreSQL document store (up, one spec)", "up", "harness",
                                           SAMPLE["ops spec"], suffix="p", env={"E2E_DOC_STORE": "postgres"}))
        lane.steps.extend(own_stack_sample(ctx, lane.id, "no route out (up, one spec)", "up", "harness",
                                           SAMPLE["ops spec"], suffix="a", env={"E2E_AIRGAP": "1"}))
    else:
        lane.steps.append(whole_stack_step(ctx, lane.id, "PostgreSQL document store (run.sh pg)", "pg", suffix="p",
                                           timeout=7200))
        lane.steps.append(whole_stack_step(ctx, lane.id, "no route out (run.sh airgap)", "airgap", suffix="a",
                                           timeout=7200))
    lane.steps.append(whole_stack_step(ctx, lane.id, "SECRET_KEY rotation on a stack (run.sh keyring)", "keyring",
                                       suffix="k", timeout=3600))


def lane_fault(ctx: Ctx, lane: Lane) -> None:
    artifacts = ctx.out / "lanes" / lane.id / "artifacts"
    # Ports of their own (a compose project per base), clear of the perf rig's 37600 range.
    lane.steps.append(pytest_step(ctx, lane.id, "crash points on real processes",
                                  ["--port-base=38400", f"--fault-artifacts={artifacts}",
                                   *(["--broker=redis", *SAMPLE["fault"][0:1], *SAMPLE["fault"][1:]] if ctx.sample
                                     else ["--broker=all"])], cwd=FAULT_DIR, timeout=3 * 3600))


# The CI rig's gated profiles that run on one host (perf-harness.yml runs `--profile rig`, every one): the MCP fan-out
# (SLO #12), the model gateway, sandbox start (SLO #17, the sandbox image) and the engines' turn latency.
RIG_PROFILES = ("tool-gateway", "modes", "approval", "cancel", "reconnect", "mcp-5-servers", "model-gateway", "sandbox",
                "engine-latency")


def lane_perf(ctx: Ctx, lane: Lane) -> None:
    stores = it_stores(ctx)
    container = ctx.env.get("QA_MONGO_CONTAINER")
    if isinstance(stores, str) or not container:
        note = stores if isinstance(stores, str) else "set QA_MONGO_CONTAINER to the replica set's container"
        lane.steps.append(Step("perf rig", status=SKIPPED, note=note))
        return
    mongo_uri, redis_url = stores
    redis_port = urllib.parse.urlsplit(redis_url).port or 6379
    redis_db = free_redis_db(redis_url)
    if redis_db is None:
        lane.steps.append(Step("perf rig", status="error", note="no empty Redis DB (1-7) at $REDIS_IT_URL"))
        return
    for profile in RIG_PROFILES[:1] if ctx.sample else (*RIG_PROFILES, "soak"):
        step = Step(f"rig {profile}")
        folder = ctx.out / "lanes" / lane.id / profile
        db = f"pxhqa_rig_{slug(profile).replace('-', '_')}_{secrets.token_hex(3)}"
        argv = [ctx.python, "perf/bench_harness.py", "--profile", profile, "--label", f"qa-{socket.gethostname()}",
                "--samples", str(5 if ctx.sample else ctx.rig_samples), "--mongo-uri",
                with_database(mongo_uri, db, directConnection="true"),
                "--mongo-container", container, "--redis-port", str(redis_port), "--redis-db", str(redis_db),
                "--port-base", "37600",
                "--output", str(folder / "result.json"), "--summary", str(folder / "summary.md"),
                "--rig-out", str(folder / "rig")]
        if profile == "soak":
            argv += ["--soak-minutes", str(ctx.soak_minutes)]
        step.exit_code, step.seconds = run_cmd(ctx, argv, cwd=IT_DIR, log=folder / "run.log", heavy=True,
                                               timeout=3600 + ctx.soak_minutes * 60,
                                               env={"SANDBOX_TEST_IMAGE": ctx.env.get("E2E_SANDBOX_IMAGE", "pxh-sandbox:pr1")})
        step.log = rel(ctx, folder / "run.log")
        step.cases = verdict_cases(read_verdicts(folder / "result.json"))
        step.status = judge_step(step)
        lane.steps.append(step)


def verdict_cases(verdicts: list[dict[str, Any]]) -> list[Case]:
    """The rig's verdicts as cases. `missing`, or any verdict with no value, is a rig that measured nothing (a crash
    or a profile whose samples all fell through), never a pass; `rerun` with a value is a host above its core count,
    which says nothing either way (slo_gates.py), so it is a skip with that reason."""
    cases = []
    for v in verdicts:
        status = {"pass": PASSED, "waived": PASSED, "fail": FAILED, "missing": FAILED}.get(v["status"], SKIPPED)
        if v.get("value") is None:
            status = FAILED
        cases.append(Case(id=f"{v['slo']}:{v['profile']}:{v['metric']}.{v['stat']}",
                          name=f"{v['slo']} {v['metric']} {v['stat']}", status=status,
                          message=f"{v['value']} vs gate {v['threshold']} ({v['status']}) {v.get('detail') or ''}".strip(),
                          rerun=FAILED if status == FAILED else ""))
    return cases


def read_verdicts(path: Path) -> list[dict[str, Any]]:
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get("verdicts") or [])
    except (OSError, ValueError):
        return []


def free_redis_db(url: str) -> int | None:
    """An empty DB (7 down to 1) of the Redis at ``url``, asked over its own protocol: the rig clears only that DB."""
    parts = urllib.parse.urlsplit(url)

    def command(*words: str) -> bytes:
        return f"*{len(words)}\r\n".encode() + b"".join(
            f"${len(w.encode())}\r\n".encode() + w.encode() + b"\r\n" for w in words)

    try:
        with socket.create_connection((parts.hostname or "127.0.0.1", parts.port or 6379), timeout=10) as conn:
            reader = conn.makefile("rb")

            def ask(*words: str) -> str:
                conn.sendall(command(*words))
                return reader.readline().decode(errors="replace").strip()

            if parts.password is not None:
                user = [urllib.parse.unquote(parts.username)] if parts.username else []
                if not ask("AUTH", *user, urllib.parse.unquote(parts.password)).startswith("+"):
                    return None
            for db in range(7, 0, -1):
                if ask("SELECT", str(db)).startswith("+") and ask("DBSIZE") == ":0":
                    return db
    except OSError:
        return None
    return None


def lane_human_stack(ctx: Ctx, lane: Lane) -> None:
    # Where scripts/human-test-stack.sh keeps the stack (its HT1_HOME and run name).
    home = Path(os.environ.get("HT1_HOME", HOME / ".cache" / "pxh-ht1"))
    run_dir = home / "state" / "ht1"
    src = home / "src"
    if not (run_dir / "seed.json").is_file():
        lane.steps.append(Step("human-test stack smoke", status=SKIPPED,
                               note="no human-test stack is up (scripts/human-test-stack.sh up)"))
        return
    # The smoke of the commit the stack runs, not this tree's: it reads that stack's seed.
    script = src / "deployment" / "e2e" / "smoke_human.py"
    if not script.is_file():
        script = ROOT / "deployment" / "e2e" / "smoke_human.py"
    step = Step("human-test stack smoke (scripted model, no live call)")
    log = ctx.out / "lanes" / lane.id / "smoke.log"
    step.exit_code, step.seconds = run_cmd(ctx, [ctx.python, str(script), str(run_dir)], cwd=ROOT, log=log, timeout=1800)
    step.log = rel(ctx, log)
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s+(ok  |FAIL|skip) (.+?)(?:: (.*))?$", line)
        if m:
            status = {"ok  ": PASSED, "FAIL": FAILED, "skip": SKIPPED}[m.group(1)]
            step.cases.append(Case(id=m.group(2), name=m.group(2), status=status, message=m.group(3) or "",
                                   rerun=FAILED if status == FAILED else ""))
    step.status = judge_step(step)
    lane.steps.append(step)


LANES: list[tuple[str, str, Callable[[Ctx, Lane], None]]] = [
    ("regressions", "The reported cases: their regression tests (QA-FINAL)", lane_regressions),
    ("unit", "Unit and cohesion (Python, Node)", lane_unit),
    ("contracts", "Contracts", lane_contracts),
    ("replay", "Replay", lane_replay),
    ("integration", "Integration (in-process)", lane_integration),
    ("frontend", "Frontend unit, types, lint, locales", lane_frontend),
    ("stores", "MongoDB, Redis and PostgreSQL itests", lane_stores),
    ("ux", "UX screenshots (classic interface)", lane_ux),
    ("latency", "Browser latency (scripted model)", lane_latency),
    ("e2e", "E2E browser (harness, classic interface, journeys)", lane_e2e),
    ("mcphub", "MCP-E2E modules on hub-on stacks", lane_mcphub),
    ("matrix", "Options matrix, ST-1 rows", lane_matrix),
    ("docker", "Docker: sandbox, real engines", lane_docker),
    ("ops", "Installs: PostgreSQL document store, no route out, key rotation", lane_ops),
    ("fault", "Crash points (fault suite)", lane_fault),
    ("perf", "Perf rig: PF-05, latency SLOs, soak (short)", lane_perf),
    ("human-stack", "Human-test stack smoke", lane_human_stack),
]
# Lanes only the final pass runs by default (each brings its own stacks up; together some five hours).
FINAL_ONLY = ("mcphub", "matrix", "ops", "fault")


# ---- report ------------------------------------------------------------------------------------------------


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=False).stdout.strip()


def build_report(ctx: Ctx, lanes: list[Lane], started: float, args: argparse.Namespace) -> dict[str, Any]:
    totals = {"tests": 0, PASSED: 0, FAILED: 0, SKIPPED: 0, FLAKY: 0, REAL: 0}
    for lane in lanes:
        for step in lane.steps:
            for key, value in step.counts().items():
                totals[key] = totals.get(key, 0) + value
    budgets = json.loads(LATENCY_BUDGETS.read_text(encoding="utf-8")) if LATENCY_BUDGETS.is_file() else {"metrics": {}}
    samples = read_latency_samples(ctx.out)
    perf = []
    for result in sorted((ctx.out / "lanes" / "perf").glob("*/result.json")):
        perf.extend(read_verdicts(result))
    captures = ux_captures(ctx)
    return {
        "report": "qa-full",
        "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(timespec="seconds"),
        "wall_s": round(time.time() - started),
        "sha": git("rev-parse", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "host": socket.gethostname(),
        "options": {"lanes": args.lanes, "update_ux_baselines": ctx.update_ux, "soak_minutes": ctx.soak_minutes,
                    "rig_samples": ctx.rig_samples, "e2e_run": ctx.e2e_run, "sample": ctx.sample},
        "exit": exit_status(lanes),
        "totals": totals,
        "lanes": [lane_json(lane) for lane in lanes],
        "latency": {"rows": judge_latency(samples, budgets), "samples": samples},
        "perf": perf,
        "ux": captures,
    }


def lane_json(lane: Lane) -> dict[str, Any]:
    """A lane for report.json: every test counted, only the ones that did not simply pass listed."""
    out = asdict(lane) | {"counts": _lane_counts(lane)}
    for step in out["steps"]:
        listed = [c for c in step["cases"] if c["status"] != PASSED or c["rerun"]]
        step["passed_unlisted"] += len(step["cases"]) - len(listed)
        step["cases"] = listed
    return out


def _lane_counts(lane: Lane) -> dict[str, int]:
    counts: dict[str, int] = {}
    for step in lane.steps:
        for key, value in step.counts().items():
            counts[key] = counts.get(key, 0) + value
    return counts


def ux_captures(ctx: Ctx) -> list[dict[str, Any]]:
    folder = ctx.out / "ux"
    diffs = {p.name.replace("-diff.png", ""): p for p in (folder / "diffs").glob("*-diff.png")} if (folder / "diffs").is_dir() else {}
    shots = []
    for png in sorted(folder.glob("*.png")):
        name = png.stem
        diff = next((d for key, d in diffs.items() if key.endswith(name) or name in key), None)
        shots.append({"name": name, "path": rel(ctx, png), "diff": rel(ctx, diff) if diff else "",
                      "status": FAILED if diff else PASSED})
    return shots


STATUS_CLASS = {PASSED: "ok", FAILED: "bad", "error": "bad", FLAKY: "warn", SKIPPED: "skip", "missing": "warn",
                "pending": "skip", "pass": "ok", "fail": "bad", "waived": "warn", "rerun": "warn"}


def render_html(report: dict[str, Any]) -> str:
    e = html.escape

    def badge(status: str) -> str:
        return f'<span class="b {STATUS_CLASS.get(status, "skip")}">{e(status)}</span>'

    def ms(value: Any) -> str:
        return "—" if value is None else f"{float(value):,.0f}"

    t = report["totals"]
    verdict = {0: ("All lanes passed", "ok"), 1: ("Real failures", "bad"), 3: ("Only flaky failures", "warn")}[report["exit"]]
    out = [
        "<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>",
        f"<title>QA report {e(report['sha'][:10])}</title><style>{CSS}</style></head><body><main>",
        f"<h1>QA report</h1><p class=meta>{e(report['branch'])} at <code>{e(report['sha'][:12])}</code> on "
        f"{e(report['host'])}, started {e(report['started_at'])}, {report['wall_s'] // 60} min</p>",
        f'<p class="verdict {verdict[1]}">{e(verdict[0])}</p>',
        "<div class=tiles>",
        *(f"<div class=tile><b>{t.get(k, 0):,}</b><span>{label}</span></div>" for k, label in
          (("tests", "tests"), (PASSED, "passed"), (REAL, "real failures"), (FLAKY, "flaky"), (SKIPPED, "skipped"))),
        "</div><h2>Lanes</h2><table><thead><tr><th>Lane</th><th>Status</th><th class=n>Tests</th><th class=n>Passed</th>"
        "<th class=n>Failed</th><th class=n>Flaky</th><th class=n>Skipped</th><th class=n>Minutes</th></tr></thead><tbody>",
    ]
    for lane in report["lanes"]:
        c = lane["counts"]
        minutes = sum(s["seconds"] for s in lane["steps"]) / 60
        out.append(f"<tr><td><a href='#lane-{e(lane['id'])}'>{e(lane['title'])}</a></td><td>{badge(lane['status'])}</td>"
                   f"<td class=n>{c.get('tests', 0)}</td><td class=n>{c.get(PASSED, 0)}</td><td class=n>{c.get(REAL, 0)}</td>"
                   f"<td class=n>{c.get(FLAKY, 0)}</td><td class=n>{c.get(SKIPPED, 0)}</td><td class=n>{minutes:.1f}</td></tr>")
    out.append("</tbody></table>")

    rows = report["latency"]["rows"]
    if rows:
        out.append("<h2>Browser latency (scripted model)</h2><table><thead><tr><th>Measure</th><th class=n>Samples</th>"
                   "<th class=n>p50 ms</th><th class=n>Budget p50</th><th class=n>p95 ms</th><th class=n>Budget p95</th>"
                   "<th>Status</th></tr></thead><tbody>")
        for r in rows:
            out.append(f"<tr><td>{e(r['title'])}</td><td class=n>{r['samples']}</td><td class=n>{ms(r['p50_ms'])}</td>"
                       f"<td class=n>{ms(r['budget_p50_ms'])}</td><td class=n>{ms(r['p95_ms'])}</td>"
                       f"<td class=n>{ms(r['budget_p95_ms'])}</td><td>{badge(r['status'])}</td></tr>")
        out.append("</tbody></table>")
    if report["perf"]:
        out.append("<h2>Perf rig against SLO.md</h2><table><thead><tr><th>SLO</th><th>Profile</th><th>Metric</th>"
                   "<th class=n>Target</th><th class=n>Gate</th><th class=n>Measured</th><th>Status</th></tr></thead><tbody>")
        for v in report["perf"]:
            value = "—" if v.get("value") is None else f"{v['value']:g}"
            out.append(f"<tr><td>{e(str(v['slo']))}</td><td>{e(v['profile'])}</td><td>{e(v['metric'])} {e(v['stat'])}</td>"
                       f"<td class=n>{v['target']:g}</td><td class=n>{v['threshold']:g}</td><td class=n>{value}</td>"
                       f"<td>{badge(v['status'])} {e(v.get('detail') or '')}</td></tr>")
        out.append("</tbody></table>")
    if report["ux"]:
        bad = sum(1 for s in report["ux"] if s["status"] == FAILED)
        out.append(f"<h2>UX screenshots</h2><p>{len(report['ux'])} captures, {bad} differ from their baseline.</p><div class=gallery>")
        for shot in report["ux"]:
            diff = f"<a href='{e(shot['diff'])}'>diff</a>" if shot["diff"] else ""
            out.append(f"<figure class='{STATUS_CLASS[shot['status']]}'><a href='{e(shot['path'])}'>"
                       f"<img loading=lazy src='{e(shot['path'])}' alt='{e(shot['name'])}'></a>"
                       f"<figcaption>{e(shot['name'])} {diff}</figcaption></figure>")
        out.append("</div>")

    out.append("<h2>Steps</h2>")
    for lane in report["lanes"]:
        out.append(f"<section id='lane-{e(lane['id'])}'><h3>{e(lane['title'])} {badge(lane['status'])}</h3>")
        if lane["reason"]:
            out.append(f"<p class=note>{e(lane['reason'])}</p>")
        out.append("<table><thead><tr><th>Step</th><th>Status</th><th class=n>Tests</th><th class=n>Failed</th>"
                   "<th class=n>Flaky</th><th class=n>Seconds</th><th>Log</th></tr></thead><tbody>")
        for step in lane["steps"]:
            c = _counts_of(step)
            log = f"<a href='{e(step['log'])}'>log</a>" if step["log"] else ""
            note = f"<div class=note>{e(step['note'])}</div>" if step["note"] else ""
            out.append(f"<tr><td>{e(step['name'])}{note}</td><td>{badge(step['status'])}</td><td class=n>{c['tests']}</td>"
                       f"<td class=n>{c[REAL]}</td><td class=n>{c[FLAKY]}</td><td class=n>{step['seconds']:.0f}</td><td>{log}</td></tr>")
        out.append("</tbody></table>")
        for step in lane["steps"]:
            failed = [c for c in step["cases"] if c["status"] == FAILED]
            if failed:
                out.append(f"<details open><summary>{e(step['name'])}: {len(failed)} failed</summary><ul class=fails>")
                for case in failed:
                    label = FLAKY if case["rerun"] == PASSED else REAL
                    out.append(f"<li>{badge(label)} <code>{e(case['id'])}</code><pre>{e(case['message'][:1500])}</pre></li>")
                out.append("</ul></details>")
            if step["skips"]:
                out.append(f"<details><summary>{e(step['name'])}: {len(step['skips'])} skipped, with reasons</summary><ul>")
                out.extend(f"<li>{e(s['test'])}: {e(s['reason'])}</li>" for s in step["skips"])
                out.append("</ul></details>")
        out.append("</section>")
    out.append("<p class=meta>Generated by scripts/qa_full.py. The JSON is report.json beside this page.</p></main></body></html>")
    return "\n".join(out)


def _counts_of(step: dict[str, Any]) -> dict[str, int]:
    unlisted = step.get("passed_unlisted", 0)
    c = {"tests": len(step["cases"]) + unlisted, PASSED: unlisted, FAILED: 0, SKIPPED: 0, FLAKY: 0, REAL: 0}
    for case in step["cases"]:
        c[case["status"]] = c.get(case["status"], 0) + 1
        if case["status"] == FAILED:
            c[FLAKY if case["rerun"] == PASSED else REAL] += 1
    return c


CSS = """
:root{color-scheme:light dark;--fg:#1c2024;--muted:#60646c;--bg:#fff;--line:#e0e1e6;--ok:#18794e;--bad:#ce2c31;
--warn:#ab6400;--skip:#80838d;--chip:#f0f0f3}
@media (prefers-color-scheme:dark){:root{--fg:#edeef0;--muted:#b0b4ba;--bg:#111113;--line:#2e3135;--ok:#3dd68c;
--bad:#ff9592;--warn:#ffca16;--skip:#9ea2a9;--chip:#212225}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 20px 64px}h1{font-size:24px;margin:0 0 4px}
h2{font-size:18px;margin:32px 0 8px}h3{font-size:15px;margin:24px 0 6px}.meta{color:var(--muted)}
.verdict{font-weight:600;font-size:16px}.verdict.ok{color:var(--ok)}.verdict.bad{color:var(--bad)}.verdict.warn{color:var(--warn)}
.tiles{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}.tile{border:1px solid var(--line);border-radius:8px;padding:10px 16px;min-width:120px}
.tile b{display:block;font-size:22px;font-variant-numeric:tabular-nums}.tile span{color:var(--muted)}
table{border-collapse:collapse;width:100%;margin:4px 0 12px}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:500}.n{text-align:right;font-variant-numeric:tabular-nums}
.b{display:inline-block;border-radius:999px;padding:0 8px;font-size:12px;background:var(--chip)}
.b.ok{color:var(--ok)}.b.bad{color:var(--bad)}.b.warn{color:var(--warn)}.b.skip{color:var(--skip)}
.note{color:var(--muted);font-size:12px}pre{white-space:pre-wrap;font-size:12px;max-height:240px;overflow:auto;background:var(--chip);padding:8px;border-radius:6px}
.fails li{margin:6px 0}.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}
figure{margin:0;border:1px solid var(--line);border-radius:8px;overflow:hidden}figure.bad{border-color:var(--bad)}
figure img{width:100%;display:block;aspect-ratio:16/10;object-fit:cover;object-position:top}
figcaption{padding:4px 8px;font-size:12px;color:var(--muted)}a{color:inherit}
"""


# ---- main --------------------------------------------------------------------------------------------------


def base_env(python: str) -> dict[str, str]:
    env = dict(os.environ)
    node_bin = HOME / ".local" / "node" / "current" / "bin"
    env["PATH"] = os.pathsep.join([str(node_bin), str(Path(python).parent), env.get("PATH", "")])
    env.setdefault("TMPDIR", "/tmp/pxhqa")
    Path(env["TMPDIR"]).mkdir(parents=True, exist_ok=True)
    # The venv's own folder: resolving the interpreter's link would land on the system Python's.
    site = Path(python).absolute().parent.parent / "lib" / "python3.12" / "site-packages"
    env.setdefault("E2E_SITE_PACKAGES", str(site))
    env.setdefault("E2E_SANDBOX_IMAGE", "pxh-sandbox:pr1")
    # The PostgreSQL stack (ops lane, run.sh pg) runs the document store's migrations from this venv's packages.
    migrate = Path(env.get("QA_PG_MIGRATE_VENV", "/nonexistent")) / "lib" / "python3.12" / "site-packages"
    if migrate.is_dir():
        env.setdefault("E2E_PG_MIGRATE_SITE", str(migrate))
    # The lanes' stack runs its own MongoDB and Redis (run.sh's dev-harness profile, ST-1's stores otherwise): on the
    # shared replica set a neighbouring stack's journal flushes and index builds made every second latency sample
    # slow (QA-CLASSIC-FU-7), so a budget was judged on another stack's load.
    env.setdefault("E2E_STACK", "dev-harness")
    # Python suites call into the TypeScript side (C-5, parity tests); without Node on PATH they would skip.
    env.setdefault("PXH_REQUIRE_NODE", "1")
    env.pop("CI", None)
    return env



def default_python() -> str:
    """The checkout's venv (CONTRIBUTING), else the interpreter running this script."""
    for name in ("venv", ".venv"):
        candidate = ROOT / "backend" / "python" / name / "bin" / "python"
        if candidate.is_file():
            return str(candidate)
    return sys.executable

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, help="the report folder (index.html, report.json, each lane's logs)")
    parser.add_argument("--lanes", default="", help="comma-separated lane ids (default: every lane; with --final, every "
                        "lane including the ones that bring their own stacks)")
    parser.add_argument("--final", action="store_true",
                        help="QA-FINAL: every lane, then final.md and final.json, the matrix's rows marked by this run")
    parser.add_argument("--sample", action="store_true",
                        help="each lane on a small selection of its suites: proves the lanes work, judges no row")
    parser.add_argument("--skip", default="", help="comma-separated lane ids to leave out")
    parser.add_argument("--list", action="store_true", help="print the lanes and exit")
    parser.add_argument("--python", default=os.environ.get("PXH_PYTHON") or default_python())
    parser.add_argument("--update-ux-baselines", action="store_true")
    # Six minutes: the RSS growth reading needs ten 30 s samples after start-up (harness_profiles.growth).
    parser.add_argument("--soak-minutes", type=float, default=6.0, help="the soak's length (SLO #19 is 1440)")
    parser.add_argument("--rig-samples", type=int, default=40)
    parser.add_argument("--e2e-run", default="", help="the e2e stack's run id (default: a new one)")
    parser.add_argument("--reuse-stack", action="store_true", help="use the e2e stack --e2e-run names, left up")
    parser.add_argument("--keep-stack", action="store_true", help="leave the e2e stack up after its lanes")
    parser.add_argument("--no-lane", action="store_true", help="run heavy steps without the PXH_LANE wrapper")
    parser.add_argument("--rerender", action="store_true",
                        help="rebuild --out's report.json and index.html from what the folder holds, running nothing")
    args = parser.parse_args(argv)

    known = [name for name, _, _ in LANES]
    if not args.lanes:
        args.lanes = ",".join(n for n in known if args.final or n not in FINAL_ONLY)
    if args.list:
        for name, title, _ in LANES:
            if name in args.lanes.split(","):
                print(f"  {name:12} {title}")
        return 0
    if args.out is None:
        parser.error("--out is required")
    wanted = [x for x in args.lanes.split(",") if x and x not in args.skip.split(",")]
    unknown = [x for x in wanted if x not in known]
    if unknown:
        parser.error(f"unknown lanes: {unknown} (known: {known})")

    out = args.out.resolve()
    if args.rerender:
        return rerender(out)
    out.mkdir(parents=True, exist_ok=True)
    lane_bin = os.environ.get("PXH_LANE", "")
    ctx = Ctx(out=out, python=args.python, env=base_env(args.python),
              lane_cmd=[] if args.no_lane or not lane_bin or not Path(lane_bin).is_file() else [lane_bin],
              e2e_run=args.e2e_run or f"qa{secrets.token_hex(3)}",
              state_dir=Path(os.environ.get("E2E_STATE_DIR", HOME / ".cache" / "pxh-e2e")),
              update_ux=args.update_ux_baselines, soak_minutes=args.soak_minutes, rig_samples=args.rig_samples,
              keep_stack=args.keep_stack, reuse_stack=args.reuse_stack, sample=args.sample)
    started = time.time()
    lanes: list[Lane] = []
    stack_lanes = {"ux", "latency", "e2e"}
    try:
        for name, title, fn in LANES:
            if name not in wanted:
                continue
            lane = Lane(name, title)
            lanes.append(lane)
            ctx.log(f"qa_full: lane {name}: {title}")
            began = time.monotonic()
            try:
                fn(ctx, lane)
            except Exception as error:  # noqa: BLE001 - one lane's crash is reported, the others still run
                lane.steps.append(Step("lane crashed", status="error", note=f"{type(error).__name__}: {error}"))
            if not lane.steps and lane.reason:
                lane.steps.append(Step("not run", status="error", note=lane.reason))
            lane.seconds = time.monotonic() - began
            if not (stack_lanes & set(wanted[wanted.index(name) + 1:])):
                down = stack_down(ctx)
                if down:
                    lane.steps.append(down)
            lane.status = judge_lane(lane)
            ctx.log(f"qa_full: lane {name}: {lane.status} ({lane.seconds / 60:.1f} min)")
            write_report(ctx, lanes, started, args)
    finally:
        down = stack_down(ctx)
        if down and lanes:
            lanes[-1].steps.append(down)
        report = write_report(ctx, lanes, started, args)
    ctx.log(f"qa_full: report {out / 'index.html'}")
    if args.final:
        import qa_matrix  # noqa: PLC0415

        summary = qa_matrix.write_summary(qa_matrix.load(), report, out)
        ctx.log(f"qa_full: final summary {out / 'final.md'}: {summary['verdicts']}")
    t = report["totals"]
    ctx.log(f"qa_full: {t['tests']} tests, {t[PASSED]} passed, {t[REAL]} real failures, {t[FLAKY]} flaky, {t[SKIPPED]} skipped")
    return report["exit"]


def rerender(out: Path) -> int:
    """The report again from the folder: the perf steps judged from their result files by today's rules, the latency
    samples and UX captures as the folder now holds them, every lane and the totals recounted."""
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    lanes = []
    for raw in report["lanes"]:
        steps = [Step(**{**{k: v for k, v in st.items() if k != "cases"}, "cases": [Case(**c) for c in st["cases"]]})
                 for st in raw["steps"]]
        lane = Lane(raw["id"], raw["title"], seconds=raw["seconds"], reason=raw["reason"], steps=steps)
        for step in lane.steps:
            if lane.id == "perf" and step.name.startswith("rig "):
                step.cases = verdict_cases(read_verdicts(out / "lanes" / "perf" / step.name[4:] / "result.json"))
                step.status = judge_step(step)
        lane.status = judge_lane(lane)
        lanes.append(lane)
    ctx = Ctx(out=out, python="", env={}, lane_cmd=[], e2e_run=report["options"]["e2e_run"], state_dir=out,
              update_ux=report["options"]["update_ux_baselines"], soak_minutes=report["options"]["soak_minutes"],
              rig_samples=report["options"]["rig_samples"], keep_stack=False, reuse_stack=False,
              sample=bool(report["options"].get("sample")))
    fresh = build_report(ctx, lanes, datetime.fromisoformat(report["started_at"]).timestamp(),
                         argparse.Namespace(lanes=report["options"]["lanes"]))
    fresh.update({"sha": report["sha"], "branch": report["branch"], "host": report["host"], "wall_s": report["wall_s"]})
    (out / "report.json").write_text(json.dumps(fresh, indent=1) + "\n", encoding="utf-8")
    (out / "index.html").write_text(render_html(fresh), encoding="utf-8")
    print(f"qa_full: rerendered {out / 'index.html'}")
    return fresh["exit"]


def write_report(ctx: Ctx, lanes: list[Lane], started: float, args: argparse.Namespace) -> dict[str, Any]:
    report = build_report(ctx, lanes, started, args)
    (ctx.out / "report.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    (ctx.out / "index.html").write_text(render_html(report), encoding="utf-8")
    return report


if __name__ == "__main__":
    sys.exit(main())
