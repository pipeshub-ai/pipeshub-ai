"""NE-1 over the real toolset registry (P2.2): every connector tool, scoped to an instance, reaches every engine."""

from __future__ import annotations

import re

import pytest

from app.agents.agent_loop.tool_loader import _infer_path_prefix
from harness_core.engines.policy import check_grant_names
from harness_core.tool_identity import SCOPED_NAME_MAX, connector_tool_name
from harness_core.tools.decorators import TOOL_META_ATTR
from harness_engines.claude_code import launch as claude_code_launch
from harness_engines.claude_code.adapter import ClaudeCodeAdapter
from harness_engines.codex import launch as codex_launch
from harness_engines.codex.adapter import CodexAdapter
from harness_engines.opencode import launch as opencode_launch
from harness_engines.opencode.adapter import OpencodeAdapter
from tests.support.toolset_registry import discovered_toolsets

PROVIDER_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
INSTANCE = "7f3c2a10-0b6e-4d55-9a1e-3c1d2b9e8f01"


def _scoped_names() -> list[str]:
    names: list[str] = []
    for ts_name, meta in discovered_toolsets().items():
        cls = meta.get("class")
        if cls is None:
            continue
        group = _infer_path_prefix(cls, fallback_name=ts_name).rsplit("/", 1)[-1]
        for attr in dir(cls):
            tool = getattr(getattr(cls, attr, None), TOOL_META_ATTR, None)
            if tool is not None and not attr.startswith("_"):
                names.append(connector_tool_name(group, INSTANCE, tool.path.rsplit("/", 1)[-1], shared=True))
    return names


@pytest.mark.parametrize(
    ("exposed", "capabilities"),
    [
        (opencode_launch.exposed_tool_name, OpencodeAdapter().capabilities()),
        (claude_code_launch.exposed_tool_name, ClaudeCodeAdapter().capabilities()),
        (codex_launch.exposed_tool_name, CodexAdapter().capabilities()),
    ],
    ids=["opencode", "claude_code", "codex"],
)
def test_ne1_every_registered_connector_tool_scoped_to_an_instance(exposed: object, capabilities: object) -> None:
    names = _scoped_names()
    assert len(names) > 100
    assert all(len(n) <= SCOPED_NAME_MAX for n in names)
    assert len(set(names)) == len(names)
    assert all(PROVIDER_NAME.fullmatch(exposed(n)) for n in names)  # type: ignore[operator]
    check_grant_names(capabilities, names)  # type: ignore[arg-type]
