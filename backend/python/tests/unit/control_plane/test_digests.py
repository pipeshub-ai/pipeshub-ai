"""CPD2-1..CPD2-6: one digest scheme for catalog items, shared with Node through vectors."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from app.control_plane.catalog.digests import (
    DuplicateDescriptorName,
    descriptor_set_digest,
    engine_digest,
    skill_descriptor_digest,
    tool_descriptor_digest,
)
from app.control_plane.catalog.refs import InvalidItemRef, ItemRef
from harness_core.mcp_hub.manifest import ApprovedManifest
from harness_core.skills import skill_bundle_digest, skill_bundle_manifest

REPO = Path(__file__).resolve().parents[5]
VECTORS = json.loads((REPO / "contracts/control-plane/vectors/digests.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("vector", VECTORS["tools"], ids=lambda v: v["description"])
def test_cpd2_1_tool_descriptor_vectors(vector: dict[str, object]) -> None:
    assert tool_descriptor_digest(vector["descriptor"]) == vector["digest"]  # type: ignore[arg-type]


def test_cpd2_1_member_order_and_display_fields_do_not_change_the_digest() -> None:
    tool = VECTORS["tools"][0]["descriptor"]
    reordered = dict(reversed(list(tool.items())))
    assert tool_descriptor_digest(reordered) == tool_descriptor_digest(tool)
    assert tool_descriptor_digest({**tool, "digest": "sha256:" + "0" * 64}) == tool_descriptor_digest(tool)


def test_cpd2_1_an_absent_effect_or_sink_is_left_out_and_a_declared_one_is_covered() -> None:
    tool = {"name": "t", "description": "d", "inputSchema": {"type": "object"}}
    assert tool_descriptor_digest(tool) == tool_descriptor_digest({**tool, "effect": None, "sink": None})
    assert tool_descriptor_digest({**tool, "effect": "read"}) != tool_descriptor_digest({**tool, "effect": "write"})


PIN_VECTORS = json.loads((REPO / "contracts/harness/vectors/descriptor-pins.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("vector", PIN_VECTORS["descriptor"], ids=lambda v: v["name"])
def test_cpd2_1_the_catalog_digest_is_the_run_pin_digest(vector: dict[str, object]) -> None:
    assert tool_descriptor_digest(vector["tool"]) == vector["digest"]  # type: ignore[arg-type]


@pytest.mark.parametrize("vector", PIN_VECTORS["mcpManifest"], ids=lambda v: v["name"])
def test_cpd2_2_the_set_digest_is_the_approved_manifest_digest(vector: dict[str, object]) -> None:
    approved = vector["approved"]
    assert isinstance(approved, dict)
    assert descriptor_set_digest(approved.items()) == ApprovedManifest(approved, "p", 1).digest()


@pytest.mark.parametrize("vector", VECTORS["sets"], ids=lambda v: v["description"])
def test_cpd2_2_descriptor_set_vectors(vector: dict[str, object]) -> None:
    pairs = [(d["name"], d["digest"]) for d in vector["descriptors"]]  # type: ignore[index, union-attr]
    assert descriptor_set_digest(pairs) == vector["digest"]
    assert descriptor_set_digest(list(reversed(pairs))) == vector["digest"]


def test_cpd2_2_duplicate_descriptor_name_is_refused() -> None:
    with pytest.raises(DuplicateDescriptorName, match="a"):
        descriptor_set_digest([("a", "sha256:" + "1" * 64), ("a", "sha256:" + "2" * 64)])


@pytest.mark.parametrize("vector", VECTORS["skills"], ids=lambda v: v["description"])
def test_cpd2_3_skill_descriptor_digest_is_the_bundle_digest(vector: dict[str, object]) -> None:
    files = {path: base64.b64decode(content) for path, content in vector["files"].items()}  # type: ignore[union-attr]
    manifest = skill_bundle_manifest(files)
    assert manifest == vector["manifest"]
    assert skill_descriptor_digest(manifest) == vector["digest"] == skill_bundle_digest(files)


@pytest.mark.parametrize("vector", VECTORS["engines"], ids=lambda v: v["description"])
def test_cpd2_4_engine_vectors(vector: dict[str, object]) -> None:
    assert engine_digest(vector["engine"]) == vector["digest"]  # type: ignore[arg-type]


@pytest.mark.parametrize("vector", VECTORS["refs"]["valid"], ids=lambda v: v["ref"])
def test_cpd2_5_item_ref_round_trip(vector: dict[str, str]) -> None:
    ref = ItemRef.parse(vector["ref"])
    assert (ref.kind, ref.publisher, ref.name, ref.version) == (
        vector["kind"],
        vector["publisher"],
        vector["name"],
        vector["version"],
    )
    assert str(ref) == vector["ref"]


@pytest.mark.parametrize("ref", VECTORS["refs"]["invalid"])
def test_cpd2_5_malformed_item_refs_are_refused(ref: str) -> None:
    with pytest.raises(InvalidItemRef):
        ItemRef.parse(ref)


def test_qa3_an_mcp_tool_s_catalog_digest_is_the_digest_an_admin_approves() -> None:
    """The catalog descriptor carries the tool's behaviour hints, so one digest names the tool in both places."""
    from app.agents.mcp.models import MCPToolInfo
    from app.control_plane.catalog.items import ToolDescriptor
    from app.control_plane.contracts.generated.descriptors.tool import (
        CatalogToolDescriptor,
    )
    from app.harness.mcp_hub.manifests import descriptor_of

    (vector,) = [v for v in VECTORS["tools"] if v["description"] == "MCP tool with behaviour hints"]
    listed = vector["descriptor"]
    info = MCPToolInfo(
        name=listed["name"],
        namespaced_name=f"mcp_github_{listed['name']}",
        description=listed["description"],
        input_schema=listed["inputSchema"],
        annotations=listed["annotations"],
    )

    in_catalog = ToolDescriptor(info.name, info.description or "", info.input_schema, hints=info.annotations)

    assert in_catalog.digest == descriptor_of(info).digest() == vector["digest"]
    assert in_catalog.to_json() == {**listed, "digest": vector["digest"]}
    _ = CatalogToolDescriptor.model_validate(in_catalog.to_json())
    flipped = ToolDescriptor(info.name, info.description or "", info.input_schema, hints={"readOnlyHint": False})
    assert flipped.digest != in_catalog.digest
