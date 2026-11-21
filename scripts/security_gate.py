#!/usr/bin/env python3
"""The release security gate (C6.42): the security suite and the canary scans, one definition for CI and a laptop.

    python3 scripts/security_gate.py list                     # the stages, what each runs and why it may not skip
    python3 scripts/security_gate.py run security canary      # some stages
    python3 scripts/security_gate.py run all                  # every stage; exit 1 on any finding

`harness-security-gate.yml` calls `run <stage>` in its jobs and `release-publish.yml` waits for that workflow, so a
release passes exactly what this file says. A stage fails on a failed case, on a skip it does not allow, on fewer
cases than it holds (an empty run is not a pass) and on a missing report (pytest died before writing one). A stage
that cannot run here fails too: a release gate that skipped a stage for want of Docker would read green, as C-13 once
did. The Docker stages need a daemon and the images the workflow builds first (the sandbox image, and the engine
images for `engines`); on the shared dev host run them through the lane wrapper.

Every QA14 canary scan (`test_qa14_*.py`) is found by name, so a new scan joins the gate without an edit here.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import TextIO

REPO = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
PYTHON_DIR = Path("backend/python")
C13 = "tests/cohesion/test_c13_sandbox_reaches_only_gateways.py"
GVISOR_LANE = tuple(
    f"tests/unit/harness/sandbox_broker/{name}.py"
    for name in ("test_sec_iso_escape_docker", "test_sec_iso_exhaustion_docker", "test_egress_security_docker")
) + (C13,)
TIMEOUT = ["--timeout-method=thread"]
# The run_code sandbox image the gate's jobs build with Compose (SANDBOX_DOCKER_IMAGE); every Docker stage's cases
# read it as SANDBOX_TEST_IMAGE, so no stage depends on a tag only a developer's machine has.
SANDBOX_IMAGE = "pipeshubai/pipeshub-sandbox:local"


@dataclass(frozen=True)
class Stage:
    name: str
    why: str
    #: pytest arguments, run in backend/python; empty for a plain command.
    pytest: tuple[str, ...] = ()
    #: a command run from the repository root instead of pytest (its exit code is the verdict).
    command: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    docker: bool = False
    #: substrings of a skip reason that may skip; None lets any skip through (PXH_REQUIRE_DOCKER already turns
    #: the lane's "no daemon or image" skips into failures).
    allow_skips: tuple[str, ...] | None = ()
    min_tests: int = 1
    #: after the report: the engine lane fails when a case skipped for want of an image the gate built.
    image_check: bool = False


def _qa14_scans(repo: Path) -> list[str]:
    root = repo / PYTHON_DIR
    return sorted(p.relative_to(root).as_posix() for p in (root / "tests").rglob("test_qa14_*.py"))


def stages(repo: Path = REPO) -> list[Stage]:
    """The gate, in the order `run all` takes it: offline stages first, so a finding there shows in seconds."""
    docker = {"PXH_REQUIRE_DOCKER": "1"}
    return [
        Stage(
            "supply-chain",
            "no locked or installed dependency is a denylisted release, and every install is from a hashed lock",
            command=(sys.executable, "-I", "scripts/supply_chain.py", "check"),
        ),
        Stage(
            "security",
            "the sandbox, egress, secret and red-team regressions that need no Docker",
            pytest=(
                "tests/unit/harness/security",
                "tests/unit/harness_core/security",
                "-m",
                "not docker and not slow and not integration",
                *(f"--deselect={scan}" for scan in _qa14_scans(repo)),
            ),
            # A matrix cell that does not exist: a plan-and-execute case has no durable route.
            allow_skips=("is not a durable run",),
            min_tests=100,
        ),
        Stage(
            "canary",
            "a canary planted in a run never reaches a log, a trace, the activity stream or a stored key name",
            pytest=tuple(_qa14_scans(repo)),
            # The activity scan reads its Redis here and skips without one, which the gate refuses.
            env={"REDIS_IT_URL": os.environ.get("REDIS_IT_URL", "redis://127.0.0.1:6379")},
            min_tests=10,
        ),
        Stage(
            "c13",
            "a sandbox reaches only the two gateways (C-13), on a real sandbox",
            # Its gVisor case runs in the gvisor stage, after the job installs runsc.
            pytest=(C13, "-k", "not gvisor", "--timeout=600", *TIMEOUT),
            env=docker,
            docker=True,
        ),
        Stage(
            "docker-lane",
            "every Docker-marked case: real sandboxes, egress proxy, broker",
            pytest=(
                "-n",
                "2",
                "-m",
                "docker and not slow",
                "tests/unit",
                "tests/cohesion",
                "--ignore=tests/unit/containers",
                f"--deselect={C13}",
                "--timeout=600",
                *TIMEOUT,
            ),
            env={**docker, "SANDBOX_TEST_IMAGE": SANDBOX_IMAGE, "SANDBOX_DOCKER_IMAGE": SANDBOX_IMAGE},
            docker=True,
            allow_skips=None,
            min_tests=50,
        ),
        Stage(
            "gvisor",
            "the escape, exhaustion and internal-reach probes on runsc (C6.05, C20.39), red on a daemon without it",
            pytest=(
                "-m",
                "docker and not slow",
                "-k",
                "gvisor",
                *GVISOR_LANE,
                "--timeout=600",
                *TIMEOUT,
            ),
            env={**docker, "PXH_REQUIRE_GVISOR": "1", "SANDBOX_TEST_IMAGE": SANDBOX_IMAGE},
            docker=True,
            # The quota guard's engine-image cases need images this job does not build (the engines stage's).
            allow_skips=("is not built",),
            min_tests=20,
        ),
        Stage(
            "engines",
            "each redistributable engine's real lane and tools-layer licence scan, on the images built for it",
            pytest=(
                "-n",
                "2",
                "-m",
                "docker and not slow",
                "tests/unit/harness/engines",
                "tests/unit/harness_engines",
                "tests/unit/harness/sandbox_broker/test_engine_tools_image_docker.py",
                "--timeout=900",
                *TIMEOUT,
            ),
            # The matrix lane's MCP runner image is built FROM the sandbox image.
            env={**docker, "SANDBOX_TEST_IMAGE": SANDBOX_IMAGE},
            docker=True,
            allow_skips=None,
            min_tests=20,
            image_check=True,
        ),
    ]


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


def _reports() -> ModuleType:
    return _load("assert_no_skips", REPO / "scripts" / "assert_no_skips.py")


def _images() -> ModuleType:
    return _load("release_images", REPO / "scripts" / "engines" / "release_images.py")


@dataclass
class Result:
    stage: str
    findings: list[str]
    #: cases the report held; None for a plain command.
    cases: int | None = None

    @property
    def ok(self) -> bool:
        return not self.findings


Runner = Callable[[Sequence[str], Path, Mapping[str, str]], int]


def _subprocess(argv: Sequence[str], cwd: Path, env: Mapping[str, str]) -> int:
    return subprocess.run(list(argv), cwd=cwd, env={**os.environ, **env}, check=False).returncode


def verdict(stage: Stage, code: int, report: Path | None) -> Result:
    """What keeps a stage from passing, read from its exit code and its JUnit report."""
    if not stage.pytest:
        return Result(stage.name, [] if code == 0 else [f"{' '.join(stage.command[1:])} exited {code}"])
    if report is None or not report.is_file():
        return Result(stage.name, [f"pytest wrote no report (exit {code})"], 0)
    reports = _reports()
    findings = list(reports.failed([str(report)]))
    total, skips = reports.skipped([str(report)])
    if stage.allow_skips is not None:
        reasons = _skip_reasons(report)
        findings += [f"SKIPPED {case}: {reasons.get(case, '')}" for case in skips
                     if not any(a in reasons.get(case, "") for a in stage.allow_skips)]
    if total < stage.min_tests:
        findings.append(f"only {total} cases ran; the stage holds at least {stage.min_tests}")
    if stage.image_check:
        images = _images()
        builds = images.plan(json.loads(images.ALLOWLIST.read_text(encoding="utf-8")))
        findings += [f"SKIPPED FOR WANT OF A BUILT IMAGE {line}" for line in images.hidden_cases(builds, [report])]
    if code not in (0, 1) and not findings:
        findings.append(f"pytest exited {code}")
    if code == 1 and not findings:
        findings.append("pytest failed outside a test case (collection or a fixture)")
    return Result(stage.name, findings, total)


def _skip_reasons(report: Path) -> dict[str, str]:
    out = {}
    for case in ET.parse(report).getroot().iter("testcase"):
        skipped = case.find("skipped")
        if skipped is not None:
            out[f"{case.get('classname', '')}::{case.get('name', '')}"] = " ".join(
                filter(None, (skipped.get("message"), skipped.text)))
    return out


def run_stage(stage: Stage, repo: Path = REPO, runner: Runner = _subprocess, workdir: Path | None = None) -> Result:
    if not stage.pytest:
        return verdict(stage, runner(stage.command, repo, stage.env), None)
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        report = Path(tmp) / f"{stage.name}.xml"
        argv = [sys.executable, "-m", "pytest", "-rs", f"--junitxml={report}", *stage.pytest]
        code = runner(argv, repo / PYTHON_DIR, stage.env)
        return verdict(stage, code, report)


def run(names: Sequence[str], repo: Path = REPO, runner: Runner = _subprocess, out: TextIO = sys.stdout) -> int:
    known = {s.name: s for s in stages(repo)}
    chosen = list(known) if list(names) == ["all"] else list(names)
    unknown = [n for n in chosen if n not in known]
    if unknown:
        print(f"unknown stage(s): {', '.join(unknown)}; known: {', '.join(known)}", file=out)
        return 2
    # Every stage runs, whatever the one before it found: a release needs the whole list of what blocks it.
    results = []
    for name in chosen:
        print(f"::group::security gate: {name}", file=out, flush=True)
        results.append(run_stage(known[name], repo, runner))
        print("::endgroup::", file=out, flush=True)
    for result in results:
        cases = "" if result.cases is None else f" ({result.cases} cases)"
        print(f"{'PASS' if result.ok else 'FAIL'} {result.stage}{cases}", file=out)
        for finding in result.findings:
            print(f"  {finding}", file=out)
    failed = [r.stage for r in results if not r.ok]
    print(f"security gate: {len(results) - len(failed)} of {len(results)} stages passed"
          + (f"; blocked by {', '.join(failed)}" if failed else ""), file=out)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    run_p = sub.add_parser("run")
    run_p.add_argument("stages", nargs="+", help="stage names, or `all`")
    args = ap.parse_args(argv)
    if args.cmd == "list":
        for s in stages():
            what = " ".join(s.pytest) if s.pytest else " ".join(s.command[1:])
            print(f"{s.name}{' (Docker)' if s.docker else ''}: {s.why}\n    {what}")
        return 0
    return run(args.stages)


if __name__ == "__main__":
    sys.exit(main())
