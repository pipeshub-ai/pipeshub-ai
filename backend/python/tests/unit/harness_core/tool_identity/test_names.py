"""TI-1..TI-5 (P2.2): tool refs and the names an instance's tools reach a model under."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from harness_core.tool_identity import (
    SCOPED_NAME_MAX,
    ToolRef,
    ToolRefInvalid,
    connector_tool_name,
    instance_tag,
    scoped_group,
    scoped_mcp_tool_name,
    scoped_tool,
    shared_types,
)

VECTORS: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts/harness/vectors/tool-identity.json").read_text("utf-8")
)
SCOPED = re.compile(VECTORS["exportRule"])
A = "7f3c2a10-0b6e-4d55-9a1e-3c1d2b9e8f01"
B = "c41e9d77-5a2b-4f08-8e6d-0a9b7c6d5e42"


@pytest.mark.parametrize("tool_id", [f"jira@{A}/search_issues", "mcp@i/a.b", "web@default/web_search"])
def test_ti1_tool_ref_round_trips(tool_id: str) -> None:
    ref = ToolRef.parse(tool_id)
    assert str(ref) == tool_id
    assert ToolRef.parse("jira@default/x").type_keyed


@pytest.mark.parametrize("bad", ["no-at", "a@b", "Jira@i/t", "jira@i/", "jira@/t", f"jira@i/{'t' * 129}", "jira@i j/t"])
def test_ti1_malformed_refs_are_refused(bad: str) -> None:
    with pytest.raises(ToolRefInvalid) as refused:
        ToolRef.parse(bad)
    assert refused.value.reason_code == "tool_ref_invalid"


def test_ti2_one_instance_keeps_todays_names() -> None:
    assert connector_tool_name("jira", A, "search_issues", shared=False) == "jira__search_issues"
    assert scoped_group("jira", A, shared=False) == "jira"


@pytest.mark.parametrize("case", VECTORS["connector"], ids=lambda c: c["exported"])
def test_ti3_ti4_connector_vectors(case: dict[str, Any]) -> None:
    name = connector_tool_name(case["group"], case["instance"], case["tool"], shared=case["shared"])
    assert name == case["exported"]
    if case["shared"]:
        assert SCOPED.fullmatch(name)
        assert instance_tag(case["instance"]) in name
        assert name.count("__") == 1


@pytest.mark.parametrize("case", VECTORS["mcp"], ids=lambda c: c["exported"])
def test_ti3_ti4_mcp_vectors(case: dict[str, Any]) -> None:
    name = scoped_mcp_tool_name(case["type"], case["instance"], case["tool"])
    assert name == case["exported"]
    assert SCOPED.fullmatch(name)


def test_ti3_two_instances_are_distinct_deterministic_and_rename_free() -> None:
    a, b = (connector_tool_name("jira", i, "search_issues", shared=True) for i in (A, B))
    assert a != b
    assert a == connector_tool_name("jira", A, "search_issues", shared=True)
    assert VECTORS["tags"] == {A: instance_tag(A), B: instance_tag(B)}
    assert scoped_mcp_tool_name("github", A, "x") != scoped_mcp_tool_name("github", B, "x")


def test_ti4_scoped_path_segments_rebuild_the_name() -> None:
    group, method = scoped_tool("google_drive_enterprise", A, "get_group_members_list")
    assert f"{group}__{method}" == connector_tool_name("google_drive_enterprise", A, "get_group_members_list", shared=True)
    assert len(f"{group}__{method}") <= SCOPED_NAME_MAX


def test_ti4_two_long_tools_of_one_instance_stay_apart() -> None:
    long = "x" * 60
    assert connector_tool_name("jira", A, long + "a", shared=True) != connector_tool_name("jira", A, long + "b", shared=True)


def test_ti5_shared_types_compare_as_the_graph_stores_names() -> None:
    pairs = [("Jira", A), ("jira", B), ("google_drive", A), ("Google Drive", A), ("slack", A)]
    assert shared_types(pairs) == frozenset({"jira"})
    assert shared_types([("jira", A), ("jira", A)]) == frozenset()
