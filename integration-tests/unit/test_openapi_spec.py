"""Response checks use the spec the server publishes: the base file plus its overlays.

A build that documents extra routes or fields in an ``api-docs/*-overlay.yaml``
serves them merged into ``pipeshub-openapi.yaml``; its responses must be judged
against that merged document, merged the same way the API merges it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from helper import openapi_spec

pytestmark = pytest.mark.unit


def test_objects_merge_by_key_arrays_keep_distinct_items_and_scalars_are_replaced() -> None:
    spec = {"info": {"title": "base", "version": "1"}, "tags": [{"name": "Users"}], "openapi": "3.0.0"}
    openapi_spec.merge_spec(spec, {"info": {"title": "merged"}, "tags": [{"name": "Users"}, {"name": "Extra"}], "openapi": "3.0.3"})
    assert spec == {
        "info": {"title": "merged", "version": "1"},
        "tags": [{"name": "Users"}, {"name": "Extra"}],
        "openapi": "3.0.3",
    }


def test_an_overlay_adds_a_field_to_a_base_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = tmp_path / "modules" / "api-docs" / "pipeshub-openapi.yaml"
    overlay = tmp_path / "extra" / "modules" / "api-docs" / "extra-overlay.yaml"
    for path, doc in (
        (base, {"components": {"schemas": {"User": {"type": "object", "properties": {"email": {"type": "string"}}}}}}),
        (overlay, {"components": {"schemas": {"User": {"properties": {"seatStatus": {"type": "string"}}}}}}),
    ):
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    monkeypatch.setattr(openapi_spec, "API_SRC", tmp_path)
    monkeypatch.setattr(openapi_spec, "BASE_SPEC", base)
    openapi_spec.load_spec.cache_clear()
    try:
        user = openapi_spec.load_spec()["components"]["schemas"]["User"]
    finally:
        openapi_spec.load_spec.cache_clear()
    assert user == {"type": "object", "properties": {"email": {"type": "string"}, "seatStatus": {"type": "string"}}}


def test_the_checked_in_spec_loads_with_every_overlay_in_the_tree() -> None:
    with openapi_spec.BASE_SPEC.open(encoding="utf-8") as fh:
        base_paths = set(yaml.safe_load(fh)["paths"])
    overlay_paths = set()
    for path in openapi_spec.overlay_paths():
        with path.open(encoding="utf-8") as fh:
            overlay_paths |= set(yaml.safe_load(fh).get("paths", {}))
    assert set(openapi_spec.load_spec()["paths"]) == base_paths | overlay_paths
