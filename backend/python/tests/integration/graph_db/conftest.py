"""A test marked ``arango_only`` is not collected on the Neo4j leg, and one marked ``neo4j_only`` is not
collected on the ArangoDB leg.

The backend-matrix job fails on any skip, because a skip is a test that did not run against the
backend. A test that cannot apply to Neo4j is left out of its collection instead.

``neo4j_env`` and ``arango_env`` read the backends' connection settings (``_backends.py``).
"""

from __future__ import annotations

import pytest

from ._backends import ArangoEnv, Neo4jEnv, arango_env, neo4j_env


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    left_out = [
        item for item in items
        if item.get_closest_marker("arango_only") and getattr(item, "callspec", None) is not None
        and item.callspec.params.get("env") == "neo4j"
    ]
    left_out += [
        item for item in items
        if item.get_closest_marker("neo4j_only") and getattr(item, "callspec", None) is not None
        and "arango" in item.callspec.params.values()
    ]
    if left_out:
        config.hook.pytest_deselected(items=left_out)
        items[:] = [item for item in items if item not in left_out]


@pytest.fixture(scope="session", name="neo4j_env")
def _neo4j_env_fixture() -> Neo4jEnv:
    return neo4j_env()


@pytest.fixture(scope="session", name="arango_env")
def _arango_env_fixture() -> ArangoEnv:
    return arango_env()
