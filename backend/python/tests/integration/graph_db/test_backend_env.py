"""Env convention of ``_backends`` (PH02-24). Needs no database, so it is not marked integration."""

import pytest

from . import _backends

ALL_VARS = [
    *_backends.NEO4J_VARS.values(),
    *_backends.ARANGO_VARS.values(),
    _backends.GATE_VAR,
    *(old for olds in _backends.DEPRECATED_ALIASES.values() for old in olds),
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ALL_VARS:
        monkeypatch.delenv(name, raising=False)


def _skip_reason(fn) -> str:
    with pytest.raises(pytest.skip.Exception) as info:
        fn()
    return str(info.value)


def test_unset_neo4j_skips_naming_the_variables() -> None:
    reason = _skip_reason(_backends.neo4j_env)
    assert "PCC_NEO4J_URI" in reason
    assert "PCC_NEO4J_PASSWORD" in reason
    assert "localhost" not in reason


def test_unset_arango_skips_naming_the_variables() -> None:
    reason = _skip_reason(_backends.arango_env)
    assert "PCC_ARANGO_URL" in reason
    assert "PCC_ARANGO_PASSWORD" in reason
    assert "localhost" not in reason


def test_partial_config_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PCC_NEO4J_URI", "bolt://example:7687")
    assert "PCC_NEO4J_PASSWORD" in _skip_reason(_backends.neo4j_env)


def test_gate_mode_fails_instead_of_skipping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PCC_GATE", "1")
    with pytest.raises(pytest.fail.Exception, match="PCC_NEO4J_URI"):
        _backends.neo4j_env()
    with pytest.raises(pytest.fail.Exception, match="PCC_ARANGO_URL"):
        _backends.arango_env()


def test_canonical_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PCC_NEO4J_URI", "bolt://example:7687")
    monkeypatch.setenv("PCC_NEO4J_PASSWORD", "pw")
    monkeypatch.setenv("PCC_ARANGO_URL", "http://example:8529")
    monkeypatch.setenv("PCC_ARANGO_PASSWORD", "pw2")
    assert _backends.neo4j_env() == _backends.Neo4jEnv("bolt://example:7687", "neo4j", "pw")
    arango = _backends.arango_env()
    assert (arango.url, arango.user, arango.password, arango.db) == ("http://example:8529", "root", "pw2", None)
    assert arango.db_or("es") == "es"


@pytest.mark.parametrize("prefix", ["IT", "TEST"])
def test_old_names_work_with_deprecation_warning(monkeypatch: pytest.MonkeyPatch, prefix: str) -> None:
    monkeypatch.setenv(f"NEO4J_{prefix}_URI", "bolt://old:7687")
    monkeypatch.setenv(f"NEO4J_{prefix}_PASSWORD", "oldpw")
    monkeypatch.setenv(f"ARANGO_{prefix}_URL", "http://old:8529")
    monkeypatch.setenv(f"ARANGO_{prefix}_PASSWORD", "oldpw2")
    with pytest.warns(DeprecationWarning, match=f"NEO4J_{prefix}_URI is deprecated, use PCC_NEO4J_URI"):
        neo4j = _backends.neo4j_env()
    with pytest.warns(DeprecationWarning, match=f"ARANGO_{prefix}_URL"):
        arango = _backends.arango_env()
    assert neo4j.uri == "bolt://old:7687"
    assert arango.url == "http://old:8529"


def test_new_name_wins_over_old_without_warning(monkeypatch: pytest.MonkeyPatch, recwarn) -> None:
    monkeypatch.setenv("PCC_NEO4J_URI", "bolt://new:7687")
    monkeypatch.setenv("NEO4J_TEST_URI", "bolt://old:7687")
    monkeypatch.setenv("PCC_NEO4J_PASSWORD", "pw")
    assert _backends.neo4j_env().uri == "bolt://new:7687"
    assert not [w for w in recwarn if issubclass(w.category, DeprecationWarning)]
