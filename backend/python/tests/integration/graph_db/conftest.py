"""A test marked ``arango_only`` is not collected on the Neo4j leg, and one marked ``neo4j_only`` is not
collected on the ArangoDB leg.

The backend-matrix job fails on any skip, because a skip is a test that did not run against the
backend. A test that cannot apply to Neo4j is left out of its collection instead.
"""

from __future__ import annotations

import pytest


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


#: Suites that build sparse, synthetic worlds to exercise access, closure and projection logic, not payloads the
#: application writes: graph schema validation is off for them. Every other suite here (the real-writer ones:
#: KB pushes, partial-write failures, record-write concurrency, entity and connector writes, ...) stays strict.
_SYNTHETIC_WORLD_MODULES = frozenset({
    "test_conformance", "test_kg_capture", "test_kg_closure_parity", "test_kg_derivation", "test_kg_guard",
    "test_kg_projection", "test_kg_projection_races", "test_kg_sql_helpers", "test_access", "test_identity",
    "test_core", "test_linear_access_real_backends",
})


@pytest.fixture(autouse=True)
def _synthetic_worlds_skip_schema_validation(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.module.__name__.rsplit(".", 1)[-1] in _SYNTHETIC_WORLD_MODULES:
        monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "off")
