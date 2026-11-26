"""KN-06 (C3.22, C5.01's ``/pipeshub/knowledge`` row): an over-cap knowledge result is kept whole in the sandbox.

Past the engine's result cap, the gateway writes a knowledge tool's whole result to ``/pipeshub/knowledge/<id>.md``
(the session folder's ``knowledge/``, read-only to the engine) and the engine reads its opening and the path; any other
tool's result is still cut with the ``ph`` hint, and ``ph`` itself still reads every result whole.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from harness_core.engines.controller import (
    _SavingEndpoint,  # pyright: ignore[reportPrivateUsage]
)
from harness_core.engines.tool_folder import Shelf, ToolFolder
from harness_core.gateway import McpResult
from harness_core.gateway.result_cap import (
    ENGINE_RESULT_CAP_CHARS,
    SHELF_PREVIEW_CHARS,
    bound_engine_result,
    shelf_name,
)
from harness_core.logs import HarnessLog
from harness_core.sandbox.tar import read_tar_stream
from harness_core.testing.contracts.tool_endpoint import build_endpoint, claims_for
from harness_engines.pipeshub import (
    KNOWLEDGE_PATH,
    SESSION_DIR,
    knowledge_tool,
    pipeshub_tool_folder,
)
from tests.unit.harness.gateway.support import GatewayServer, McpClientEndpoint
from tests.unit.harness.gateway.test_ph_contract import PhEndpoint

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

LONG = "\n".join(f"line {i}: " + "x" * 80 for i in range(1_000))
LOG = HarnessLog.named("tests.wp12")


class SeedingSandbox:
    """The one sandbox call shelving makes: ``seed_tar`` into the read-only session folder."""

    def __init__(self, *, fail: bool = False) -> None:
        self.files: dict[str, bytes] = {}
        self.fail = fail

    async def seed_tar(self, dest: str, stream: AsyncIterator[bytes]) -> None:
        if self.fail:
            raise OSError("the helper container could not start")
        for path, content in (await read_tar_stream(stream)).items():
            self.files[f"{dest}/{path}"] = content


def _result(text: str, *, invocation: str = "inv-1") -> McpResult:
    return McpResult(text=text, is_error=False, disposition="executed", retryable=False, invocation_id=invocation)


def test_kn06_the_pipeshub_folder_shelves_only_knowledge_results() -> None:
    folder = pipeshub_tool_folder()
    assert folder.shelf is not None and folder.shelf.path == KNOWLEDGE_PATH == "/pipeshub/knowledge"
    assert knowledge_tool("knowledgegraph__search") and knowledge_tool("knowledgegraph__fetch_record")
    assert not knowledge_tool("jira__search_issues") and not knowledge_tool("search_tools")


async def test_kn06_an_over_cap_knowledge_result_is_written_whole_and_the_engine_reads_its_opening() -> None:
    sandbox = SeedingSandbox()
    endpoint = _SavingEndpoint(build_endpoint().endpoint)
    endpoint.shelf = (pipeshub_tool_folder(), sandbox, LOG)  # type: ignore[assignment]
    shelved = await bound_engine_result(endpoint, _result(LONG), tool="knowledgegraph__search", arguments={"query": "q"})
    name = shelf_name("knowledgegraph__search", "inv-1", LONG)
    path = f"{KNOWLEDGE_PATH}/{name}.md"
    assert sandbox.files == {f"{SESSION_DIR}/knowledge/{name}.md": LONG.encode()}
    assert path in shelved.text and f"{len(LONG)} characters" in shelved.text
    assert shelved.text.startswith("line 0: ") and len(shelved.text) < SHELF_PREVIEW_CHARS + 400
    # A short result, an error and another tool's long result are as before.
    short = _result("PP-1")
    assert await bound_engine_result(endpoint, short, tool="knowledgegraph__search", arguments={}) is short
    jira = await bound_engine_result(endpoint, _result(LONG), tool="jira__search", arguments={"jql": "x"})
    assert "ph call jira__search" in jira.text and len(sandbox.files) == 1


async def test_kn06_a_knowledge_call_through_call_tool_is_shelved_as_the_tool_it_calls() -> None:
    sandbox = SeedingSandbox()
    endpoint = _SavingEndpoint(build_endpoint().endpoint)
    endpoint.shelf = (pipeshub_tool_folder(), sandbox, LOG)  # type: ignore[assignment]
    wrapped = {"name": "knowledgegraph__search", "arguments": {"query": "q"}}
    shelved = await bound_engine_result(endpoint, _result(LONG), tool="call_tool", arguments=wrapped)
    assert KNOWLEDGE_PATH in shelved.text and len(sandbox.files) == 1


async def test_kn06_a_shelf_that_cannot_be_written_cuts_the_result_instead() -> None:
    endpoint = _SavingEndpoint(build_endpoint().endpoint)
    endpoint.shelf = (pipeshub_tool_folder(), SeedingSandbox(fail=True), LOG)  # type: ignore[assignment]
    cut = await bound_engine_result(endpoint, _result(LONG), tool="knowledgegraph__search", arguments={"query": "q"})
    assert KNOWLEDGE_PATH not in cut.text and "ph call knowledgegraph__search" in cut.text
    assert len(cut.text) < ENGINE_RESULT_CAP_CHARS + 800


async def test_kn06_over_the_gateway_the_engine_reads_the_shelf_and_ph_reads_the_result_whole() -> None:
    world = build_endpoint()
    sandbox = SeedingSandbox()
    endpoint = _SavingEndpoint(world.endpoint)
    # The contract world's read tool, shelved as a knowledge tool is.
    endpoint.shelf = (ToolFolder(SESSION_DIR, lambda *_: {}, shelf=Shelf("knowledge", KNOWLEDGE_PATH, lambda n: n == "lookup")), sandbox, LOG)  # type: ignore[assignment]
    claims = claims_for(world.endpoint.binding)
    async with GatewayServer().running() as server:
        async with server.gateway.serving(endpoint, world.endpoint.binding) as key:  # type: ignore[arg-type]
            through_mcp = await McpClientEndpoint(server, key).call_tool(claims, "lookup", {"q": LONG})
            whole = await PhEndpoint(server, key).call_tool(claims, "lookup", {"q": LONG})
    (path, content), = sandbox.files.items()
    assert content.decode() == f"lookup:{LONG}"
    assert f"{KNOWLEDGE_PATH}/{path.rsplit('/', 1)[1]}" in through_mcp.text and len(through_mcp.text) < SHELF_PREVIEW_CHARS + 400
    assert whole.text == f"lookup:{LONG}"


async def test_kn06_the_controller_shelves_into_the_turns_own_sandbox() -> None:
    """The controller points the run's endpoint at the turn's sandbox once the folder is written there."""
    from tests.unit.harness_core.engines.test_controller import (
        IMAGE,
        WatchingAdapter,
        World,
        task,
    )

    shelved: list[McpResult] = []

    class Shelving(WatchingAdapter):
        async def open(self, sandbox: Any, launch: Any, restore: Any, *, correlation: Any = None) -> Any:  # noqa: ANN401
            ((served, _),) = world.host._mounts.values()  # pyright: ignore[reportPrivateUsage]
            shelved.append(
                await bound_engine_result(served, _result(LONG), tool="knowledgegraph__search", arguments={"query": "q"})
            )
            return await super().open(sandbox, launch, restore, correlation=correlation)

    world = World(
        adapter=Shelving(),
        config_overrides={"tool_folder": pipeshub_tool_folder()},
        image_files={IMAGE: {"/pipeshub/VERSION": b"1\n"}},
    )
    with contextlib.suppress(asyncio.CancelledError):
        outcome = await world.controller.run_turn(world.run(), task(0, "say ok"))
    assert outcome.reason == "completed"
    (result,) = shelved
    name = shelf_name("knowledgegraph__search", "inv-1", LONG)
    assert f"{KNOWLEDGE_PATH}/{name}.md" in result.text
    seen = world.adapter.files_at_open[0]  # pyright: ignore[reportAttributeAccessIssue]
    assert seen[f"{SESSION_DIR}/knowledge/{name}.md"] == LONG.encode()
