"""Fixtures for the permission conformance suite.

The provider fixtures are those of the graph-permissions harness (module scope,
loopback and non-default ports only, wiped on setup), re-exported so both suites
talk to the same throwaway stores through the same `KH_PERM_*` variables.
"""

from __future__ import annotations

import pytest

from tests.integration.graph_permissions.conftest import (  # noqa: F401
    arango_provider,
    arango_settings,
    neo4j_provider,
    neo4j_settings,
)

from .layers import GraphCheckLayer, arango_layer, neo4j_layer


@pytest.fixture(scope="module")
def graph_layers(
    neo4j_provider, arango_provider, neo4j_settings, arango_settings  # noqa: F811
) -> list[GraphCheckLayer]:
    return [neo4j_layer(neo4j_provider, neo4j_settings), arango_layer(arango_provider, arango_settings)]
