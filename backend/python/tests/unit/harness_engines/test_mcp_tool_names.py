"""NV-4 (P2.8): every exported MCP tool name reaches each engine's model intact, or the engine's grant check refuses it.

The engines' ``exposed_tool_name`` is the code that turns a granted name into the function name a model sees.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from harness_core.engines.policy import check_grant_names
from harness_engines.claude_code import launch as claude_code_launch
from harness_engines.claude_code.adapter import ClaudeCodeAdapter
from harness_engines.codex import launch as codex_launch
from harness_engines.codex.adapter import CodexAdapter
from harness_engines.opencode import launch as opencode_launch
from harness_engines.opencode.adapter import OpencodeAdapter

VECTORS: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[5] / "contracts/harness/vectors/mcp-tool-names.json").read_text("utf-8")
)
PROVIDER_NAME = re.compile(VECTORS["providerRule"])


ENGINES = [
    ("opencode", opencode_launch.exposed_tool_name, OpencodeAdapter().capabilities()),
    ("claude_code", claude_code_launch.exposed_tool_name, ClaudeCodeAdapter().capabilities()),
    ("codex", codex_launch.exposed_tool_name, CodexAdapter().capabilities()),
]


@pytest.mark.parametrize(("engine", "exposed", "capabilities"), ENGINES, ids=[e[0] for e in ENGINES])
@pytest.mark.parametrize("case", VECTORS["export"], ids=lambda c: c["exported"][:30])
def test_nv4_every_engine_carries_every_exported_name(
    engine: str, exposed: Any, capabilities: Any, case: dict[str, Any]  # noqa: ANN401 - parametrized
) -> None:
    """MCPFIX F3: an exported name with any engine's prefix is still a provider's function name, so a tool an
    admin approved never refuses an engine run (it used to, between 53 and 64 characters)."""
    del engine
    assert PROVIDER_NAME.fullmatch(exposed(case["exported"]))
    check_grant_names(capabilities, [case["exported"]])


SCOPED: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[5] / "contracts/harness/vectors/tool-identity.json").read_text("utf-8")
)
SCOPED_NAMES = [c["exported"] for c in SCOPED["connector"] if c["shared"]] + [c["exported"] for c in SCOPED["mcp"]]


@pytest.mark.parametrize(("engine", "exposed", "capabilities"), ENGINES, ids=[e[0] for e in ENGINES])
@pytest.mark.parametrize("name", SCOPED_NAMES, ids=lambda n: n[:30])
def test_ne1_every_engine_carries_every_instance_scoped_name(
    engine: str, exposed: Any, capabilities: Any, name: str  # noqa: ANN401 - parametrized
) -> None:
    """P2.2 NE-1: a name scoped to an instance never makes a grant refusable."""
    del engine
    assert PROVIDER_NAME.fullmatch(exposed(name))
    check_grant_names(capabilities, [name])
