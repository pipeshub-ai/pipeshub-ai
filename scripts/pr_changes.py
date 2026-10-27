#!/usr/bin/env python3
"""Say which CI checks a pull request's changed files can affect.

Each workflow asks this script (through .github/actions/pr-changes) and runs
only the jobs whose code changed: a Node-only change skips the Python unit
shards and the Python backend-matrix suites, and a Markdown-only change skips
the browser smoke test.

Every event other than a pull request (a push to main, the schedule, a manual
run, a workflow_call from another workflow) runs everything, so whatever a
pull request skipped is still checked on main. A change to this script or its
action also runs everything, so a mistake in the rules shows up at once.

Run: python3 scripts/pr_changes.py --files changed.txt   (one path per line)
     python3 scripts/pr_changes.py --all
Test: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Iterable

# The pull request files API lists at most this many; a longer list is cut off.
FILES_API_LIMIT = 3000

PYTHON = "backend/python"
INTEGRATION = f"{PYTHON}/tests/integration"

# A change here runs every check.
EVERYTHING = (
    "scripts/pr_changes.py",
    ".github/actions/pr-changes/**",
)

# Files nothing runs or builds from. A pull request made only of these runs
# none of the checks below.
DOCS = (
    "**/*.md",
    "**/*.mdx",
    "docs/**",
    "changelog/**",
    "LICENSE*",
    ".github/dependabot.yml",
    ".github/ISSUE_TEMPLATE/**",
    ".github/PULL_REQUEST_TEMPLATE*",
    ".github/CODEOWNERS",
)

NODE_APP = (
    "backend/nodejs/apps/src/**",
    "backend/nodejs/apps/tests/**",
    "backend/nodejs/apps/package.json",
    "backend/nodejs/apps/package-lock.json",
    "backend/nodejs/apps/tsconfig*.json",
    "backend/nodejs/apps/.mocharc.yaml",
    "backend/nodejs/apps/.c8rc.json",
)

PYTHON_APP = (
    f"{PYTHON}/app/**",
    f"{PYTHON}/tests/**",
    f"{PYTHON}/pyproject.toml",
    f"{PYTHON}/pytest.ini",
    f"{PYTHON}/.coveragerc",
)

PYTHON_PACKAGING = (f"{PYTHON}/pyproject.toml", f"{PYTHON}/pytest.ini")
COMPOSE = "deployment/docker-compose"

# Checks decided by path alone.
CHECKS: dict[str, tuple[str, ...]] = {
    # unit-test.yml
    "node_unit": NODE_APP + (".github/workflows/unit-test.yml",),
    "python_unit": PYTHON_APP
    + (
        "scripts/unit_shards.py",
        "scripts/unit_shard_durations.json",
        ".github/workflows/unit-test.yml",
    ),
    # main.yml
    "frontend": ("frontend/**", ".github/workflows/main.yml"),
    "node_build": ("backend/nodejs/**", ".github/workflows/main.yml"),
    # codeql.yml, one per language it analyses
    "codeql_python": ("**/*.py", "**/pyproject.toml", ".github/workflows/codeql.yml"),
    "codeql_javascript": (
        "**/*.js",
        "**/*.jsx",
        "**/*.mjs",
        "**/*.cjs",
        "**/*.ts",
        "**/*.tsx",
        "**/package.json",
        "**/package-lock.json",
        ".github/workflows/codeql.yml",
    ),
    "codeql_actions": (".github/workflows/**", ".github/actions/**"),
}

# The Node code the storage job starts (tests/support/storage-it-server.ts and
# what it requires) and the packages it installs.
STORAGE_NODE = (
    "backend/nodejs/apps/src/modules/storage/**",
    "backend/nodejs/apps/src/libs/middlewares/**",
    "backend/nodejs/apps/src/libs/utils/**",
    "backend/nodejs/apps/src/libs/keyValueStore/**",
    "backend/nodejs/apps/src/libs/services/authtoken.service.ts",
    "backend/nodejs/apps/src/libs/services/keyValueStore.service.ts",
    "backend/nodejs/apps/src/libs/services/logger.service.ts",
    "backend/nodejs/apps/src/libs/services/mongo.service.ts",
    "backend/nodejs/apps/tests/support/**",
    "backend/nodejs/apps/package.json",
    "backend/nodejs/apps/package-lock.json",
    "backend/nodejs/apps/tsconfig.json",
    "backend/nodejs/apps/tsconfig.test.json",
)


# backend-matrix.yml's Python suites. Their tests import more than their own
# directories: the shared tests/conftest.py loads the graph, messaging and vector
# packages, and through them most of app/. So any Python change runs every
# suite; only a change outside Python (Node, frontend, docs) skips them, since
# none of them runs Node.
MATRIX_PYTHON = (
    f"{PYTHON}/app/**",
    f"{INTEGRATION}/**",
    f"{PYTHON}/tests/conftest.py",
    f"{COMPOSE}/docker-compose.integration.messaging.yml",
    f"{COMPOSE}/docker-compose.integration.vector-db.yml",
    f"{COMPOSE}/docker-compose.integration.graph-db.yml",
    f"{COMPOSE}/docker-compose.integration.kv.yml",
    ".github/workflows/backend-matrix.yml",
) + PYTHON_PACKAGING

for _suite in ("messaging", "vector", "kv", "graph", "gitlab"):
    CHECKS[f"matrix_{_suite}"] = MATRIX_PYTHON
# The storage job runs the Python storage tests against the Node storage server.
CHECKS["matrix_storage"] = MATRIX_PYTHON + STORAGE_NODE

# unit-test.yml's Redis Cluster job: the Python cluster suite (which, like the
# matrix suites, loads most of app/ through the shared conftest), then the Node
# suite, where only the cluster provider's test reaches the cluster.
CHECKS["redis_cluster"] = (
    f"{PYTHON}/app/**",
    f"{PYTHON}/tests/conftest.py",
    f"{INTEGRATION}/conftest.py",
    f"{INTEGRATION}/redis_cluster/**",
    "backend/nodejs/apps/src/libs/services/redis/**",
    "backend/nodejs/apps/src/libs/keyValueStore/**",
    "backend/nodejs/apps/src/libs/services/keyValueStore.service.ts",
    "backend/nodejs/apps/tests/libs/services/redis/**",
    "backend/nodejs/apps/tests/helpers/*edis*",
    "backend/nodejs/apps/package.json",
    "backend/nodejs/apps/package-lock.json",
    f"{COMPOSE}/docker-compose.integration.redis-cluster.yml",
    ".github/workflows/unit-test.yml",
) + PYTHON_PACKAGING

# Checks that run for any change outside DOCS: they exercise the whole product.
ANY_CODE = ("e2e_smoke",)

NAMES = tuple(CHECKS) + ANY_CODE

# CodeQL's language for each codeql_* check, in the order codeql.yml lists them.
CODEQL_LANGUAGES = {
    "codeql_actions": "actions",
    "codeql_javascript": "javascript-typescript",
    "codeql_python": "python",
}


def _pattern(glob: str) -> re.Pattern[str]:
    """``**`` spans directories (and may be empty); ``*`` and ``?`` stay inside one."""
    out = []
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


_COMPILED: dict[str, re.Pattern[str]] = {}


def matches(path: str, globs: Iterable[str]) -> bool:
    for glob in globs:
        pattern = _COMPILED.get(glob)
        if pattern is None:
            pattern = _COMPILED[glob] = _pattern(glob)
        if pattern.match(path):
            return True
    return False


def everything() -> dict[str, str]:
    return {name: "run everything" for name in NAMES}


def decide(paths: list[str]) -> dict[str, str]:
    """Each check that runs, with the first changed file that made it run."""
    paths = [p.strip() for p in paths if p.strip()]
    if not paths or len(paths) >= FILES_API_LIMIT:
        return everything()
    for path in paths:
        if matches(path, EVERYTHING):
            return {name: f"{path} changed" for name in NAMES}
    reasons: dict[str, str] = {}
    for name, globs in CHECKS.items():
        hit = next((p for p in paths if matches(p, globs)), None)
        if hit is not None:
            reasons[name] = hit
    code = next((p for p in paths if not matches(p, DOCS)), None)
    if code is not None:
        for name in ANY_CODE:
            reasons[name] = code
    return reasons


def codeql_matrix(reasons: dict[str, str]) -> str:
    """The ``include`` list for codeql.yml's matrix: the languages that changed."""
    entries = [
        f'{{"language":"{language}","build-mode":"none"}}'
        for check, language in CODEQL_LANGUAGES.items()
        if check in reasons
    ]
    return "[" + ",".join(entries) + "]"


def _append(path_var: str, text: str) -> None:
    path = os.environ.get(path_var)
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--files", help="a file listing the changed paths, one per line")
    group.add_argument("--all", action="store_true", help="run every check (any event but a pull request)")
    args = parser.parse_args(argv)

    if args.all:
        reasons = everything()
    else:
        with open(args.files, encoding="utf-8") as handle:
            reasons = decide(handle.read().splitlines())

    outputs = "".join(f"{name}={'true' if name in reasons else 'false'}\n" for name in NAMES)
    outputs += f"codeql_languages={codeql_matrix(reasons)}\n"
    sys.stdout.write(outputs)
    _append("GITHUB_OUTPUT", outputs)
    lines = ["| Check | Runs | Because |", "|---|---|---|"]
    for name in NAMES:
        why = reasons.get(name)
        lines.append(f"| {name} | {'yes' if why else 'no'} | {why or 'nothing it covers changed'} |")
    _append("GITHUB_STEP_SUMMARY", "### Checks this pull request runs\n\n" + "\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
