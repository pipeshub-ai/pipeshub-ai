#!/usr/bin/env python3
"""Select the tests a diff can affect, and optionally run them once per commit.

    scripts/affected_tests.py                     # print the selection for merge-base(origin/main)..HEAD
    scripts/affected_tests.py A..B                # for another range
    scripts/affected_tests.py --json              # the selection as JSON
    scripts/affected_tests.py --run [--out DIR]   # run it, results under DIR (default: the temp folder's
                                                  # affected-tests/<sha>/, reused when present)

AFFECTED_TESTS_BASE names another base branch. PXH_LANE, when set, is a wrapper every suite's command runs
through (for example one that caps its CPU and memory); unset, the commands run directly.

Changed paths map to components through the source and test globs of docs/test-plan/data/components.json, then
one hop out to the components that depend on them, then to their test files (the same globs and test-file walk as
scripts/harness_test_inventory.py). The cohesion suites always run. The selection widens to every suite (fails
safe) when a change touches a shared contract, codegen output, shared test support or a file no component claims.
A change to engines, sandboxes, skills, run assembly or the run definition also selects the Docker lane (the
Docker-marked tests, which the python lane deselects); --run runs it when SANDBOX_TEST_IMAGE names the sandbox
image and is red without it.
A vector under contracts/**/vectors selects the tests and test support that name it; it fails safe when no reader is
found or a reader is shared test support. Documentation alone selects only the cohesion set; a top-level script selects the
scripts' own tests.

Results are keyed by the commit sha, so a resumed work package reads them instead of rerunning; --run refuses a
worktree with uncommitted changes to tracked files, since those results would not belong to the sha.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from functools import lru_cache
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness_test_inventory import COMPONENTS, ROOT, glob_to_regex, walk  # noqa: E402

BASE = os.environ.get("AFFECTED_TESTS_BASE", "origin/main")
LANE = os.environ.get("PXH_LANE", "")


def venv_python(root: Path = ROOT, env: Mapping[str, str] | None = None) -> Path:
    """The interpreter the Python suite runs with: ``PXH_VENV_PYTHON``, else the checkout's venv (CONTRIBUTING), else
    the one running this script, so a worktree without a venv of its own still runs the suite."""
    env = os.environ if env is None else env
    if env.get("PXH_VENV_PYTHON"):
        return Path(env["PXH_VENV_PYTHON"])
    venv = root / "backend/python/venv/bin/python"
    return venv if venv.exists() else Path(sys.executable)


VENV_PYTHON = venv_python()

PY_ROOT = "backend/python/"
NODE_ROOT = "backend/nodejs/apps/"
FRONTEND_ROOT = "frontend/"

FAIL_SAFE = (
    "contracts/**",
    "**/vectors/**",
    "**/contracts/generated/**",
    "**/*.generated.ts",
    "frontend/lib/contracts/**",
    "backend/python/tests/support/**",
    "backend/python/tests/conftest.py",
    # The shared test kit, except the port contract suites: each of those belongs to its port's component.
    "backend/python/harness_core/testing/*.py",
    "backend/python/harness_core/testing/engines/**",
    "backend/python/harness_core/testing/replay/**",
    "backend/python/harness_core/testing/sandbox/**",
    "backend/nodejs/apps/tests/helpers/**",
    "backend/nodejs/apps/.mocharc*",
    "backend/python/pytest.ini",
    # Composition roots, package roots and cross-cutting error and schema modules: every component imports them.
    "backend/nodejs/apps/src/modules/agent-harness/composition.ts",
    "backend/nodejs/apps/src/modules/agent-harness/binders/**",
    "backend/nodejs/apps/src/modules/agent-harness/config.ts",
    "backend/nodejs/apps/src/modules/agent-harness/domain/errors.ts",
    "backend/python/app/harness/__init__.py",
    "backend/python/harness_core/__init__.py",
    "backend/python/tests/unit/harness/conftest.py",
    "backend/python/tests/unit/harness_core/__init__.py",
    "backend/python/tests/unit/harness_core/conftest.py",
)
# A shared vector selects the tests that read it (found by name in test and test-support code), so the selection is
# its consumer set; codegen outputs generated from a vector stay fail-safe above.
VECTORS = ("contracts/**/vectors/**",)
CONSUMER_ROOTS = ("backend/python/tests", "backend/nodejs/apps/tests", "backend/python/harness_core/testing",
                  "frontend/app", "frontend/lib", "frontend/tests", "integration-tests/harness", "scripts")
# Fixtures and helpers of a suite that reads them as a whole: a change runs that suite.
SUITE_SUPPORT = {
    "backend/python/tests/contracts/": ("python", "tests/contracts"),
    "backend/nodejs/apps/tests/contracts/": ("node", "tests/contracts/**/*.test.ts"),
    "backend/python/tests/cohesion/": ("python", "tests/cohesion"),
    "backend/nodejs/apps/tests/cohesion/": ("node", "tests/cohesion/**/*.test.ts"),
}
# Documentation, CI and lint configuration, and the served API docs: no test outcome here depends on them, and the
# cohesion set (always run) checks the workflows and the OpenAPI routes.
DOCS_ONLY = ("docs/**", "*.md", "**/README.md", "**/CHANGELOG.md", "changelog/**", ".github/**", "**/eslint.config.mjs",
             "**/.eslintrc*", "frontend/playwright*.config.ts", "backend/nodejs/apps/src/modules/api-docs/*.yaml")

COHESION = {
    "python": ["tests/cohesion"],
    "node": ["tests/cohesion/**/*.test.ts"],
}
EVERYTHING = {
    "python": ["tests/unit", "tests/contracts", "tests/harness_replay", "tests/evals", "tests/cohesion"],
    "node": ["tests/**/*.test.ts"],
    "frontend": ["**/*.test.{ts,tsx}"],
    "scripts": ["scripts/test_*.py"],
}
PY_IGNORE = ["tests/unit/containers"]
# Mocha appends command-line specs to the config's `spec` (every test), so a subset runs with the repo's
# config minus `spec`.
MOCHARC_WITHOUT_SPEC = """const fs = require("fs");
const yaml = require(require.resolve("js-yaml", { paths: [process.cwd()] }));
const rc = yaml.load(fs.readFileSync(process.cwd() + "/.mocharc.yaml", "utf8"));
delete rc.spec;
module.exports = rc;
"""
UNCLAIMED_AREAS = (
    "backend/nodejs/apps/src/modules/agent-harness/", "backend/nodejs/apps/src/modules/control-plane/",
    "backend/nodejs/apps/tests/modules/agent-harness/", "backend/nodejs/apps/tests/modules/control-plane/",
    "backend/python/harness_core/", "backend/python/harness_engines/", "backend/python/app/harness/",
    "backend/python/app/control_plane/", "backend/python/tests/unit/harness", "backend/python/tests/unit/harness_core",
    "backend/python/tests/unit/harness_engines", "backend/python/tests/cohesion", "backend/python/tests/contracts",
    "backend/python/tests/harness_replay", "backend/nodejs/apps/tests/contracts", "backend/nodejs/apps/tests/cohesion",
    "integration-tests/harness", "backend/nodejs/apps/src/modules/enterprise_search/adapters/harness-*",
)
PY_MARKERS = "not docker and not slow"
# The python lane deselects Docker-marked tests, so a change to what those tests exercise (engines, sandboxes,
# skills, run assembly and the run definition's pins) also runs them as their own lane: a sandbox is where an
# engine run's pinned skills and tools are actually used.
DOCKER_AREAS = (
    "backend/python/harness_core/engines/**", "backend/python/harness_engines/**",
    "backend/python/app/harness/engines/**", "backend/python/harness_core/sandbox/**",
    "backend/python/app/harness/sandbox/**", "backend/python/harness_core/skills/**",
    "backend/python/app/harness/skills/**", "backend/python/app/agents/agent_loop/skills/**",
    "backend/python/app/harness/assembly/**", "backend/python/app/agents/run_definition.py",
    "backend/python/tests/unit/harness/engines/**",
)
DOCKER_MARKERS = "docker and not slow"
DOCKER_TARGETS = ["tests/unit", "tests/cohesion"]
# The sandbox image the Docker-marked tests start their containers from.
DOCKER_IMAGE_ENV = "SANDBOX_TEST_IMAGE"


@dataclass
class Selection:
    everything: bool = False
    reasons: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    python: list[str] = field(default_factory=list)
    node: list[str] = field(default_factory=list)
    frontend: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    needs_stack: list[str] = field(default_factory=list)
    # Changed paths that make the Docker lane part of this selection.
    docker: list[str] = field(default_factory=list)

    def suites(self) -> dict[str, list[str]]:
        return {k: v for k, v in (("python", self.python), ("node", self.node),
                                  ("frontend", self.frontend), ("scripts", self.scripts)) if v}


def _matcher(globs: Iterable[str]) -> Callable[[str], bool]:
    regexes = [glob_to_regex(g) for g in globs]
    return lambda path: any(rx.match(path) for rx in regexes)


def bucket(test_path: str) -> str:
    """Which runner a test file belongs to; `needs_stack` for suites that need services or a browser."""
    name = test_path.rsplit("/", 1)[-1]
    if test_path.startswith(PY_ROOT + "tests/") and name.endswith(".py"):
        return "python"
    if test_path.startswith(NODE_ROOT + "tests/") and name.endswith(".test.ts"):
        return "node"
    if test_path.startswith(FRONTEND_ROOT) and not test_path.startswith(FRONTEND_ROOT + "tests/e2e/") \
            and name.endswith((".test.ts", ".test.tsx")):
        return "frontend"
    if test_path.startswith("scripts/") and name.startswith("test_") and name.endswith(".py"):
        return "scripts"
    if name.endswith("-contract.ts"):
        return "support"
    return "needs_stack"


def _is_test_code(path: str) -> bool:
    if path.startswith("frontend/"):
        return ".test." in path or "/__tests__/" in path or path.startswith("frontend/tests/")
    if path.startswith("scripts/"):
        return path.rsplit("/", 1)[-1].startswith("test_")
    return path.endswith((".py", ".ts", ".tsx", ".mjs", ".js"))


def _vector_terms(path: str) -> list[str]:
    """What a reader of the vector names: its file name, or for a file in a vector directory, that directory."""
    rest = path.split("/vectors/", 1)[1]
    return [rest.split("/", 1)[0]] if "/" in rest else [rest]


@lru_cache(maxsize=1)
def _test_code(root: Path = ROOT) -> dict[str, str]:
    out = {}
    for rel in git("ls-files", *CONSUMER_ROOTS, cwd=root).split():
        if _is_test_code(rel):
            try:
                out[rel] = (root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return out


def vector_consumers(path: str) -> list[str]:
    """Test and test-support files that name the vector; a directory name must appear beside `vectors`."""
    terms = _vector_terms(path)
    in_dir = "/" in path.split("/vectors/", 1)[1]
    return sorted(rel for rel, text in _test_code().items()
                  if all(t in text for t in terms) and (not in_dir or "vectors" in text))


def select(changed: Sequence[str], components: list[dict], test_files: Sequence[str],
           consumers_of: Callable[[str], list[str]] = vector_consumers) -> Selection:
    sel = Selection()
    docker_area = _matcher(DOCKER_AREAS)
    sel.docker = sorted(path for path in changed if docker_area(path))
    fail_safe = _matcher(FAIL_SAFE)
    vector = _matcher(VECTORS)
    docs_only = _matcher(DOCS_ONLY)
    owned = {c["id"]: (_matcher(c["source"]), _matcher(c["tests"])) for c in components}
    hit: set[str] = set()
    direct_tests: set[str] = set()
    suite_runs: set[tuple[str, str]] = set()
    for path in changed:
        if vector(path):
            reasons = _select_vector_readers(path, consumers_of(path), fail_safe, owned, hit, direct_tests, suite_runs)
            sel.reasons += reasons
            continue
        if fail_safe(path):
            sel.reasons.append(f"shared: {path}")
            continue
        owners = [cid for cid, (src, tst) in owned.items() if src(path) or tst(path)]
        if owners:
            hit.update(owners)
            if bucket(path) in ("python", "node", "frontend", "scripts"):
                direct_tests.add(path)
        elif path.startswith("scripts/") and path.count("/") == 1 and path.endswith(".py"):
            direct_tests.update(t for t in test_files if bucket(t) == "scripts")
        elif run := _suite_support(path):
            suite_runs.add(run)
        elif docs_only(path):
            continue
        else:
            sel.reasons.append(f"unclaimed: {path}")
    if sel.reasons:
        sel.everything = True
        sel.python = list(EVERYTHING["python"])
        sel.node = list(EVERYTHING["node"])
        sel.frontend = list(EVERYTHING["frontend"])
        sel.scripts = list(EVERYTHING["scripts"])
        sel.components = sorted(c["id"] for c in components)
        return sel
    dependents = {c["id"] for c in components if hit & set(c.get("depends", []))} - hit
    sel.components = sorted(hit)
    sel.dependents = sorted(dependents)
    chosen = hit | dependents
    claims = _matcher(g for c in components if c["id"] in chosen for g in c["tests"])
    picked = {t for t in test_files if claims(t)} | direct_tests
    out: dict[str, set[str]] = {"python": set(), "node": set(), "frontend": set(), "scripts": set(), "needs_stack": set()}
    for t in picked:
        b = bucket(t)
        if b != "support":
            out[b].add(t)
    sel.python = sorted(COHESION["python"]) + sorted(p[len(PY_ROOT):] for p in out["python"]
                                                      if not p.startswith(PY_ROOT + "tests/cohesion/"))
    sel.node = sorted(COHESION["node"]) + sorted(p[len(NODE_ROOT):] for p in out["node"]
                                                  if not p.startswith(NODE_ROOT + "tests/cohesion/"))
    for suite, target in sorted(suite_runs):
        listed = sel.python if suite == "python" else sel.node
        if target not in listed:
            listed.insert(0, target)
    sel.frontend = sorted(p[len(FRONTEND_ROOT):] for p in out["frontend"])
    sel.scripts = sorted(out["scripts"])
    sel.needs_stack = sorted(out["needs_stack"])
    return sel


def _select_vector_readers(path: str, readers: list[str], fail_safe: Callable[[str], bool],
                           owned: dict[str, tuple[Callable[[str], bool], Callable[[str], bool]]],
                           hit: set[str], direct_tests: set[str], suite_runs: set[tuple[str, str]]) -> list[str]:
    """Adds a vector's readers to the selection; returns the reasons to select everything instead."""
    if not readers:
        return [f"vector with no reader found: {path}"]
    reasons = []
    for reader in readers:
        if fail_safe(reader):
            reasons.append(f"vector read by shared test support: {path} ({reader})")
        elif _looks_like_test(reader):
            direct_tests.add(reader)
        elif run := _suite_support(reader):
            suite_runs.add(run)
        else:
            owners = [cid for cid, (src, tst) in owned.items() if src(reader) or tst(reader)]
            if not owners:
                reasons.append(f"vector read by unclaimed test support: {path} ({reader})")
            hit.update(owners)
    return reasons


