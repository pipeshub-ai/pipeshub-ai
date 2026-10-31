#!/usr/bin/env python3
"""Say which CI checks a pull request's changed files can affect.

Each workflow asks this script (through .github/actions/pr-changes) and runs
only the jobs whose code changed: a Node-only change skips the Python unit
shards and the Python backend-matrix suites, and a Markdown-only change skips
the browser smoke test. The integration suite's shards are picked the same way,
from which connector or test folder a change touches.

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
    # The storage server reads and writes its documents through these.
    "backend/nodejs/apps/src/libs/repositories/**",
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


# Test helpers outside tests/integration that the integration suites import.
# test_pr_changes.py walks those imports and fails when this list misses one.
INTEGRATION_HELPERS = (
    f"{PYTHON}/tests/conftest.py",
    f"{PYTHON}/tests/support/**",
    f"{PYTHON}/tests/unit/agents/adapter/conftest.py",
    f"{PYTHON}/tests/unit/agents/adapter/support/scripted_transport.py",
    f"{PYTHON}/tests/unit/connectors/sources/test_connector_workflow_integration.py",
    f"{PYTHON}/tests/unit/modules/entity_resolution/conftest.py",
    f"{PYTHON}/tests/unit/modules/indexing/test_entity_index_rebuild.py",
    f"{PYTHON}/tests/unit/modules/pipeline/fakes.py",
    f"{PYTHON}/tests/unit/modules/transformers/test_entity_vectorstore_delete.py",
    f"{PYTHON}/tests/unit/services/artifact_registry/fakes.py",
    f"{PYTHON}/tests/unit/services/vector_db/test_strategy_contract.py",
    f"{PYTHON}/tests/unit/web/qa/lab.py",
)

# backend-matrix.yml's Python suites. Their tests import more than their own
# directories: the shared tests/conftest.py loads the graph, messaging and vector
# packages, and through them most of app/. So once the workflow starts (its
# trigger paths list what the suites cover), any change to app/, the integration
# tests or the helpers they import runs every suite; only a change outside Python
# (Node, frontend, docs) skips them, since none of them runs Node.
MATRIX_PYTHON = (
    f"{PYTHON}/app/**",
    f"{INTEGRATION}/**",
    f"{COMPOSE}/docker-compose.integration.messaging.yml",
    f"{COMPOSE}/docker-compose.integration.vector-db.yml",
    f"{COMPOSE}/docker-compose.integration.graph-db.yml",
    f"{COMPOSE}/docker-compose.integration.kv.yml",
    ".github/workflows/backend-matrix.yml",
) + INTEGRATION_HELPERS + PYTHON_PACKAGING

for _suite in ("messaging", "vector", "kv", "graph", "gitlab"):
    CHECKS[f"matrix_{_suite}"] = MATRIX_PYTHON
# The storage job runs the Python storage tests against the Node storage server.
CHECKS["matrix_storage"] = MATRIX_PYTHON + STORAGE_NODE

# unit-test.yml's Redis Cluster job. Its Python suite loads most of app/ through
# the shared conftest and imports the messaging integration scenarios, and its
# Node step runs the whole Node suite with REDIS_MODE=cluster, which is what
# catches a unit test that reads the ambient mode. So it runs on any backend
# change the unit tests run on.
CHECKS["redis_cluster"] = (
    NODE_APP
    + PYTHON_APP
    + (
        f"{COMPOSE}/docker-compose.integration.redis-cluster.yml",
        ".github/workflows/unit-test.yml",
    )
)

# integration-tests.yml's helper unit tests (mocks only). The shards run them
# first; a pull request that runs no shard runs them in a job of their own.
CHECKS["integration_helpers"] = (
    "integration-tests/**",
    ".github/workflows/integration-tests.yml",
)

# Checks that run for any change outside DOCS: they exercise the whole product.
ANY_CODE = ("e2e_smoke",)

NAMES = tuple(CHECKS) + ANY_CODE

# CodeQL's language for each codeql_* check, in the order codeql.yml lists them.
CODEQL_LANGUAGES = {
    "codeql_actions": "actions",
    "codeql_javascript": "javascript-typescript",
    "codeql_python": "python",
}


IT_WORKFLOW = ".github/workflows/integration-tests.yml"

# The connector shards' markers, as CONN_SHARD_1 .. CONN_SHARD_5 in
# integration-tests.yml list them (test_pr_changes.py checks the two agree).
CONNECTOR_SHARDS = {
    "connectors-1": (
        "nextcloud", "bookstack", "bookstack_sim", "mariadb", "minio", "web", "local_fs", "local_fs_sim", "github",
        "sharepoint",
    ),
    "connectors-2": (
        "jira", "azure_files", "smb", "zendesk", "outlook", "gmail_workspace", "gmail_individual", "servicenow",
    ),
    "connectors-3": ("notion", "confluence", "gcs", "zammad", "sharepoint_sim", "postgres"),
    "connectors-4": ("google_drive_individual", "gitlab", "azure_blob", "rss", "github_teams", "linear"),
    "connectors-5": ("google_drive_workspace", "s3"),
}
# The shards a pull request can run, in the workflow's order. Each one waits for
# an approval and then takes about three hours, so these rules save the most.
IT_SHARDS = (*CONNECTOR_SHARDS, "core", "demo")
_SHARD_OF = {marker: shard for shard, markers in CONNECTOR_SHARDS.items() for marker in markers}
_SHARD_OF["demo"] = "demo"

# Changes no shard builds, starts or collects. integration-tests/unit runs in the
# helper job (and first in every shard); sdk_tests and perf have workflows of
# their own and are outside pytest.ini's testpaths.
IT_NO_STACK = (
    f"{PYTHON}/tests/**",
    "backend/nodejs/apps/tests/**",
    "frontend/**/*.test.ts",
    "frontend/**/*.test.tsx",
    "scripts/test_*.py",
    "scripts/unit_shards.py",
    "scripts/unit_shard_durations.json",
    "integration-tests/unit/**",
    "integration-tests/sdk_tests/**",
    "integration-tests/perf/**",
)
IT_RUNTIME_TREES = (f"{PYTHON}/app/", f"{PYTHON}/tests/evals/", "backend/nodejs/apps/src/", "frontend/", "integration-tests/")
# The suite's own README and other Markdown at its top level, which nothing reads.
IT_SUITE_DOCS = ("integration-tests/*.md",)
# The demo questions' model list, which integration-tests/connectors/demo imports.
IT_DEMO = (f"{PYTHON}/tests/evals/**",)
# Test folders whose tests only the core shard runs and that no other folder
# imports (test_pr_changes.py checks both), the nightly-only folders core still
# collects (a file that fails to import fails every shard, and core shows it),
# and the frontend, whose end-to-end tests run on core.
# response-validation/helper is not in it: the connector suites' graph validators
# import its schema checks.
IT_CORE = tuple(
    f"integration-tests/{folder}/**"
    for folder in (
        "cleanup", "examples", "mcp_package", "parsers", "permissions", "response-validation", "security",
        "resilience", "storage", "ai_agents", "named_entities",
    )
) + ("frontend/**",)
# The desktop app's Local FS code, which the local_fs_sim suite runs headless, and
# what the desktop agent loads it with: the Electron tsconfig and the frontend's packages.
IT_LOCAL_SYNC = (
    "frontend/electron/local-sync/**",
    "frontend/electron/tsconfig.json",
    "frontend/package.json",
    "frontend/package-lock.json",
)

# Connector code: a folder under one of these is that connector's.
CONNECTOR_ROOTS = (
    f"{PYTHON}/app/connectors/sources",
    f"{PYTHON}/app/sources/client",
    f"{PYTHON}/app/sources/external",
)
# The markers whose tests exercise each connector folder's code, under any of
# the roots. A change there runs those markers' shards and core, which starts
# every connector and holds the agent tests that call the clients. A folder
# that is not listed, such as one shared by many connectors (http, utils,
# microsoft) or imported by product code outside the connectors (atlassian,
# local_fs, notion, web), runs every shard. An empty tuple is a connector with no
# integration suite, so core alone. test_pr_changes.py checks this list against
# the imports: a listed folder may be imported only from its own markers'
# folders, the agents' actions and tools, and the connector registry.
CONNECTOR_CODE_MARKERS: dict[str, tuple[str, ...]] = {
    **{
        name: ()
        for name in (
            "airtable", "asana", "bitbucket", "box", "clickhouse", "clickup", "databricks", "discord",
            "dropbox_individual", "drupal_wiki", "evernote", "freshdesk", "hackernews", "lattice", "linkedin",
            "lumos", "monday", "pagerduty", "posthog", "redshift", "salesforce", "trello", "workday",
            "zoom",
        )
    },
    "azure": ("azure_blob", "azure_files"),
    "azure_blob": ("azure_blob",),
    "azure_files": ("azure_files",),
    "bookstack": ("bookstack", "bookstack_sim"),
    "cifs": ("smb",),
    "demo": ("demo",),
    # external/bookstack imports external/dropbox.
    "dropbox": ("bookstack", "bookstack_sim"),
    "gcs": ("gcs",),
    "google_cloud_storage": ("gcs",),
    "github": ("github", "github_teams"),
    "github_teams": ("github", "github_teams"),
    "gitlab": ("gitlab",),
    "gitlab_personal": ("gitlab",),
    "google": ("google_drive_workspace", "google_drive_individual", "gmail_workspace", "gmail_individual"),
    "linear": ("linear",),
    "minio": ("minio",),
    "s3": ("s3", "minio"),
    "network_share": ("smb",),
    "nextcloud": ("nextcloud",),
    "rss": ("rss",),
    "servicenow": ("servicenow",),
    "smb": ("smb",),
    "zammad": ("zammad",),
    "zendesk": ("zendesk",),
}
CONNECTOR_TESTS = "integration-tests/connectors"
# A connector test folder's markers, where they differ from its name.
# test_pr_changes.py checks that every test file in a folder carries one of them.
CONNECTOR_TEST_MARKERS: dict[str, tuple[str, ...]] = {
    "bookstack": ("bookstack", "bookstack_sim"),
    "github_personal": ("github",),
    "gmail": ("gmail_workspace", "gmail_individual"),
    "local_fs": ("local_fs", "local_fs_sim"),
    "sharepoint": ("sharepoint", "sharepoint_sim"),
    # Marked cifs, which no shard selects; core still collects the files.
    "cifs": (),
    # Marked snowflake, which no shard selects: it needs a live account.
    "snowflake": (),
}
# Shards whose suites import a connector test folder's helpers, beyond the
# folder's own: MinIO's tests build on S3's, the personal GitHub suite on GitHub
# Teams' (which, like GitLab's, uses Jira's block parser), Gmail and the individual
# Google Drive suite on the workspace Drive suite's helpers, and the cleanup tests
# on core create MinIO, Postgres and S3 connectors.
# test_pr_changes.py follows the suite's imports and fails when this misses one.
CONNECTOR_TESTS_ALSO_RUN: dict[str, tuple[str, ...]] = {
    "github_teams": ("connectors-1",),
    "google_drive_workspace": ("connectors-2", "connectors-4"),
    "jira": ("connectors-1", "connectors-4"),
    "minio": ("core",),
    "postgres": ("core",),
    "s3": ("connectors-1", "core"),
}

_CONNECTOR_CODE = re.compile("(?:" + "|".join(map(re.escape, CONNECTOR_ROOTS)) + r")/([^/]+)/")
_CONNECTOR_TEST = re.compile(re.escape(CONNECTOR_TESTS) + r"/([^/]+)/")


def _shards_of(markers: Iterable[str]) -> set[str]:
    return {_SHARD_OF[marker] for marker in markers}


def _integration_shards_for(path: str) -> set[str]:
    # Markdown in these trees can be what the code or the tests read (the skill
    # loader's files, the parser fixtures), so it follows its folder's rule.
    if matches(path, DOCS) and (not path.startswith(IT_RUNTIME_TREES) or matches(path, IT_SUITE_DOCS)):
        return set()
    if matches(path, IT_DEMO):
        return {"demo"}
    if matches(path, IT_NO_STACK) or (path.startswith(".github/workflows/") and path != IT_WORKFLOW):
        return set()
    if code := _CONNECTOR_CODE.match(path):
        markers = CONNECTOR_CODE_MARKERS.get(code.group(1))
        return set(IT_SHARDS) if markers is None else {"core"} | _shards_of(markers)
    if test := _CONNECTOR_TEST.match(path):
        name = test.group(1)
        markers = CONNECTOR_TEST_MARKERS.get(name, (name,) if name in _SHARD_OF else None)
        if markers is None:
            return set(IT_SHARDS)
        return (_shards_of(markers) or {"core"}) | set(CONNECTOR_TESTS_ALSO_RUN.get(name, ()))
    if matches(path, IT_LOCAL_SYNC):
        return {"core"} | _shards_of(("local_fs_sim",))
    if matches(path, IT_CORE) and not path.startswith("integration-tests/response-validation/helper/"):
        return {"core"}
    return set(IT_SHARDS)


def integration_shards(paths: list[str]) -> dict[str, str]:
    """Each integration shard a pull request runs, with the first changed file that made it run."""
    paths = [p.strip() for p in paths if p.strip()]
    if not paths or len(paths) >= FILES_API_LIMIT:
        return {shard: "run everything" for shard in IT_SHARDS}
    for path in paths:
        if matches(path, EVERYTHING):
            return {shard: f"{path} changed" for shard in IT_SHARDS}
    reasons: dict[str, str] = {}
    for path in paths:
        for shard in _integration_shards_for(path):
            reasons.setdefault(shard, path)
    return {shard: reasons[shard] for shard in IT_SHARDS if shard in reasons}


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
        shards = {shard: "run everything" for shard in IT_SHARDS}
    else:
        with open(args.files, encoding="utf-8") as handle:
            paths = handle.read().splitlines()
        reasons = decide(paths)
        shards = integration_shards(paths)

    outputs = "".join(f"{name}={'true' if name in reasons else 'false'}\n" for name in NAMES)
    outputs += f"codeql_languages={codeql_matrix(reasons)}\n"
    outputs += "integration_shards=[" + ",".join(f'"{shard}"' for shard in shards) + "]\n"
    sys.stdout.write(outputs)
    _append("GITHUB_OUTPUT", outputs)
    lines = ["| Check | Runs | Because |", "|---|---|---|"]
    for name in NAMES:
        why = reasons.get(name)
        lines.append(f"| {name} | {'yes' if why else 'no'} | {why or 'nothing it covers changed'} |")
    for shard in IT_SHARDS:
        why = shards.get(shard)
        lines.append(f"| integration {shard} | {'yes' if why else 'no'} | {why or 'nothing it covers changed'} |")
    _append("GITHUB_STEP_SUMMARY", "### Checks this pull request runs\n\n" + "\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
