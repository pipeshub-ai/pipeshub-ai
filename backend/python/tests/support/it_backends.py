"""Where the integration suites find their real backends: only where a variable says.

A suite runs against a backend only when that backend's variable is set; unset, it skips and names the variable.
There is no localhost default. On a shared host another session's test databases listen on the conventional ports,
and a suite that dialled one ran against, and wrote into, someone else's data (2026-10-28).

Set but unreachable is a failure: the job said the backend is there, and a skip would turn a broken backend into a
green run. A port or password variable keeps its default; the host or URL variable here is what opts a suite in.
"""

from __future__ import annotations

import os
from typing import NoReturn

import pytest

BACKENDS: dict[str, str] = {
    "NEO4J_IT_URI": "Neo4j",
    "ARANGO_IT_URL": "ArangoDB",
    "NEO4J_TEST_URI": "Neo4j",
    "ARANGO_TEST_URL": "ArangoDB",
    "REDIS_IT_HOST": "Redis",
    "REDIS_IT_URL": "Redis",
    "REDIS_CLUSTER_ENDPOINTS": "a Redis Cluster",
    "QDRANT_IT_HOST": "Qdrant",
    "KAFKA_IT_BOOTSTRAP": "Kafka",
    "ETCD_IT_URL": "etcd",
    "QDRANT_HOST": "Qdrant",
    "OPENSEARCH_HOST": "OpenSearch",
    "REDIS_VECTOR_HOST": "Redis (vector)",
    "KH_PERM_NEO4J_URI": "the permission harness's Neo4j",
    "KH_PERM_ARANGO_URL": "the permission harness's ArangoDB",
}


def configured(variable: str) -> str | None:
    """The variable's value, or None when it is unset or blank."""
    _known(variable)
    return os.environ.get(variable, "").strip() or None


def endpoint(variable: str) -> str:
    """The variable's value; skips the calling test or fixture when it is unset."""
    value = configured(variable)
    if value is None:
        pytest.skip(f"set {variable} to run against {BACKENDS[variable]}")
    return value


def unavailable(variable: str, error: BaseException | str) -> NoReturn:
    """A backend could not be used: fail when its variable is set, skip when it is not."""
    value = configured(variable)
    if value is not None:
        pytest.fail(f"{BACKENDS[variable]} is configured ({variable}={value}) but not reachable: {error!r}")
    pytest.skip(f"set {variable} to run against {BACKENDS[variable]}")


def _known(variable: str) -> None:
    if variable not in BACKENDS:
        raise KeyError(f"{variable} is not a backend endpoint variable; add it to it_backends.BACKENDS")
