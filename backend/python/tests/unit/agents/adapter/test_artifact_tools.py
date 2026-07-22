"""Background artifact pipeline (`image_generator`, `coding_sandbox`,
`database_sandbox`) survives the adapter layer unmodified — Phase 3's
"Special Tool Categories #3" note: these tools call `register_task()`
directly, and the adapter deliberately does NOT special-case them (see
`tool_adapter.py`'s module docstring). This suite verifies a tool's
fire-and-forget `register_task()` side effect still lands in the shared
`conversation_tasks` registry when executed through `PipesHubToolAdapter`/
`PipesHubStructuredToolAdapter`, and that `RespondPipeline`'s draining path
(`stream_llm_response_with_tools` -> `await_and_collect_results`) can still
retrieve it afterward."""

from __future__ import annotations

import asyncio

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.agents.agent_loop.tool_adapter import (
    PipesHubStructuredToolAdapter,
    PipesHubToolAdapter,
)
from app.agents.tools.models import Tool as RegistryTool
from app.utils import conversation_tasks
from tests.unit.agents.adapter.conftest import make_context


class _NoArgs(BaseModel):
    pass


def _upload_artifact_and_register(conversation_id: str) -> str:
    """Stand-in for `image_generator`'s pattern: kick off a background
    upload task and return immediately, leaving the task for the streaming
    layer to await later via `await_and_collect_results`."""

    async def _upload() -> dict[str, str]:
        await asyncio.sleep(0)
        return {"url": "https://blob.example.com/artifact.png"}

    task = asyncio.ensure_future(_upload())
    conversation_tasks.register_task(conversation_id, task)
    return "Generating image in the background..."


class TestBackgroundTaskSurvivesRegistryToolAdapter:
    async def test_registry_tool_registers_task_that_can_be_drained(self) -> None:
        context = make_context()
        conversation_id = "conv-registry-1"

        registry_tool = RegistryTool(
            app_name="image_generator", tool_name="generate",
            description="Generate an image", function=_upload_artifact_and_register,
        )
        adapter = PipesHubToolAdapter(
            registry_tool, "image_generator", "generate", context_ref=lambda: context
        )

        output = await adapter.execute(conversation_id=conversation_id)

        assert output.success is True
        assert "background" in output.data.lower()

        results = await conversation_tasks.await_and_collect_results(conversation_id)
        assert results == [{"url": "https://blob.example.com/artifact.png"}]

    async def test_draining_an_unregistered_conversation_returns_empty(self) -> None:
        results = await conversation_tasks.await_and_collect_results("conv-never-used")
        assert results == []


class TestBackgroundTaskSurvivesStructuredToolAdapter:
    async def test_dynamic_tool_registers_task_that_can_be_drained(self) -> None:
        conversation_id = "conv-dynamic-1"

        async def _coro(**kwargs) -> str:
            return _upload_artifact_and_register(conversation_id)

        structured_tool = StructuredTool.from_function(
            name="generate_image", description="Generate an image",
            args_schema=_NoArgs, coroutine=_coro,
        )
        adapter = PipesHubStructuredToolAdapter(structured_tool, "dynamic", "generate_image")

        output = await adapter.execute()

        assert output.success is True

        results = await conversation_tasks.await_and_collect_results(conversation_id)
        assert results == [{"url": "https://blob.example.com/artifact.png"}]

    async def test_task_registered_even_when_multiple_tools_run_for_same_conversation(self) -> None:
        conversation_id = "conv-multi-1"

        async def _first(**kwargs) -> str:
            return _upload_artifact_and_register(conversation_id)

        async def _second(**kwargs) -> str:
            return _upload_artifact_and_register(conversation_id)

        tool_a = PipesHubStructuredToolAdapter(
            StructuredTool.from_function(name="a", description="a", args_schema=_NoArgs, coroutine=_first),
            "dynamic", "a",
        )
        tool_b = PipesHubStructuredToolAdapter(
            StructuredTool.from_function(name="b", description="b", args_schema=_NoArgs, coroutine=_second),
            "dynamic", "b",
        )

        await tool_a.execute()
        await tool_b.execute()

        results = await conversation_tasks.await_and_collect_results(conversation_id)
        assert len(results) == 2
