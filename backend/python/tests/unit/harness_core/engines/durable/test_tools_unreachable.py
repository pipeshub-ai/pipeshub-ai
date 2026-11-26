"""P9 found while building (FB-2, from FM-6): an engine that started without reaching its granted tools must not
complete the turn; the claim goes back and a later attempt drives the turn again, once (SIGNFIX: in a manual test the
same broken state was driven three times, quarantined ``crash_loop``, and the crash loop's reap ended the person's kept
Claude sign-in, so their next message asked them to sign in again). A second attempt that cannot reach them fails the
run ``engine_tools_unreachable``, which the chat words, and leaves the conversation's kept sandbox alone.

The lane's opencode, its tool gateway's token out of reach (Node restarting), connected no MCP server and
answered "tool unavailable"; the run completed without its effect. Here the gateway answers ``tools/list``
with an error before the endpoint sees it, as the proxy does when it cannot get the attempt's token."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.engines.controller import KEPT_LABEL
from harness_core.engines.durable.kernel import ENGINE_TOOLS_UNREACHABLE
from harness_core.engines.jsonview import as_object, as_str
from harness_core.sandbox.types import LeaseRef, RuntimeMode, SandboxSpec
from harness_core.testing.engines.durable import IMAGE, ORG, SESSION, EngineWorld
from harness_core.testing.engines.tool_host import InMemoryToolGatewayHost

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.testing.sandbox.egress import EgressRequest

RUN = "run-no-tools"


@pytest.fixture
def unreachable(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    state = {"down": True}
    original = InMemoryToolGatewayHost.handle

    async def handle(self: InMemoryToolGatewayHost, request: EgressRequest) -> JsonValue:
        if state["down"] and as_str(as_object(request.body).get("method")) == "tools/list":
            return {"error": {"code": "upstream_unavailable"}}
        return await original(self, request)

    monkeypatch.setattr(InMemoryToolGatewayHost, "handle", handle)
    return state


async def test_a_turn_whose_engine_never_listed_its_tools_is_released_then_completed(
    unreachable: dict[str, bool],
) -> None:
    world = EngineWorld()
    world.admit(RUN, 'tool lookup {"q": "x"}\nsay done')
    first = await world.attempt(RUN)
    assert (first.status, first.reason) == ("released", ENGINE_TOOLS_UNREACHABLE), first
    assert "run.completed" not in world.types(RUN)
    unreachable["down"] = False
    world.expire()
    second = await world.attempt(RUN)
    assert second.status == "completed"
    assert sum(n for (name, _), n in world.effects.items() if name == "lookup") == 1


async def test_a_run_without_tools_needs_no_listing(unreachable: dict[str, bool]) -> None:
    world = EngineWorld(tools=())
    world.admit(RUN, "say hello")
    assert (await world.attempt(RUN)).status == "completed"


async def test_sf10_tools_still_unreachable_on_the_retry_fail_the_run_with_a_named_code_not_a_crash_loop(
    unreachable: dict[str, bool],
) -> None:
    world = EngineWorld()
    world.admit(RUN, 'tool lookup {"q": "x"}\nsay done')
    first = await world.attempt(RUN)
    assert (first.status, first.reason) == ("released", ENGINE_TOOLS_UNREACHABLE), first
    world.expire()
    second = await world.attempt(RUN)
    assert (second.status, second.reason) == ("failed", ENGINE_TOOLS_UNREACHABLE), second
    [failed] = [e for e in world.events(RUN) if e.type == "run.failed"]
    assert failed.data.get("errorType") == ENGINE_TOOLS_UNREACHABLE
    assert "run.quarantined" not in world.types(RUN)
    assert sum(n for (name, _), n in world.effects.items() if name == "lookup") == 0


async def test_sf11_the_failure_leaves_the_conversations_kept_sign_in_sandbox(unreachable: dict[str, bool]) -> None:
    world = EngineWorld()
    kept = SandboxSpec(
        IMAGE, RuntimeMode.DOCKER_HARDENED, labels={"orgId": ORG, "sessionId": SESSION, KEPT_LABEL: "1"}
    )
    handle = await world.broker.acquire(kept, lease=LeaseRef("worker-0", 1), idempotency_key="signin:kept")
    world.admit(RUN, 'tool lookup {"q": "x"}\nsay done')
    disposition = await world.attempt(RUN)
    for _ in range(3):
        if disposition.status != "released":
            break
        world.expire()
        disposition = await world.attempt(RUN)
    assert disposition.status == "failed"
    assert handle.id in world.broker.sandboxes, "a PipesHub-side failure never ends the person's sign-in"
