"""tests/support/it_backends.py: a backend is used only when its variable names it."""

from __future__ import annotations

import pytest

from tests.support import it_backends


def test_an_unset_endpoint_skips_and_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEO4J_IT_URI", raising=False)
    with pytest.raises(pytest.skip.Exception, match="set NEO4J_IT_URI to run against Neo4j"):
        it_backends.endpoint("NEO4J_IT_URI")


def test_a_blank_endpoint_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ETCD_IT_URL", "  ")
    assert it_backends.configured("ETCD_IT_URL") is None
    with pytest.raises(pytest.skip.Exception, match="set ETCD_IT_URL"):
        it_backends.endpoint("ETCD_IT_URL")


def test_a_set_endpoint_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARANGO_IT_URL", "http://graph.test:18529")
    assert it_backends.endpoint("ARANGO_IT_URL") == "http://graph.test:18529"


def test_a_configured_backend_that_cannot_be_reached_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAFKA_IT_BOOTSTRAP", "broker.test:29192")
    with pytest.raises(pytest.fail.Exception, match=r"Kafka is configured \(KAFKA_IT_BOOTSTRAP=broker.test:29192\) "
                                                    r"but not reachable: ConnectionError\('refused'\)"):
        it_backends.unavailable("KAFKA_IT_BOOTSTRAP", ConnectionError("refused"))


def test_an_unconfigured_backend_that_cannot_be_reached_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QDRANT_IT_HOST", raising=False)
    with pytest.raises(pytest.skip.Exception, match="set QDRANT_IT_HOST to run against Qdrant"):
        it_backends.unavailable("QDRANT_IT_HOST", ConnectionError("refused"))


def test_an_unknown_variable_is_refused() -> None:
    # A typo would otherwise read as "unset" and skip the suite for good.
    with pytest.raises(KeyError, match="NEO4J_IT_URL"):
        it_backends.endpoint("NEO4J_IT_URL")
