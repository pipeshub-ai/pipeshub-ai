"""NV-1, NV-2, NV-3, NV-5: MCP tool names, the names models and engines see, and description escaping (P2.8)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from harness_core.mcp_hub import (
    ToolNameInvalid,
    check_spec_name,
    export_description,
    export_name,
    export_names,
    export_namespace,
)

VECTORS: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts/harness/vectors/mcp-tool-names.json").read_text("utf-8")
)
PROVIDER_NAME = re.compile(VECTORS["exportRule"])


@pytest.mark.parametrize("case", VECTORS["spec"], ids=lambda c: repr(c["name"])[:40])
def test_nv1_the_spec_rule(case: dict[str, Any]) -> None:
    if case["valid"]:
        check_spec_name(case["name"])
    else:
        with pytest.raises(ToolNameInvalid) as refused:
            check_spec_name(case["name"])
        assert refused.value.reason_code == "mcp_tool_name_invalid"


@pytest.mark.parametrize("case", VECTORS["export"], ids=lambda c: f"{c['label'][:12]}/{c['name'][:20]}")
def test_nv2_exported_names(case: dict[str, Any]) -> None:
    exported = export_name(export_namespace(case["label"]), case["name"])
    assert exported == case["exported"]
    assert PROVIDER_NAME.fullmatch(exported)


@pytest.mark.parametrize("case", VECTORS["collisions"], ids=["crafted", "duplicate", "invalid"])
def test_nv3_collisions_refuse_every_party(case: dict[str, Any]) -> None:
    exported, refused = export_names(case["namespace"], case["names"])
    assert exported == case["exported"]
    assert {name: r.reason_code for name, r in refused.items()} == case["refused"]


@pytest.mark.parametrize("case", VECTORS["descriptions"], ids=range(len(VECTORS["descriptions"])))
def test_nv5_descriptions_are_escaped_as_data(case: dict[str, Any]) -> None:
    assert export_description(case["input"]) == case["output"]


def test_f3_a_name_at_the_bound_is_kept_and_one_over_is_cut_with_its_digest() -> None:
    """MCPFIX F3-1, F3-3: 52 characters is the bound; two long names alike at the front stay apart."""
    assert export_name("github", "a" * 41) == "mcp_github_" + "a" * 41
    cut = export_name("github", "a" * 42)
    assert len(cut) == 52 and cut.startswith("mcp_github_aaaa") and cut != ("mcp_github_" + "a" * 42)[:52]
    east, west = (export_name("github", f"generate_quarterly_revenue_report_summary_for_region_{r}") for r in ("east", "west"))
    assert east != west and east[:43] == west[:43] and len(east) == len(west) == 52


def test_dmcp4_schema_prose_loses_invisible_characters_and_data_is_kept() -> None:
    """D-MCP-4: a schema's descriptions and titles reach the model as prose; enum values and patterns are matched."""
    from harness_core.mcp_hub.names import export_schema, invisible_char

    hidden = "".join(chr(0xE0000 + ord(c)) for c in "leak")
    schema = {
        "type": "object",
        "title": "Args\u200b",
        "properties": {
            "q": {"type": "string", "description": "query" + hidden, "enum": ["a\u200bb"], "pattern": "^x\u00ad$"},
            "items": {"type": "array", "items": [{"description": "one\ufe0f"}]},
        },
    }
    assert export_schema(schema) == {
        "type": "object",
        "title": "Args",
        "properties": {
            "q": {"type": "string", "description": "query", "enum": ["a\u200bb"], "pattern": "^x\u00ad$"},
            "items": {"type": "array", "items": [{"description": "one"}]},
        },
    }
    assert invisible_char("plain text") is None
    assert invisible_char("x" + hidden) == chr(0xE0000 + ord("l"))
    assert invisible_char("x\u2061") == "\u2061"
