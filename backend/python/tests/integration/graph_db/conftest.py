import pytest

from ._backends import ArangoEnv, Neo4jEnv, arango_env, neo4j_env


@pytest.fixture(scope="session", name="neo4j_env")
def _neo4j_env_fixture() -> Neo4jEnv:
    return neo4j_env()


@pytest.fixture(scope="session", name="arango_env")
def _arango_env_fixture() -> ArangoEnv:
    return arango_env()
