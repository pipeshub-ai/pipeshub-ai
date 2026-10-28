"""A test marked ``arango_only`` is not collected on the Neo4j leg.

The backend-matrix job fails on any skip, because a skip is a test that did not run against the
backend. A test that cannot apply to Neo4j is left out of its collection instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    left_out = [
        item for item in items
        if item.get_closest_marker("arango_only") and getattr(item, "callspec", None) is not None
        and item.callspec.params.get("env") == "neo4j"
    ]
    if left_out:
        config.hook.pytest_deselected(items=left_out)
        items[:] = [item for item in items if item not in left_out]
