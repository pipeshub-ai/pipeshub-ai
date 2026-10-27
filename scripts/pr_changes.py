#!/usr/bin/env python3
"""Say which CI checks a pull request's changed files can affect.

Each workflow asks this script (through .github/actions/pr-changes) and runs
only the jobs whose code changed: a Node-only change skips the Python unit
shards, a Kafka fix runs the messaging legs of the backend matrix and not the
graph ones, and a Markdown-only change skips the browser smoke test.

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
    "backend/python/app/**",
    "backend/python/tests/**",
    "backend/python/pyproject.toml",
    "backend/python/pytest.ini",
    "backend/python/.coveragerc",
)

# Shared by every leg of backend-matrix.yml: what configures or starts any of them.
MATRIX_SHARED = (
    "backend/python/pyproject.toml",
    "backend/python/app/config/**",
    "backend/python/app/indexing_main.py",
    "backend/python/tests/integration/*.py",
    "backend/python/tests/integration/compose/**",
    ".github/workflows/backend-matrix.yml",
)

# The graph and storage legs discover their tests across tests/integration, so
# everything those tests exercise belongs to both.
GRAPH_AND_STORAGE = (
    "backend/python/app/modules/artifacts/**",
    "backend/python/app/modules/record_metadata/**",
    "backend/python/app/models/**",
    "backend/python/app/services/cache/**",
    "backend/python/app/exceptions/storage_exceptions.py",
    "backend/python/app/utils/jwt.py",
    "backend/python/app/utils/storage_path.py",
    "backend/python/app/utils/storage_move.py",
    "backend/python/app/services/storage_documents.py",
)

GIT_HOSTS = (
    "backend/python/tests/integration/github_teams/**",
    "backend/python/app/connectors/sources/github_teams/**",
    "backend/python/app/connectors/sources/github/**",
    "backend/python/app/sources/external/github/**",
    "backend/python/app/sources/client/github/**",
    "backend/python/app/modules/parsers/code_parser/**",
    "backend/python/app/connectors/core/base/sync_point/**",
    "backend/python/app/connectors/core/registry/filters.py",
)

CHECKS: dict[str, tuple[str, ...]] = {
    # unit-test.yml
    "node_unit": NODE_APP + (".github/workflows/unit-test.yml",),
    "python_unit": PYTHON_APP
    + (
        "scripts/unit_shards.py",
        "scripts/unit_shard_durations.json",
        ".github/workflows/unit-test.yml",
    ),
    "redis_cluster": (
        "backend/python/app/config/**",
        "backend/python/app/containers/**",
        "backend/python/app/core/**",
        "backend/python/app/health/**",
        "backend/python/app/services/**",
        "backend/python/app/connectors/services/**",
        "backend/python/app/modules/indexing/**",
        "backend/python/app/modules/code_graph/**",
        "backend/python/app/modules/transformers/**",
        "backend/python/tests/integration/redis_cluster/**",
        "backend/python/pyproject.toml",
        "backend/nodejs/apps/src/libs/services/redis/**",
        "backend/nodejs/apps/src/libs/keyValueStore/**",
        "backend/nodejs/apps/src/libs/services/keyValueStore.service.ts",
        "backend/nodejs/apps/package.json",
        "backend/nodejs/apps/package-lock.json",
        "deployment/docker-compose/docker-compose.integration.redis-cluster.yml",
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
    # backend-matrix.yml, one per suite
    "matrix_messaging": MATRIX_SHARED
    + (
        "backend/python/app/services/messaging/**",
        "backend/python/app/services/redis/**",
        "backend/python/tests/integration/messaging/**",
        "deployment/docker-compose/docker-compose.integration.messaging.yml",
    ),
    "matrix_vector": MATRIX_SHARED
    + (
        "backend/python/app/services/vector_db/**",
        "backend/python/app/services/redis/**",
        "backend/python/app/modules/transformers/**",
        "backend/python/tests/integration/vector_db/**",
        "deployment/docker-compose/docker-compose.integration.vector-db.yml",
    ),
    "matrix_kv": MATRIX_SHARED
    + (
        "backend/python/app/services/redis/**",
        "backend/python/tests/integration/kv/**",
        "backend/nodejs/apps/src/libs/keyValueStore/**",
        "backend/nodejs/apps/src/libs/services/keyValueStore.service.ts",
        "deployment/docker-compose/docker-compose.integration.kv.yml",
    ),
    "matrix_graph": MATRIX_SHARED
    + GRAPH_AND_STORAGE
    + GIT_HOSTS
    + (
        "backend/python/app/services/graph_db/**",
        "backend/python/app/connectors/core/base/data_processor/**",
        "backend/python/app/connectors/core/base/data_store/**",
        "backend/python/app/modules/transformers/**",
        "backend/python/app/connectors/sources/localKB/**",
        "backend/python/app/modules/indexing/record_republish.py",
        "backend/python/app/connectors/sources/linear/**",
        "backend/python/app/sources/client/linear/**",
        "backend/python/app/sources/external/linear/**",
        "backend/python/tests/integration/graph_db/**",
        "deployment/docker-compose/docker-compose.integration.graph-db.yml",
    ),
    "matrix_gitlab": MATRIX_SHARED
    + GIT_HOSTS
    + (
        "backend/python/app/services/graph_db/**",
        "backend/python/app/connectors/core/base/data_processor/**",
        "backend/python/app/connectors/core/base/data_store/**",
        "backend/python/tests/integration/gitlab_simulated/**",
        "backend/python/app/connectors/sources/gitlab/**",
        "backend/python/app/connectors/sources/gitlab_personal/**",
        "backend/python/app/sources/external/gitlab/**",
        "backend/python/app/sources/client/gitlab/**",
        "deployment/docker-compose/docker-compose.integration.graph-db.yml",
    ),
    "matrix_storage": MATRIX_SHARED
    + GRAPH_AND_STORAGE
    + (
        "backend/nodejs/apps/src/modules/storage/**",
        "backend/nodejs/apps/src/libs/middlewares/**",
        "backend/nodejs/apps/src/libs/utils/**",
        "backend/nodejs/apps/src/libs/services/authtoken.service.ts",
        "backend/nodejs/apps/src/libs/services/logger.service.ts",
        "backend/nodejs/apps/src/libs/services/mongo.service.ts",
        "backend/nodejs/apps/tests/support/**",
        "backend/nodejs/apps/package.json",
        "backend/nodejs/apps/package-lock.json",
        "backend/nodejs/apps/tsconfig.json",
        "backend/nodejs/apps/tsconfig.test.json",
    ),
}

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
