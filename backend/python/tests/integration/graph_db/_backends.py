"""One env convention for the graph-DB integration tests.

Neo4j:   PCC_NEO4J_URI, PCC_NEO4J_PASSWORD, [PCC_NEO4J_USER=neo4j]
ArangoDB: PCC_ARANGO_URL, PCC_ARANGO_PASSWORD, [PCC_ARANGO_USER=root], [PCC_ARANGO_DB]

There are deliberately no host defaults: a test never guesses where a database is. When a
backend is not configured the test skips with the names above; with PCC_GATE=1 it fails.
The pre-PH-02 names (NEO4J_IT_*, NEO4J_TEST_*, ARANGO_IT_*, ARANGO_TEST_*) still work for
one release and warn.
"""

import os
import warnings
from dataclasses import dataclass
from typing import NoReturn

import pytest

GATE_VAR = "PCC_GATE"

NEO4J_VARS = {"uri": "PCC_NEO4J_URI", "password": "PCC_NEO4J_PASSWORD", "user": "PCC_NEO4J_USER"}
ARANGO_VARS = {
    "url": "PCC_ARANGO_URL",
    "password": "PCC_ARANGO_PASSWORD",
    "user": "PCC_ARANGO_USER",
    "db": "PCC_ARANGO_DB",
}

DEPRECATED_ALIASES = {
    "PCC_NEO4J_URI": ("NEO4J_IT_URI", "NEO4J_TEST_URI"),
    "PCC_NEO4J_PASSWORD": ("NEO4J_IT_PASSWORD", "NEO4J_TEST_PASSWORD"),
    "PCC_ARANGO_URL": ("ARANGO_IT_URL", "ARANGO_TEST_URL"),
    "PCC_ARANGO_PASSWORD": ("ARANGO_IT_PASSWORD", "ARANGO_TEST_PASSWORD"),
    "PCC_ARANGO_DB": ("ARANGO_TEST_DB",),
}


@dataclass(frozen=True)
class Neo4jEnv:
    uri: str
    user: str
    password: str


@dataclass(frozen=True)
class ArangoEnv:
    url: str
    user: str
    password: str
    db: str | None

    def db_or(self, default: str) -> str:
        return self.db or default


def gate_mode() -> bool:
    return os.environ.get(GATE_VAR) == "1"


def _read(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    for old in DEPRECATED_ALIASES.get(name, ()):
        value = os.environ.get(old)
        if value:
            warnings.warn(
                f"{old} is deprecated, use {name}", DeprecationWarning, stacklevel=3
            )
            return value
    return None


def unavailable(reason: str) -> NoReturn:
    """Skip, or fail when PCC_GATE=1 so a gate run cannot pass by skipping everything."""
    if gate_mode():
        pytest.fail(f"{reason} ({GATE_VAR}=1: refusing to skip)", pytrace=False)
    pytest.skip(reason)


def neo4j_env() -> Neo4jEnv:
    uri, password = _read(NEO4J_VARS["uri"]), _read(NEO4J_VARS["password"])
    if not uri or not password:
        unavailable(
            f"Neo4j not configured: set {NEO4J_VARS['uri']} and {NEO4J_VARS['password']} "
            "(see tests/integration/graph_db/README.md)"
        )
    return Neo4jEnv(uri=uri, user=_read(NEO4J_VARS["user"]) or "neo4j", password=password)


def arango_env() -> ArangoEnv:
    url, password = _read(ARANGO_VARS["url"]), _read(ARANGO_VARS["password"])
    if not url or not password:
        unavailable(
            f"ArangoDB not configured: set {ARANGO_VARS['url']} and {ARANGO_VARS['password']} "
            "(see tests/integration/graph_db/README.md)"
        )
    return ArangoEnv(
        url=url,
        user=_read(ARANGO_VARS["user"]) or "root",
        password=password,
        db=_read(ARANGO_VARS["db"]),
    )
