"""The spec the suite checks replies against is the one the server documents.

``helper/openapi_spec.py`` merges a downstream build's spec files into
``pipeshub-openapi.yaml`` the way ``ApiDocsService`` does at start-up. These
tests pin that merge, and that every ``$ref`` in the result points somewhere,
so a schema dropped from one file while the other still uses it fails here
instead of as a confusing validation error against a live stack.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from helper import openapi_spec

pytestmark = pytest.mark.unit


def test_objects_merge_lists_keep_every_distinct_item_and_values_are_replaced() -> None:
    target = {
        "paths": {"/a": {"get": {"summary": "base"}}},
        "tags": [{"name": "Base"}],
        "info": {"version": "1"},
    }
    openapi_spec.merge(target, {
        "paths": {"/a": {"post": {"summary": "added"}}, "/b": {"get": {}}},
        "tags": [{"name": "Base"}, {"name": "Added"}],
        "info": {"version": "2"},
        "__proto__": {"polluted": True},
    })
    assert target == {
        "paths": {"/a": {"get": {"summary": "base"}, "post": {"summary": "added"}}, "/b": {"get": {}}},
        "tags": [{"name": "Base"}, {"name": "Added"}],
        "info": {"version": "2"},
    }


def test_without_an_extension_the_spec_is_the_repository_file(tmp_path: Path) -> None:
    assert openapi_spec.overlay_paths(tmp_path) == []
    assert openapi_spec.load_spec(tmp_path)["paths"].keys() == openapi_spec._read(openapi_spec.SPEC_PATH)["paths"].keys()


def test_an_extension_overlay_is_merged_in(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "paths:\n  /it-overlay-probe:\n    get:\n      operationId: itOverlayProbe\n"
        "components:\n  schemas:\n    ItOverlayProbe:\n      type: object\n"
    )
    extension = tmp_path / "downstream" / "suite_extensions"
    extension.mkdir(parents=True)
    (extension / "openapi_overlays.py").write_text(f"OVERLAYS = ({str(overlay)!r},)\n")

    spec = openapi_spec.load_spec(tmp_path)

    assert spec["paths"]["/it-overlay-probe"]["get"]["operationId"] == "itOverlayProbe"
    assert "ItOverlayProbe" in spec["components"]["schemas"]
    assert len(spec["paths"]) == len(openapi_spec._read(openapi_spec.SPEC_PATH)["paths"]) + 1


def test_every_ref_in_the_served_spec_resolves() -> None:
    spec = openapi_spec.load_spec()

    def resolves(pointer: str) -> bool:
        node: object = spec
        for part in pointer.split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
        return True

    refs = set(re.findall(r'"\$ref": "#/([^"]+)"', json.dumps(spec, default=str)))
    assert len(refs) > 100, "found almost no $ref; the reader has stopped matching the spec"
    dangling = sorted(ref for ref in refs if not resolves(ref))
    assert not dangling, f"These $ref targets are in no spec file: {dangling}"
