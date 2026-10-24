"""Connector write-path tests: each scenario runs on Neo4j and on ArangoDB.

These drive a connector's sync-side code with a stubbed source API and the real
``DataSourceEntitiesProcessor`` writing into the disposable stores of the parent
directory (see ``../conftest.py`` for how to start them and why the suite refuses
a development database). Unit tests mock the processor, which is how a connector
can pass its unit tests while its writes leave the graph wrong.
"""

import pytest

from ..write_path_support import _ArangoBackend, _Neo4jBackend


@pytest.fixture(params=["arango", "neo4j"])
def backend(request, arango_provider, arango_settings, neo4j_provider, neo4j_settings) -> _ArangoBackend | _Neo4jBackend:
    if request.param == "arango":
        return _ArangoBackend(arango_provider, arango_settings)
    return _Neo4jBackend(neo4j_provider, neo4j_settings)
