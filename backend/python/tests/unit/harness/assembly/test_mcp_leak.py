"""L0.4 X-1 leak test: durable attempts against a real stdio MCP server leave no process behind."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import psutil
import pytest

from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.mcp.client import MCPClientManager
from app.agents.mcp.models import MCPAuthMode, MCPServerTemplate, MCPTransport
from app.agents.mcp.registry import get_mcp_registry
from app.harness.assembly import teardown
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.teardown import MCP_SESSIONS_KEY
from harness_core.driver import RunDriver
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
    from collections.abc import Iterator

ATTEMPTS = 100
SERVER = str(Path(__file__).with_name("fake_mcp_stdio_server.py"))
MCP_PRS = f"mcp@{w.MCP}/list_prs"


class _Seen:
    def __init__(self) -> None:
        self.closed: list[Any] = []
        self.sessions = 0


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Seen]:
    # Main's STDIO policy (#3887) spawns a catalog instance's template program, never a stored one.
    registry = get_mcp_registry()
    registry.auto_discover_templates()
    monkeypatch.setitem(
        registry._templates,  # pyright: ignore[reportPrivateUsage]
        "github",
        MCPServerTemplate(
            type_id="github", display_name="GitHub", description="Fake GitHub server",
            transport=MCPTransport.STDIO, default_auth_mode=MCPAuthMode.NONE,
            supported_auth_modes=[MCPAuthMode.NONE], command=sys.executable, args=[SERVER],
        ),
    )

    async def stdio_instance(instance_id: str, config_service: object, *_: object) -> dict[str, Any]:
        return {
            "_id": instance_id, "orgId": w.ORG, "createdBy": w.ACTOR, "name": "GitHubMCP",
            "typeId": "github", "transport": "stdio", "authMode": "none",
            "command": sys.executable, "args": [SERVER], "createdAt": 0, "updatedAt": 0,
        }

    record = _Seen()
    real_close, real_open = teardown.close_environment, MCPClientManager.open

    async def recording_close(context: Any, log: Any) -> None:  # noqa: ANN401
        record.closed.append(context)
        await real_close(context, log)

    async def counting_open(self: MCPClientManager) -> Any:  # noqa: ANN401
        record.sessions += 1
        return await real_open(self)

    with (
        w.deployment(),
        # Real discovery and real sessions: both spawn the server.
        patch.object(MCPSessionManager, "tools", MCPSessionManager.tools),
        patch("app.edition_config.get_mcp_instance_resolved", stdio_instance),
        patch("app.harness.assembly.assembler.close_environment", recording_close),
        patch.object(MCPClientManager, "open", counting_open),
    ):
        yield record


def _servers() -> list[psutil.Process]:
    found: list[psutil.Process] = []
    for child in psutil.Process().children(recursive=True):
        try:
            if SERVER in child.cmdline() and child.status() != psutil.STATUS_ZOMBIE:
                found.append(child)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return found


async def _settled() -> list[psutil.Process]:
    """Process exit after close is asynchronous; give it a bounded moment."""
    for _ in range(100):
        alive = _servers()
        if not alive:
            return alive
        await asyncio.sleep(0.05)
    return _servers()


def _port() -> ScriptedModelPort:
    calls = Reply(tool_calls=[ToolCallSpec(name="mcp_github_list_prs", args={"repo": "a"})])
    script = ModelScript(id="t", turns=[Turn(reply=calls), Turn(reply=Reply(text="done"))])
    return ScriptedModelPort(ScriptedModel(script))


async def _attempt(run_id: str) -> str:
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock, lease_ms=30_000)
    spec = w.pinned_spec(tool_ids=[MCP_PRS], mcp=[w.MCP])
    journal.admit(run_id, org_id=w.ORG, session_id="ses-1", spec=spec, input_text="list prs")
    inner = PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(_port()))
    driver = RunDriver(journal=journal, assembler=w.ClaimAssembler(inner), clock=clock)
    done = await driver.run_claimed(await w.claim_next(journal, run_id))
    return done.status


@pytest.mark.timeout(600)
async def test_l4_4_no_mcp_process_or_session_survives_repeated_attempts(seen: _Seen) -> None:
    assert _servers() == []
    for n in range(ATTEMPTS):
        assert await _attempt("run-1") == "completed", f"attempt {n}"
        assert await _settled() == [], f"a stdio MCP server outlived attempt {n}"
    assert seen.sessions == ATTEMPTS
    assert len(seen.closed) == ATTEMPTS
    assert all(not ctx.tool_state.get(MCP_SESSIONS_KEY) for ctx in seen.closed)
