"""MW-10: the mcp SDK's ``Client`` over ``RunnerClientTransport`` (one runner session per connect)."""

from __future__ import annotations

from mcp import Client

from app.harness.mcp_runner.transport import (
    RunnerClientTransport,
    RunnerTarget,
    runner_refusal,
)
from harness_core.ids import RandomIds
from harness_core.sandbox.mcp_runner import InMemoryMCPRunner, RunnerOwnerMismatch
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.mcp_runner import (
    ECHO_COMMAND,
    MCPRunnerContractEnv,
    request,
    serve_echo,
)


async def test_mw10_list_and_call_over_the_transport_and_the_session_ends_with_the_client() -> None:
    runner = InMemoryMCPRunner({ECHO_COMMAND: serve_echo}, clock=ManualClock(), ids=RandomIds())
    target = RunnerTarget(runner, request(MCPRunnerContractEnv()))
    async with Client(RunnerClientTransport(target)) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        result = await client.call_tool("echo", {"text": "over the relay"})
        assert runner.runners()[0].sessions == 1
    assert {"echo", "env_digest", "fetch", "where"} <= names
    assert result.content[0].text == "over the relay"  # type: ignore[union-attr]
    assert runner.runners()[0].sessions == 0
    _ = await runner.stop_all()


def test_mw10_a_wrapped_refusal_is_found() -> None:
    refused = RunnerOwnerMismatch("no")
    try:
        try:
            raise refused
        except RunnerOwnerMismatch as inner:
            raise RuntimeError("Client failed to connect") from inner
    except RuntimeError as outer:
        assert runner_refusal(outer) is refused
        assert runner_refusal(ExceptionGroup("g", [ValueError("x"), outer])) is refused
    assert runner_refusal(ValueError("plain")) is None
