"""End to end through a real Agent: search, fetch the record, answer.

The tools here register their results exactly as the knowledge tools do, and
the hooks are the factory's own. What matters is the third model request:
the fetched record's blocks must reach the model once, and the path from a
tool's output to the ToolMessage must keep the text byte-identical, or the
manifests never match and nothing is removed.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall, ToolMessage
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.modules.retrieval.context.manifest import (
    BlockKey,
    ContentManifest,
    ManifestSource,
    manifest_registry,
)
from app.modules.retrieval.context.renderer import render_knowledge
from app.utils.chat_helpers import CitationRefMapper
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport
from tests.unit.agents.agent_loop.hooks.test_duplicate_blocks import RECORDS, _text


@pytest.fixture(autouse=True)
def _skills_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")


class _KnowledgeTool(Tool):
    def __init__(self, name: str, result: str) -> None:
        self._name = name
        self._result = result

    @property
    def name(self) -> str:
        return self._name

    @property
    def short_description(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def path(self) -> str:
        return f"/tools/knowledgegraph/{self._name}"

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        return ToolOutput(success=True, data=self._result)


def _results(tool_state: dict[str, Any]) -> tuple[str, str]:
    """A search showing record A's blocks 0 and 2 and record B's block 1,
    and a fetch of record A, registered as the real tools register them."""
    registry = manifest_registry(tool_state)
    rendered = render_knowledge(
        [_text("a", 0), _text("a", 2), _text("b", 1)], RECORDS,
        ref_mapper=CitationRefMapper(), is_multimodal_llm=False,
    )
    header = "Top 3 blocks from 2 records.\n\n"
    search = header + rendered.text
    registry.register(search, rendered.manifest.shifted(len(header)))

    fetch = "<record>\nRecord ID: id-a\n" + "".join(
        f"[ref{i}] a block {i} text\n" for i in range(4)
    ) + "</record>\n\nCite facts from the above using each block's `[refN]` id."
    registry.register(fetch, ContentManifest(
        ManifestSource.FETCH, (), (), shown_blocks=frozenset(BlockKey("a", i) for i in range(4)),
    ))
    return search, fetch


@pytest.mark.asyncio
async def test_the_model_reads_a_fetched_records_blocks_once() -> None:
    context = make_context()
    search_text, fetch_text = _results(context.tool_state)

    tools = ToolRegistry()
    tools.register_tool(TaskCompleteTool())
    tools.register_tool(_KnowledgeTool("knowledgegraph__search", search_text))
    tools.register_tool(_KnowledgeTool("knowledgegraph__fetch_record", fetch_text))
    transport = ScriptedTransport()
    transport.add_tool_call(ToolCall(id="c1", name="knowledgegraph__search", arguments={}))
    transport.add_tool_call(ToolCall(id="c2", name="knowledgegraph__fetch_record", arguments={}))
    transport.add_tool_call(ToolCall(id="c3", name="task_complete", arguments={"output": "done"}))
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    runtime = AgentRuntime(
        transport_registry=transports, tool_registry=tools,
        hooks=PipesHubAgentFactory._build_hooks(context),
    )
    agent = Agent(AgentSpec(
        name="agent-under-test", system_prompt="You answer from the knowledge base.",
        model=ModelSpec(provider="scripted", model="scripted-model"), max_turns=5,
    ), runtime)

    result = await agent.run(Goal(description="What does the A report say?"))

    assert result.success is True
    third_request = [m for m in transport.calls[2]["messages"] if isinstance(m, ToolMessage)]
    sent = "\n".join(m.text for m in third_request)
    assert sent.count("a block 0 text") == 1, "only the fetch's copy"
    assert sent.count("a block 2 text") == 1
    assert "b block 1 text" in sent, "the record the fetch did not cover stays"
    assert "its 2 matching blocks are shown in a fetch_record result below" in sent
