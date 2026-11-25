"""WP-20 store-lane finding: every real-graph suite runs in backend-matrix's graph jobs.

The graph jobs find their files with ``scripts/graph_shards.py`` (its ``GRAPH_NEEDS`` pattern), and a suite reads
its backend from an endpoint variable (``tests/support/it_backends.py``) that skips while unset. A suite whose
variable the pattern does not name, or the job does not set, never runs in CI: the ``NEO4J_TEST_URI`` suites (agent
engine profiles, toolset instances, knowledge publications, the knowledge read matrix, the record-access contract)
ran only in the local store lane, and three of their cases had rotted unseen on both backends.
"""

from __future__ import annotations

import ast
import re
import sys
import warnings
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from tests.cohesion.support import PYTHON_ROOT, REPO_ROOT

if TYPE_CHECKING:
    from types import ModuleType

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "backend-matrix.yml"
INTEGRATION = PYTHON_ROOT / "tests" / "integration"
POSTGRES_GRAPH = INTEGRATION / "graph_db" / "postgres"
# The graph jobs' own exclusion (``GRAPH_NOT`` in scripts/graph_shards.py).
GRAPH_JOB_EXCLUDES = re.compile(r"ARTIFACTS_IT_STORAGE_URL|NEO4J_IT_PRESET")
# The graph endpoints of it_backends.BACKENDS that backend-matrix serves (the permission harness has its own lane).
GRAPH_VARIABLES = {
    "neo4j": ("NEO4J_IT_URI", "NEO4J_TEST_URI"),
    "arango": ("ARANGO_IT_URL", "ARANGO_TEST_URL"),
}
_ALL = {v for variables in GRAPH_VARIABLES.values() for v in variables}


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _graph_jobs() -> list[dict[str, str]]:
    include = _workflow()["jobs"]["backend"]["strategy"]["matrix"]["include"]
    return [job for job in include if job.get("suite") == "graph"]


@cache
def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _reads(path: Path) -> set[str]:
    """The graph variables ``path`` names as a string literal (an endpoint read)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(_source(path))
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value in _ALL}


def _imported(path: Path) -> list[Path]:
    """The tests.integration modules ``path`` imports, whose backend fixtures it may use."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(_source(path))
    found: list[Path] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("tests.integration."):
            module = PYTHON_ROOT / Path(*node.module.split("."))
            found.extend(p for p in (module.with_suffix(".py"), module / "__init__.py") if p.exists())
    return found


def _real_graph_suites() -> dict[Path, set[str]]:
    """Each integration test file that reads a graph endpoint, itself or through a suite it imports fixtures from."""
    suites: dict[Path, set[str]] = {}
    for path in sorted(INTEGRATION.rglob("test_*.py")):
        # The PostgreSQL graph provider's suites run in pg-runtime.yml on their own provider fixture; one that imports
        # another backend's contract tests by name does not read that suite's Neo4j or ArangoDB endpoint.
        if POSTGRES_GRAPH in path.parents:
            continue
        # backend-matrix's graph jobs leave out the storage suites (its storage job runs them, with the graph beside it).
        if GRAPH_JOB_EXCLUDES.search(_source(path)):
            continue
        variables = _reads(path)
        for module in _imported(path):
            variables |= _reads(module)
        if variables:
            suites[path] = variables
    return suites


def _graph_shards() -> ModuleType:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import graph_shards
    finally:
        sys.path.remove(str(REPO_ROOT / "scripts"))
    return graph_shards


def test_the_graph_suites_are_found_where_the_jobs_look() -> None:
    """What the graph jobs run, across their shards: ``scripts/graph_shards.py`` discovery (main's
    ``scripts/integration_discovery.py``, following each file's imports and the conftests above it)."""
    suites = _real_graph_suites()
    assert any("NEO4J_TEST_URI" in variables for variables in suites.values())
    assert {job["backend"] for job in _graph_jobs()} == {"neo4j", "arangodb"}
    shards = _graph_shards()
    found = set(shards.discovered())
    missed = sorted(str(p.relative_to(PYTHON_ROOT)) for p in suites if str(p.relative_to(PYTHON_ROOT)) not in found)
    assert not missed, (
        f"backend-matrix's graph jobs (scripts/graph_shards.py GRAPH_NEEDS: {shards.GRAPH_NEEDS}) never run {missed}: "
        "name the endpoint variable the suite reads in that pattern, or in the file"
    )


def test_the_local_store_lane_finds_what_ci_finds() -> None:
    from tests.integration.store_lane import DISCOVER

    assert DISCOVER.pattern == _graph_shards().GRAPH_NEEDS


def test_a_suite_that_skips_one_backend_is_not_a_graph_suite_job_failure() -> None:
    """A case that applies to one backend names only that one (an indirect parameter or the module's name), so the
    other job deselects it with ``-k`` instead of running it into a skip, which fails the job."""
    for path in _real_graph_suites():
        text = _source(path)
        assert not re.search(r"pytest\.skip\([^)]*only\b", text), (
            f"{path.relative_to(PYTHON_ROOT)} skips a backend: parametrize the case with only its backend instead"
        )


def test_each_graph_job_sets_every_endpoint_its_suites_read() -> None:
    env = _workflow()["jobs"]["backend"]["env"]
    read = set().union(*_real_graph_suites().values())
    unset = sorted(v for v in read if not str(env.get(v) or "").strip())
    assert not unset, f"backend-matrix sets no {unset}: the suites reading them skip, and the job fails on a skip"
    for job in _graph_jobs():
        assert job["select"] in GRAPH_VARIABLES, job
        assert f"{job['select']}-graph-it" in job["services"].split(), job
