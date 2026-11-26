"""TD-7 (C5.01): each release engine offers its model what the agent's tool-disclosure mode says, end to end.

Each cell is one durable engine run on E1's real lane (``matrix/``: Node's journal server, the real egress proxy, the
scripted model behind E3, the engine's release image under the Docker broker), assembled by the production
``PipesHubEngineAssembler``. The run's policy answers the agent's ``toolDisclosure.mode`` (TD-4 holds that the pin
gives it); everything after is production: ``engine_disclosure`` decides what is deferred, the gateway's
``tools/list`` leaves it out and adds ``search_tools``/``call_tool``, and the engine lists the rest to its model.
Eager lists the Jira tool; lazy lists the knowledge tools and finds Jira with ``search_tools``; filesystem lists the
two disclosure tools only. A lazy run still calls the Jira tool, through ``call_tool``, under its own name.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app.harness.run_policy.pinned import PinnedRunPolicy
from app.harness.run_policy.stored import StoredRunPolicy
from app.harness.unpinned import UnpinnedRunPolicy
from tests.unit.harness.engines.matrix.assembly import JIRA_READ, matrix_spec
from tests.unit.harness.engines.matrix.fixtures import (  # noqa: F401  (the lane's fixtures)
    echo_template_fixture,  # pyright: ignore[reportUnusedImport]
    matrix_fixture,  # pyright: ignore[reportUnusedImport]
)
from tests.unit.harness.engines.real_stack import engine_image, lane_available

if TYPE_CHECKING:
    from tests.unit.harness.engines.matrix.lane import MatrixLane

pytestmark = [
    pytest.mark.docker,
    pytest.mark.timeout(1800, method="thread"),
    pytest.mark.asyncio(loop_scope="module"),
]

ENGINES = ("opencode", "codex", "claude_code")
JIRA, SEARCH, CALL, KNOWLEDGE = "jira__search_issues", "search_tools", "call_tool", "knowledgegraph__search"


def _engine(kind: str) -> str:
    available, reason = lane_available(engine_image(kind))
    if not available:
        pytest.skip(reason)
    return kind


def _has(tools: set[str], name: str) -> bool:
    return any(t == name or t.endswith((f"_{name}", f"__{name}")) for t in tools)


@pytest.mark.parametrize("mode", ["eager", "lazy", "filesystem"])
@pytest.mark.parametrize("engine", ENGINES)
async def test_td7_each_engine_offers_what_the_agents_mode_says(matrix: MatrixLane, engine: str, mode: str) -> None:
    org = matrix.org
    call = {"name": JIRA, "arguments": {"jql": "project = OPS"}}
    prompt = f"tool {CALL} {json.dumps(call)}\nsay listed" if mode != "eager" else "say listed"
    jira = matrix.jira
    assert jira is not None
    before = len(jira.seen)
    with (
        patch.object(PinnedRunPolicy, "tool_disclosure", lambda self: mode),
        patch.object(StoredRunPolicy, "tool_disclosure", lambda self: mode),
        patch.object(UnpinnedRunPolicy, "tool_disclosure", lambda self: mode),
    ):
        result = await matrix.run(prompt, matrix_spec(org, kind=_engine(engine), tools=(JIRA_READ,)))
    assert result.status == "completed", (result.status, result.types())
    tools = set(result.model_calls[0].tools)
    if mode == "eager":
        assert _has(tools, JIRA) and _has(tools, KNOWLEDGE), sorted(tools)
        assert not _has(tools, SEARCH) and not _has(tools, CALL), sorted(tools)
        return
    assert _has(tools, SEARCH) and _has(tools, CALL), sorted(tools)
    assert not _has(tools, JIRA), sorted(tools)
    assert _has(tools, KNOWLEDGE) == (mode == "lazy"), sorted(tools)
    # The deferred tool still runs, with the toolset's credential, and answers through call_tool.
    assert len(jira.seen) > before
    assert any("OPS-1" in r for r in result.tool_results()), result.tool_results()
