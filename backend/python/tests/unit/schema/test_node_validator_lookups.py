"""The schema-lookup seam of the app-level validator: a build may point it at its own registry; the default reads the OSS registry."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.schema import node_validator as nv
from app.schema.node_schema_registry import get_edge_schema, get_node_schema
from app.schema.node_validator import (
    NodeSchemaValidator,
    SchemaValidationError,
    use_schema_lookups,
)

STRICT = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False}
EDGE = {"type": "object", "properties": {"role": {"type": "string"}}, "additionalProperties": False}

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _restore(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("GRAPH_SCHEMA_ENFORCEMENT", raising=False)
    yield
    use_schema_lookups(None, None)


def test_default_reads_the_oss_registry() -> None:
    assert nv._node_lookup is None and nv._edge_lookup is None
    assert nv._node_schema("records") is get_node_schema("records")
    assert nv._edge_schema("belongsTo") is get_edge_schema("belongsTo")


def test_installed_node_lookup_feeds_validation() -> None:
    v = NodeSchemaValidator()
    v.validate_node("seam_only_collection", {"anything": 1})          # unknown to the OSS registry: nothing to enforce
    use_schema_lookups(lambda c: STRICT if c == "seam_only_collection" else None, lambda c: None)
    with pytest.raises(SchemaValidationError):
        v.validate_node("seam_only_collection", {"anything": 1})
    v.validate_node("seam_only_collection", {"name": "x"})
    assert v.validate_node_upsert("seam_only_collection", {}) == ["name"]   # required names come from the installed lookup too


def test_installed_edge_lookup_feeds_validation() -> None:
    v = NodeSchemaValidator()
    v.validate_edge("seam_only_edge", {"bogus": 1})
    use_schema_lookups(lambda c: None, lambda c: EDGE if c == "seam_only_edge" else None)
    with pytest.raises(SchemaValidationError):
        v.validate_edge("seam_only_edge", {"bogus": 1})
    v.validate_edge("seam_only_edge", {"role": "r"})


def test_none_restores_the_default() -> None:
    v = NodeSchemaValidator()
    use_schema_lookups(lambda c: STRICT, lambda c: EDGE)
    with pytest.raises(SchemaValidationError):
        v.validate_node("seam_only_collection", {"anything": 1})
    use_schema_lookups(None, None)
    v.validate_node("seam_only_collection", {"anything": 1})


def test_switching_lookups_for_the_same_collection_never_serves_the_old_validator() -> None:
    v = NodeSchemaValidator()
    other = {"type": "object", "properties": {"other": {"type": "string"}}, "additionalProperties": False}
    use_schema_lookups(lambda c: STRICT, lambda c: None)
    v.validate_node("seam_only_collection", {"name": "x"})
    with pytest.raises(SchemaValidationError):
        v.validate_node("seam_only_collection", {"other": "x"})
    use_schema_lookups(lambda c: other, lambda c: None)
    v.validate_node("seam_only_collection", {"other": "x"})
    with pytest.raises(SchemaValidationError):
        v.validate_node("seam_only_collection", {"name": "x"})


def test_partial_edge_validator_follows_the_installed_lookup() -> None:
    v = NodeSchemaValidator()
    v.validate_edge_update("seam_only_edge", {"bogus": 1})
    use_schema_lookups(lambda c: None, lambda c: EDGE if c == "seam_only_edge" else None)
    with pytest.raises(SchemaValidationError):
        v.validate_edge_update("seam_only_edge", {"bogus": 1})
    v.validate_edge_update("seam_only_edge", {"role": "r"})


@pytest.mark.parametrize("args", [({}, None), (None, "x"), (lambda c: None, [])])
def test_a_non_callable_lookup_is_refused_at_install_time(args: tuple) -> None:
    with pytest.raises(TypeError):
        use_schema_lookups(*args)
    assert nv._node_lookup is None and nv._edge_lookup is None


def test_importing_the_module_installs_nothing() -> None:
    import subprocess
    import sys

    code = "import app.schema.node_validator as m; assert m._node_lookup is None and m._edge_lookup is None"
    subprocess.run([sys.executable, "-c", code], check=True)
