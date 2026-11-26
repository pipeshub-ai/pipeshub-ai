"""L0.4 X-1, X-2: every exit of a durable attempt releases its MCP sessions and sandboxes."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.harness.assembly import teardown
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.teardown import MCP_SESSIONS_KEY, close_environment
from app.harness.assembly.tool_sources import default_sources
from harness_core.driver import CapabilityLost, RunDriver, RunRefused
from harness_core.failpoints import Raise, registry
from harness_core.logs import HarnessLog
from harness_core.testing import (
    ManualClock,
    ModelScript,
    Reply,
    ScriptedModel,
    ToolCallSpec,
    Turn,
)
from harness_core.testing.durable import ScriptedModelPort

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from app.harness.assembly.context import RunContext
    from app.harness.assembly.tool_environment import ToolEnvironment
    from harness_core.tools.registry import ToolRegistry

MCP_PRS = f"mcp@{w.MCP}/list_prs"
CALL_PRS = Reply(tool_calls=[ToolCallSpec(name="mcp_github_list_prs", args={"repo": "a"})])


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    with w.deployment():
        yield
    registry.reset()


class FakeSessions:
    """Stands in for ``MCPClientManager``: what a real stdio session would leave running."""

    def __init__(self) -> None:
        self.live: set[object] = set()
        self.opened = 0
        self.on_call: Callable[[], Awaitable[None]] | None = None
        sessions = self

        class _Manager:
            # Main's client (#4003): a session tells whether it is open and whether its server's tools changed.
            tools_changed = False

            def __init__(
                self, config: object, env: object = None, headers: object = None, runner: object = None,
            ) -> None:
                pass

            @property
            def is_open(self) -> bool:
                return self in sessions.live

            async def open(self) -> None:
                sessions.live.add(self)
                sessions.opened += 1

            async def call_tool_in_session(self, name: str, arguments: dict[str, Any], **_: object) -> object:
                if sessions.on_call is not None:
                    await sessions.on_call()
                return SimpleNamespace(is_error=False, data="pr-1")

            async def aclose(self) -> None:
                sessions.live.discard(self)

        self.manager = _Manager


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> FakeSessions:
    fake = FakeSessions()
    monkeypatch.setattr("app.agents.agent_loop.mcp_session.MCPClientManager", fake.manager)
    return fake


def _port(*replies: Reply) -> ScriptedModelPort:
    return ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=r) for r in replies])))


def _driver(
    port: ScriptedModelPort, clock: ManualClock | None = None
) -> tuple[RunDriver, w.PipesHubInMemoryJournal]:
    clock = clock or ManualClock()
    journal = w.pipeshub_journal(clock=clock, lease_ms=30_000)
    spec = w.pinned_spec(tool_ids=[MCP_PRS], mcp=[w.MCP])
    journal.admit("run-1", org_id=w.ORG, session_id="ses-1", spec=spec, input_text="list prs")
    inner = PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port))
    return RunDriver(journal=journal, assembler=w.ClaimAssembler(inner), clock=clock), journal


async def test_l4_1_a_completed_attempt_closes_its_mcp_session(sessions: FakeSessions) -> None:
    driver, journal = _driver(_port(CALL_PRS, Reply(text="done")))
    done = await driver.run_claimed(await w.claim_next(journal))
    assert done.status == "completed"
    assert sessions.opened == 1
    assert sessions.live == set()


class _Died(BaseException):
    pass


async def test_l4_2_an_attempt_that_fails_after_the_call_closes_its_session(sessions: FakeSessions) -> None:
    driver, journal = _driver(_port(CALL_PRS, Reply(text="never")))
    registry.arm("worker.tool.after_effect", Raise(RuntimeError))
    failed = await driver.run_claimed(await w.claim_next(journal))
    assert failed.status != "completed"
    assert sessions.opened == 1
    assert sessions.live == set()


async def test_l4_2_an_attempt_that_crashes_after_the_call_closes_its_session(sessions: FakeSessions) -> None:
    driver, journal = _driver(_port(CALL_PRS, Reply(text="never")))
    registry.arm("worker.tool.after_effect", Raise(_Died))
    with pytest.raises(_Died):
        _ = await driver.run_claimed(await w.claim_next(journal))
    assert sessions.opened == 1
    assert sessions.live == set()


async def test_l4_2_a_cancelled_attempt_closes_its_session(sessions: FakeSessions) -> None:
    calling = asyncio.Event()

    async def hang() -> None:
        calling.set()
        await asyncio.Event().wait()

    sessions.on_call = hang
    driver, journal = _driver(_port(CALL_PRS, Reply(text="never")))
    attempt = asyncio.create_task(driver.run_claimed(await w.claim_next(journal)))
    await calling.wait()
    attempt.cancel()
    with pytest.raises(asyncio.CancelledError):
        await attempt
    assert sessions.opened == 1
    assert sessions.live == set()


async def test_l4_2_a_drained_attempt_yields_and_closes_its_session(sessions: FakeSessions) -> None:
    driver, journal = _driver(_port(CALL_PRS, Reply(text="never")))

    async def drain() -> None:
        driver.request_drain()

    sessions.on_call = drain
    yielded = await driver.run_claimed(await w.claim_next(journal))
    assert (yielded.status, yielded.reason) == ("yielded", "drain")
    assert sessions.opened == 1
    assert sessions.live == set()


async def test_l4_2_an_attempt_that_loses_a_capability_when_resumed_is_closed(
    sessions: FakeSessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")  # listed at the claim, as D-LAZY-4 keeps it here
    closes: list[object] = []
    real = teardown.close_environment

    async def spy(context: object, log: HarnessLog) -> None:
        closes.append(context)
        await real(context, log)  # type: ignore[arg-type]

    monkeypatch.setattr("app.harness.assembly.assembler.close_environment", spy)
    clock = ManualClock()
    driver, journal = _driver(_port(CALL_PRS, Reply(text="never")), clock)
    registry.arm("worker.model.after_response_commit", Raise(_Died))
    with pytest.raises(_Died):
        _ = await driver.run_claimed(await w.claim_next(journal))
    registry.reset()
    _ = clock.advance(31)
    _ = journal.sweep()
    changed = w.MCP_TOOLS[w.MCP][0].model_copy(update={"description": "changed"})
    monkeypatch.setitem(w.MCP_TOOLS, w.MCP, [changed])
    lost = await driver.run_claimed(await w.claim_next(journal))
    assert (lost.status, lost.reason) == ("yielded", "capability_wait")
    assert len(closes) == 2


class _OpensThenFails:
    """A source that opens an MCP session while loading, then the build fails."""

    name = "opens_then_fails"

    def __init__(self, sessions: FakeSessions, error: BaseException) -> None:
        self._sessions = sessions
        self._error = error

    async def load(self, env: ToolEnvironment, run: RunContext, registry: ToolRegistry) -> list[Any]:
        manager = self._sessions.manager(None)
        await manager.open()
        env.context.tool_state.setdefault(MCP_SESSIONS_KEY, {})["inst"] = manager
        raise self._error

    async def observe(self, registry: ToolRegistry) -> dict[str, Any]:
        return {}


@pytest.mark.parametrize(
    "error",
    [
        CapabilityLost(missing=["mcp:inst"]),
        RunRefused("agent_not_found"),
        ConnectionError("graph down"),
        asyncio.CancelledError(),
    ],
    ids=["capability_lost", "refused", "error", "cancelled"],
)
async def test_l4_3_a_build_that_fails_after_the_sources_loaded_closes_what_they_opened(
    sessions: FakeSessions, error: BaseException
) -> None:
    assembler = PipesHubRuntimeAssembler(
        w.Services(),
        model_factory=w.model_factory(_port(Reply(text="x"))),
        sources=lambda: [*default_sources(), _OpensThenFails(sessions, error)],
    )
    with pytest.raises(type(error)):
        _ = await assembler.build(w.claim(w.pinned_spec(tool_ids=[])))
    assert sessions.opened == 1
    assert sessions.live == set()


def _context(sandbox_manager: object = None) -> Any:  # noqa: ANN401
    return SimpleNamespace(
        tool_state={}, sandbox_manager=sandbox_manager, logger=None, close_credentials=AsyncMock(return_value=0)
    )


async def test_l4_5_close_destroys_the_sandboxes() -> None:
    manager = MagicMock(destroy_all=AsyncMock())
    await close_environment(_context(sandbox_manager=manager), HarnessLog.named("t"))
    manager.destroy_all.assert_awaited_once()


async def test_l4_5_the_assembled_runtime_destroys_its_sandboxes(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = MagicMock(destroy_all=AsyncMock())

    class _Sandbox:
        name = "sandbox"

        async def load(self, env: ToolEnvironment, run: RunContext, registry: ToolRegistry) -> list[Any]:
            env.context.sandbox_manager = manager
            return []

        async def observe(self, registry: ToolRegistry) -> dict[str, Any]:
            return {}

    assembler = PipesHubRuntimeAssembler(
        w.Services(),
        model_factory=w.model_factory(_port(Reply(text="x"))),
        sources=lambda: [*default_sources(), _Sandbox()],
    )
    runtime = await assembler.build(w.claim(w.pinned_spec(tool_ids=[])))
    assert runtime.close is not None
    await runtime.close()
    manager.destroy_all.assert_awaited_once()


async def test_l4_6_a_failing_closer_is_logged_and_the_next_still_runs(
    sessions: FakeSessions, caplog: pytest.LogCaptureFixture
) -> None:
    broken = sessions.manager(None)
    await broken.open()

    manager = MagicMock(destroy_all=AsyncMock())
    context = _context(sandbox_manager=manager)
    context.tool_state[MCP_SESSIONS_KEY] = {"inst": broken}
    caplog.set_level(logging.INFO)

    class _Boom:
        def __init__(self, ctx: object) -> None:
            pass

        async def aclose_all(self) -> None:
            raise OSError("pipe closed")

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr("app.agents.agent_loop.mcp_session.MCPSessionManager", _Boom)
        await close_environment(context, HarnessLog.named("t", runId="run-1"))
    manager.destroy_all.assert_awaited_once()
    (failed,) = [r for r in caplog.records if getattr(r, "harness_event", None) == "run.close_failed"]
    assert failed.levelno == logging.ERROR
    assert failed.harness == {"runId": "run-1", "closer": "mcp_sessions", "errorType": "OSError"}  # type: ignore[attr-defined]
    (closed,) = [r for r in caplog.records if getattr(r, "harness_event", None) == "run.closed"]
    assert closed.harness == {"runId": "run-1", "mcpSessions": 0, "sandboxManagers": 1, "credentialHandles": 0, "droppedTasks": 0, "failed": 1}  # type: ignore[attr-defined]


async def test_p511_close_drops_the_conversation_tasks_an_attempt_left() -> None:
    """P5.11 RC-F1: a marker an attempt queued and never journaled is not presented by the next attempt."""
    import asyncio

    from app.utils.conversation_tasks import pop_tasks, register_task

    async def marker() -> dict[str, Any]:
        return {"type": "artifacts", "artifacts": [{"recordId": "a1"}]}

    register_task("ses-p511", asyncio.ensure_future(marker()))
    context = _context()
    context.conversation_id = "ses-p511"
    await close_environment(context, HarnessLog.named("t"))
    assert pop_tasks("ses-p511") == []
