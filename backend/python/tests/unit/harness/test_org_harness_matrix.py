"""ADR-OPS-08: with the server switch on and an org's Labs "Agent harness" off, every behaviour of the org that Python
owns is the one an install with the switch off gives; with Labs on it is the harness's. One test per item of the
ADR's table; each runs the item three ways (switch off, switch on + Labs off, switch on + Labs on)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.services.featureflag.config.config import CONFIG
from tests.support.config_service import FakeConfigurationService

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

WORLDS = ("switch off", "switch on, Labs off", "switch on, Labs on")


def _world(monkeypatch: pytest.MonkeyPatch, world: str) -> FakeConfigurationService:
    monkeypatch.setenv("AGENT_HARNESS_WORKER_ENABLED", "false" if world == "switch off" else "true")
    return FakeConfigurationService(flags={CONFIG.ENABLE_AGENT_HARNESS: world == "switch on, Labs on"})


async def _matrix(
    monkeypatch: pytest.MonkeyPatch, item: Callable[[FakeConfigurationService], Awaitable[Any]],
) -> dict[str, Any]:
    return {world: await item(_world(monkeypatch, world)) for world in WORLDS}


def _assert_matrix(answers: dict[str, Any], harness: object) -> None:
    assert answers["switch on, Labs off"] == answers["switch off"]
    assert answers["switch on, Labs on"] == harness
    assert answers["switch off"] != harness


@pytest.mark.asyncio
async def test_the_predicate_is_the_switch_and_the_orgs_labs_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.featureflag.platform_settings import is_agent_harness_on

    _assert_matrix(await _matrix(monkeypatch, is_agent_harness_on), harness=True)


@pytest.mark.asyncio
async def test_an_unreadable_setting_or_no_configuration_service_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.featureflag.platform_settings import is_agent_harness_on

    monkeypatch.setenv("AGENT_HARNESS_WORKER_ENABLED", "true")

    class Unreadable:
        async def get_config(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("store down")

    assert await is_agent_harness_on(Unreadable()) is False  # type: ignore[arg-type]
    assert await is_agent_harness_on(None) is False


@pytest.mark.asyncio
async def test_upload_tracking_writes_a_record_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.chat_attachments.uploads import uploads_tracked

    _assert_matrix(await _matrix(monkeypatch, uploads_tracked), harness=True)


@pytest.mark.asyncio
async def test_an_agent_chat_runs_the_saved_agent_not_its_latest_published_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.api.routes.agent as route

    saved = {"_key": "agent-1", "name": "draft name"}

    class Versions:
        def __init__(self, _graph: object) -> None: ...

        async def current(self, *_args: object) -> object:
            return "v3"

    monkeypatch.setattr(route, "AgentVersionStore", Versions)
    monkeypatch.setattr(route, "pinned_agent", lambda live, version: {**live, "name": f"published {version}"})

    async def item(config: FakeConfigurationService) -> str:
        return (await route.agent_for_chat(dict(saved), object(), "org-1", "agent-1", config))["name"]  # type: ignore[arg-type]

    _assert_matrix(await _matrix(monkeypatch, item), harness="published v3")


@pytest.mark.asyncio
async def test_the_classic_loop_has_no_flow_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agents.agent_loop.factory import PipesHubAgentFactory
    from harness_core.kernel.hooks.events import HookEvent
    from harness_core.kernel.hooks.middleware.context import ToolCallContext
    from harness_core.kernel.hooks.middleware.decisions import PreDecision
    from harness_core.tools.base import Tag
    from tests.unit.agents.adapter.conftest import FakeChatModel, make_context

    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")

    async def item(config: FakeConfigurationService) -> bool:
        context = make_context(llm=FakeChatModel(), config_service=config)
        context.previous_conversations = [{"role": "user_query", "content": "Q3 plan?"}]
        _agent, runtime, _goal, _clarifying = await PipesHubAgentFactory().create(
            context, context.llm, "react", query="post it",
        )
        ctx = ToolCallContext(
            tool_path="/toolsets/slack/send_message",
            tool_input={},
            tags=[Tag("category", "write")],
        )
        await runtime.hooks.on(HookEvent.PRE_TOOL_USE).dispatch(ctx)  # type: ignore[union-attr]
        return ctx.decision == PreDecision.DENY

    _assert_matrix(await _matrix(monkeypatch, item), harness=True)


@pytest.mark.asyncio
@pytest.mark.mcp_hub_default
async def test_the_mcp_list_shows_no_hub_or_tool_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    """The admin's MCP page reads `mcpHub` and `mcpToolApproval` from the list: an org with the harness off sees no
    hub and no approval state, even where its admin turned approval on while the harness was on."""
    from unittest.mock import AsyncMock, MagicMock

    import app.api.routes.mcp_servers as route
    import app.harness.mcp_hub.tool_admission as admission
    from tests.support.bound_org import bind_test_org

    monkeypatch.delenv("MCP_HUB", raising=False)
    monkeypatch.setattr(route, "_visible_instances", AsyncMock(return_value=[]))
    monkeypatch.setattr(route, "_check_user_is_admin", AsyncMock(return_value=True))
    monkeypatch.setattr(admission, "requires_admin_approval", AsyncMock(return_value=True))

    async def item(config: FakeConfigurationService) -> tuple[bool, bool]:
        request = MagicMock()
        request.state.user = {"userId": "u1", "orgId": "o1"}
        request.app.state.config_service = config
        for _ in bind_test_org("o1"):
            answer = await route.get_my_mcp_servers(request, include_tools=False)
        return answer["mcpHub"], answer["mcpToolApproval"]

    _assert_matrix(await _matrix(monkeypatch, item), harness=(True, True))


async def _classic_hooks(monkeypatch: pytest.MonkeyPatch, config: FakeConfigurationService) -> dict[str, Any]:
    """What the classic loop's factory wires for one turn on the AG-UI stream: its PRE_MODEL layers and the frames
    its provider wait would stream."""
    import app.agents.agent_loop.factory as factory
    from app.agents.agent_loop.hooks.retry_with_status import _legacy_status
    from tests.unit.agents.adapter.conftest import FakeChatModel, make_context

    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    seen: dict[str, Any] = {}
    original_pipeline = factory.build_context_pipeline
    original_retry = factory.retry_with_status

    def pipeline(profile: Any) -> Any:  # noqa: ANN401
        layers = original_pipeline(profile)
        seen["layers"] = [layer.__qualname__.split(".<locals>")[0] for layer in layers]
        return layers

    def retry(sink: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        seen["wait"] = kwargs.get("wait_frames", _legacy_status)
        return original_retry(sink, *args, **kwargs)

    monkeypatch.setattr(factory, "build_context_pipeline", pipeline)
    monkeypatch.setattr(factory, "retry_with_status", retry)
    context = make_context(llm=FakeChatModel(), config_service=config, protocol="agui")
    context.run_id = "run-1"
    await factory.PipesHubAgentFactory().create(context, context.llm, "react", query="hello")
    return seen


@pytest.mark.asyncio
async def test_the_classic_stream_reports_no_context_compaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Upstream's classic loop streams no `context_compacted` frame and saves no such part when it cuts history."""

    async def item(config: FakeConfigurationService) -> bool:
        return "report_context_reduction" in (await _classic_hooks(monkeypatch, config))["layers"]

    _assert_matrix(await _matrix(monkeypatch, item), harness=True)


@pytest.mark.asyncio
async def test_the_classic_stream_sends_a_provider_wait_as_upstreams_status_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retried 429 streams upstream's `status`/`retrying` frame, not the harness's `waiting_provider` snapshot."""

    async def item(config: FakeConfigurationService) -> list[dict[str, Any]]:
        wait = (await _classic_hooks(monkeypatch, config))["wait"]
        return wait("2026-11-21T12:00:07.000Z", "The AI provider is rate limiting requests, retrying (2/4)...")

    answers = await _matrix(monkeypatch, item)
    assert answers["switch off"] == [{
        "event": "status",
        "data": {"status": "retrying", "message": "The AI provider is rate limiting requests, retrying (2/4)..."},
    }]
    harness = answers["switch on, Labs on"]
    assert harness[0]["data"]["snapshot"] == {"status": "waiting_provider", "retry_at": "2026-11-21T12:00:07.000Z"}
    _assert_matrix(answers, harness=harness)