def _looks_like_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith(("test_", "integration_test_")) or name.endswith(
        (".test.ts", ".test.tsx", ".itest.ts", ".spec.ts"))


def _suite_support(path: str) -> tuple[str, str] | None:
    return next((run for prefix, run in SUITE_SUPPORT.items() if path.startswith(prefix)), None)


def unclaimed(paths: Sequence[str], components: list[dict]) -> list[str]:
    """Paths no component claims and that are not fail-safe or documentation: each widens a selection to everything."""
    claimed = _matcher(g for c in components for g in (*c["source"], *c["tests"]))
    fail_safe, docs_only = _matcher(FAIL_SAFE), _matcher(DOCS_ONLY)
    return [p for p in paths if not (claimed(p) or fail_safe(p) or docs_only(p) or _suite_support(p))]


def unclaimed_harness_files(cwd: Path = ROOT) -> list[str]:
    components = json.loads((cwd / COMPONENTS.relative_to(ROOT)).read_text())["components"]
    return unclaimed(git("ls-files", *UNCLAIMED_AREAS, cwd=cwd).split(), components)


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def default_range(cwd: Path = ROOT, base: str = BASE) -> str:
    return f"{git('merge-base', base, 'HEAD', cwd=cwd).strip()}..HEAD"


def changed_paths(rng: str, cwd: Path = ROOT) -> list[str]:
    return sorted(set(git("diff", "--name-only", "--no-renames", rng, cwd=cwd).split()))


