"""QA-23 (CG-23, TSC-G8, TSC-16): tool names stay unique within a run for any set of instances.

For a random run's bindings (toolset types, each on one or more instances, each with tools; MCP servers likewise),
the names the model sees are distinct for distinct (type, instance, tool), and every name of a shared type fits
``SCOPED_NAME_MAX`` and the export rule. Two tools under one name would call one instance's credential for the
other's request.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from harness_core.tool_identity import (
    SCOPED_NAME_MAX,
    connector_tool_name,
    scoped_mcp_tool_name,
    shared_types,
    type_key,
)

SCOPED = re.compile(
    json.loads((Path(__file__).resolve().parents[6] / "contracts/harness/vectors/tool-identity.json").read_text("utf-8"))[
        "exportRule"
    ]
)
_TYPE = st.from_regex(r"[a-z][a-z0-9_ ]{0,40}", fullmatch=True)
_INSTANCE = st.from_regex(r"[A-Za-z0-9_-]{1,64}", fullmatch=True)
_TOOL = st.from_regex(r"[A-Za-z0-9_.-]{1,128}", fullmatch=True)
_BINDINGS = st.lists(
    st.tuples(_TYPE, st.lists(_INSTANCE, min_size=1, max_size=3, unique=True), st.lists(_TOOL, min_size=1, max_size=4, unique=True)),
    min_size=1,
    max_size=5,
    unique_by=lambda b: type_key(b[0]),
)


@given(_BINDINGS)
def test_qa23_7_connector_tool_names_are_unique_per_run(bindings: list[tuple[str, list[str], list[str]]]) -> None:
    shared = shared_types((kind, instance) for kind, instances, _ in bindings for instance in instances)
    names: dict[str, tuple[str, str, str]] = {}
    for kind, instances, tools in bindings:
        is_shared = type_key(kind) in shared
        group = kind.replace(" ", "_")
        for instance in instances:
            for tool in tools:
                name = connector_tool_name(group, instance, tool, shared=is_shared)
                if is_shared:
                    assert len(name) <= SCOPED_NAME_MAX and SCOPED.fullmatch(name), name
                # One instance's tool under a non-shared type reaches the model as today's name.
                assert names.setdefault(name, (kind, instance, tool)) == (kind, instance, tool), name


@given(st.lists(_INSTANCE, min_size=2, max_size=4, unique=True), st.lists(_TOOL, min_size=1, max_size=4, unique=True), _TYPE)
def test_qa23_7_shared_mcp_tool_names_are_unique_and_fit(instances: list[str], tools: list[str], server_type: str) -> None:
    names = {scoped_mcp_tool_name(server_type, instance, tool) for instance in instances for tool in tools}
    assert len(names) == len(instances) * len(tools)
    assert all(len(name) <= SCOPED_NAME_MAX and SCOPED.fullmatch(name) for name in names), names
