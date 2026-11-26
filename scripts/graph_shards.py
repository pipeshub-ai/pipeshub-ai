"""Split the real-graph tests that backend-matrix.yml runs on each graph store into shards.

One job per store ran every real-graph test; on Neo4j that reached 37-43 min of a 45-min limit, so pull requests touching
graph code were cancelled. Each store now runs the same tests in SHARD_COUNT jobs (`graph · neo4j (k/n)`).

Which files run is unchanged: `discovered()` is the discovery the job always ran (see the workflow header). Every
discovered file runs in exactly one shard. Shards 1 to n-1 run the discovered files under the paths NAMED gives them; the
last shard runs every discovered file they do not name, so a new real-graph test runs without editing this file.
scripts/test_graph_shards.py checks that every named path is still discovered, no path is named twice, and the workflow
runs every shard on both stores.

Run (from anywhere): python3 scripts/graph_shards.py <k>/<n> prints shard k's test files, relative to backend/python.

To rebalance, move paths between shards so each shard job's time is about even. The minutes next to each path are
Neo4j / ArangoDB test time from the nightly runs of 2026-11-18 to 2026-11-20; a shard job adds about 5 min of set-up.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from integration_discovery import discover

PYTHON_ROOT = Path(__file__).resolve().parent.parent / "backend" / "python"

SHARD_COUNT = 4

# What makes a file a real-graph test: the integration suites' endpoints, the harness suites' (tests/support/it_backends.py)
# and a connection through tests/integration/real_graph.py. backend/python/tests/cohesion/test_backend_matrix_graph_discovery.py
# holds that every suite reading a graph endpoint is found by it, and tests/integration/store_lane.py runs the same set locally.
GRAPH_NEEDS = r"NEO4J_IT_URI|ARANGO_IT_URL|NEO4J_TEST_URI|ARANGO_TEST_URL|tests\.integration\.real_graph"
GRAPH_NOT = "ARTIFACTS_IT_STORAGE_URL|NEO4J_IT_PRESET"
GRAPH_EXCLUDED_DIRS = ("tests/integration/gitlab_simulated", "tests/integration/graph_db/postgres", "tests/integration/kh_browse_cost")

# Neo4j / ArangoDB minutes per shard: 1: 8.6 / 8.0, 2: 9.0 / 2.2, 3: 9.4 / 3.6, 4 (the rest): 9.2 / 9.3.
NAMED: dict[int, tuple[str, ...]] = {
    1: (
        "tests/integration/github_teams",  # 7.8 / 7.8
        "tests/integration/graph_db/test_jira_dc_issue_security_real_backends.py",  # 0.8 / 0.3
    ),
    2: (
        "tests/integration/graph_db/test_partial_write_failures_real_backends.py",  # 5.2 / 0.3
        "tests/integration/test_tool_bounds_unplaced_members_neo4j_e2e.py",  # 1.9 / 0
        "tests/integration/graph_db/test_graph_view_paging_real_backends.py",  # 1.3 / 1.1
        "tests/integration/graph_db/test_kb_delete_real_backends.py",  # 0.6 / 0.9
    ),
    3: (
        "tests/integration/test_tool_bounds_neo4j_e2e.py",  # 5.1 / 0
        "tests/integration/graph_db/test_linear_access_real_backends.py",  # 1.3 / 1.0
        "tests/integration/test_knowledge_hub_app_browse_e2e.py",  # 0.9 / 0
        "tests/integration/graph_db/test_kh_browse_denied_children_real_backends.py",  # 0.7 / 0.2
        "tests/integration/graph_db/test_kh_declared_group_cross_connector_real_backends.py",  # 0.7 / 0.1
        "tests/integration/graph_db/test_jira_issue_security_real_backends.py",  # 0.6 / 0.4
        "tests/integration/graph_db/test_permitted_entity_records_differential.py",  # 0.1 / 1.9
    ),
}


def discovered() -> list[str]:
    """Every real-graph test file, relative to backend/python.

    A file needs a graph store when it, a module it imports or a conftest above it names one. Files that also need
    storage run in the storage job; files that need a server started at a memory preset (NEO4J_IT_PRESET) are run by
    hand against one; the GitLab simulator, the PostgreSQL provider and the Knowledge Hub browse cost budgets
    (permission-conformance.yml, kh-browse-cost) have jobs of their own.
    """
    with contextlib.chdir(PYTHON_ROOT):  # discovery names modules after paths relative to the import root
        return discover(
            Path("tests/integration"), GRAPH_NEEDS, GRAPH_NOT, exclude_dirs=[Path(d) for d in GRAPH_EXCLUDED_DIRS]
        )


def under(path: str, named: str) -> bool:
    return path == named or path.startswith(named + "/")


def shard_files(shard: int, files: list[str]) -> list[str]:
    """The files of `files` that `shard` runs."""
    if shard == SHARD_COUNT:
        return [f for f in files if not any(under(f, p) for paths in NAMED.values() for p in paths)]
    return [f for f in files if any(under(f, p) for p in NAMED[shard])]


def main(argv: list[str]) -> int:
    shard, _, count = (argv[0] if len(argv) == 1 else "").partition("/")
    if not (shard.isdigit() and count == str(SHARD_COUNT) and 1 <= int(shard) <= SHARD_COUNT):
        sys.stderr.write(f"usage: graph_shards.py <k>/{SHARD_COUNT}, 1 <= k <= {SHARD_COUNT}\n")
        return 2
    files = shard_files(int(shard), discovered())
    if not files:
        # pytest given no paths would run every test it finds, not none
        sys.stderr.write(f"shard {argv[0]} has no test files\n")
        return 1
    sys.stdout.write("\n".join(files) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