def render_text(sel: Selection, rng: str) -> str:
    lines = [f"# range {rng}"]
    if sel.everything:
        lines.append("# everything (fail-safe): " + "; ".join(sel.reasons[:10]) + (" ..." if len(sel.reasons) > 10 else ""))
    else:
        lines.append(f"# components: {' '.join(sel.components) or '(none)'}; dependents: {' '.join(sel.dependents) or '(none)'}")
    for suite, items in sel.suites().items():
        lines += [f"{suite} {i}" for i in items]
    lines += [f"needs-stack {p}" for p in sel.needs_stack]
    if sel.docker:
        lines.append(f"docker {' '.join(DOCKER_TARGETS)}  # -m '{DOCKER_MARKERS}'; because of {sel.docker[0]}"
                     + (f" and {len(sel.docker) - 1} more" if len(sel.docker) > 1 else ""))
    return "\n".join(lines)


# --- run mode -----------------------------------------------------------------------------------------------

@dataclass
class LaneResult:
    suite: str
    command: list[str]
    exit_code: int
    tests: int = 0
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    seconds: float = 0.0
    junit: str = ""
    log: str = ""
    note: str = ""


def junit_counts(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return {}
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if root.tag == "testsuites" and root.get("tests") is not None:
        suites = [root]
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for s in suites:
        for k in totals:
            totals[k] += int(s.get(k) or s.get("skip" if k == "skipped" else k) or 0)
    return totals


def _present(items: list[str], base: Path) -> list[str]:
    return [i for i in items if "*" in i or (base / i).exists()]


def commands(sel: Selection, out: Path, root: Path = ROOT, python: Path = VENV_PYTHON,
             docker_image: str | None = None) -> list[tuple[str, Path, list[str], dict[str, str]]]:
    """(suite, cwd, argv, extra env) per suite; each argv is run through the lane wrapper."""
    cmds = []
    if sel.python:
        argv = [str(python), "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", PY_MARKERS,
                f"--junitxml={out / 'python.xml'}"]
        if sel.everything or len(sel.python) > 8:
            argv += ["-n", "4"]
        # onnxruntime 1.30 (imported via qdrant_client -> fastembed) runs a recursive std::regex over
        # /proc/self/cmdline at import and overflows the 8 MiB stack past ~32 KiB of argv (exit 139), so the
        # selection goes in a pytest @argfile and the command line stays short however many paths are selected.
        out.mkdir(parents=True, exist_ok=True)
        args_file = out / "python.args"
        args_file.write_text("\n".join([f"--ignore={p}" for p in PY_IGNORE] + _present(sel.python, root / PY_ROOT)) + "\n")
        cmds.append(("python", root / PY_ROOT, [*argv, f"@{args_file}"], {}))
    if sel.node:
        argv = ["npx", "mocha", "--reporter", "xunit", "--reporter-option", f"output={out / 'node.xml'}"]
        if not sel.everything:
            out.mkdir(parents=True, exist_ok=True)
            (out / "mocharc.cjs").write_text(MOCHARC_WITHOUT_SPEC)
            argv += ["--config", str(out / "mocharc.cjs"), *_present(sel.node, root / NODE_ROOT)]
        cmds.append(("node", root / NODE_ROOT, argv, {"TS_NODE_PROJECT": "tsconfig.test.json"}))
        # Mocha runs the tests without type-checking them: the count of type errors in the test tree may not rise.
        cmds.append(("nodetypes", root / NODE_ROOT, ["node", "tests/typecheck-ratchet.js"], {}))
    if sel.frontend:
        argv = ["npx", "vitest", "run", "--reporter=junit", f"--outputFile={out / 'frontend.xml'}"]
        argv += [] if sel.everything else _present(sel.frontend, root / FRONTEND_ROOT)
        cmds.append(("frontend", root / FRONTEND_ROOT, argv, {}))
    if sel.scripts:
        files = sorted(str(p.relative_to(root)) for p in (root / "scripts").glob("test_*.py")) if sel.everything \
            else _present(sel.scripts, root)
        if files:
            argv = [str(python), "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={out / 'scripts.xml'}", *files]
            cmds.append(("scripts", root, argv, {}))
    if sel.docker and docker_image:
        argv = [str(python), "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "2", "-m", DOCKER_MARKERS,
                f"--junitxml={out / 'docker.xml'}", *[f"--ignore={p}" for p in PY_IGNORE], *DOCKER_TARGETS]
        cmds.append(("docker", root / PY_ROOT, argv, {DOCKER_IMAGE_ENV: docker_image}))
    return cmds


# A host may keep one installed node_modules per package and link it into each worktree; a missing link is
# then made here instead of failing the run (the link is gitignored).
NODE_MODULES_SRC = Path(os.environ.get("PXH_NODE_MODULES_SRC", "/nonexistent"))
_LINK_NAME = {"node": "apps", "nodetypes": "apps", "frontend": "frontend"}


def _missing_prereq(suite: str, cwd: Path) -> str:
    if suite in _LINK_NAME and not (cwd / "node_modules").exists():
        src = NODE_MODULES_SRC / _LINK_NAME[suite]
        if src.exists():
            (cwd / "node_modules").symlink_to(src.resolve())
            return ""
        return f"{cwd}/node_modules missing (run npm ci there, or set PXH_NODE_MODULES_SRC)"
    return ""


def subprocess_runner(argv: list[str], cwd: Path, env: dict[str, str], log: Path) -> int:
    with log.open("w") as fh:
        return subprocess.run([LANE, *argv] if LANE else argv, cwd=cwd, env={**os.environ, **env},
                              stdout=fh, stderr=subprocess.STDOUT).returncode


def run(sel: Selection, out: Path, meta: dict, runner: Callable[[list[str], Path, dict[str, str], Path], int] = subprocess_runner,
        force: bool = False, root: Path = ROOT) -> dict:
    summary_path = out / "summary.json"
    if summary_path.exists() and not force:
        cached = json.loads(summary_path.read_text())
        cached["reused"] = True
        return cached
    out.mkdir(parents=True, exist_ok=True)
    results = []
    docker_image = os.environ.get(DOCKER_IMAGE_ENV)
    for suite, cwd, argv, env in commands(sel, out, root, docker_image=docker_image):
        log = out / f"{suite}.log"
        missing = _missing_prereq(suite, cwd)
        if missing:
            results.append(LaneResult(suite, argv, 127, note=missing, log=str(log)))
            continue
        started = time.monotonic()
        code = runner(argv, cwd, env, log)
        junit = out / f"{suite}.xml"
        counts = junit_counts(junit)
        results.append(LaneResult(suite, argv, code, tests=counts.get("tests", 0), failures=counts.get("failures", 0),
                                  errors=counts.get("errors", 0), skipped=counts.get("skipped", 0),
                                  seconds=round(time.monotonic() - started, 1), junit=str(junit), log=str(log)))
    if sel.docker and not docker_image:
        # Not run is not green: these tests are the only ones that use what the change touched in a sandbox.
        results.append(LaneResult("docker", [], 2, note=f"not run: set {DOCKER_IMAGE_ENV} (the change touches "
                                                          f"{sel.docker[0]}; the python lane deselects Docker-marked tests)"))
    summary = {**meta, "selection": asdict(sel), "lanes": [asdict(r) for r in results],
               "ok": all(r.exit_code == 0 for r in results)}
    summary_path.write_text(json.dumps(summary, indent=1) + "\n")
    (out / "summary.txt").write_text(render_summary(summary) + "\n")
    return summary


def render_summary(summary: dict) -> str:
    lines = [f"sha {summary.get('sha', '?')} range {summary.get('range', '?')}: {'GREEN' if summary['ok'] else 'RED'}"
             + (" (reused)" if summary.get("reused") else "")]
    for r in summary["lanes"]:
        counts = f"{r['tests']} tests, {r['failures']} failures, {r['errors']} errors, {r['skipped']} skipped"
        lines.append(f"  {r['suite']:<9} exit {r['exit_code']}  {counts}  {r['note'] or r['log']}")
    if summary["selection"].get("needs_stack"):
        lines.append(f"  not run here (need a running stack): {len(summary['selection']['needs_stack'])} files, see --json")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("range", nargs="?", help=f"git diff range (default merge-base({BASE}, HEAD)..HEAD)")
    ap.add_argument("--json", action="store_true", help="print the selection as JSON")
    ap.add_argument("--run", action="store_true", help="run the selection (through PXH_LANE when set)")
    ap.add_argument("--out", type=Path, help="results directory (default: the temp folder's affected-tests/<sha>)")
    ap.add_argument("--force", action="store_true", help="rerun even when a summary exists for this sha")
    ap.add_argument("--unclaimed", action="store_true",
                    help="list tracked harness files no component claims (each one selects everything); "
                         "exit 1 if there are any")
    args = ap.parse_args(argv)
    components = json.loads(COMPONENTS.read_text())["components"]
    if args.unclaimed:
        files = unclaimed_harness_files()
        if files:
            print("\n".join(files))
        return 1 if files else 0
    rng = args.range or default_range()
    sel = select(changed_paths(rng), components, walk())
    if not args.run:
        print(json.dumps(asdict(sel), indent=1) if args.json else render_text(sel, rng))
        return 0
    if git("status", "--porcelain", "--untracked-files=no").strip():
        print("uncommitted changes to tracked files: commit (a WIP commit is fine) so results key to a sha", file=sys.stderr)
        return 2
    sha = git("rev-parse", "HEAD").strip()
    out = args.out or Path(tempfile.gettempdir()) / "affected-tests" / sha
    summary = run(sel, out, {"sha": sha, "range": rng}, force=args.force)
    print(render_summary(summary))
    print(f"results: {out}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
