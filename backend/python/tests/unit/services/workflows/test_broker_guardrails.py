"""The broker is the security boundary between sandboxed workflow code and
real tenant credentials, so these tests cover the three things that decide
whether a `ctx.tool()` call is allowed to touch the outside world: the
`RunGrant` allowlist, the dry-run write skip, and the per-run call budget.

Everything here talks to `PlatformBroker.dispatch` -- the single entry point
the sandbox bridge uses -- rather than a handler in isolation, because the
ordering of those checks is itself the behaviour under test.
"""
from __future__ import annotations

from typing import Any, NamedTuple

import pytest

from app.agent_loop_lib.tools.base import ToolOutput
from app.services.workflows.interface.broker import (
    BrokerCall,
    BrokerResult,
    Capability,
    RunGrant,
    RunPrincipal,
)
from app.services.workflows.runtime.broker import build_platform_broker


class _Tag(NamedTuple):
    key: str
    value: str


class _FakeTool:
    """Stands in for a real `Tool`: implements `__call__` the same way
    `Tool.__call__` does (no validation, since fixtures pass whatever kwargs
    the test wants), returning a `ToolOutput`."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> ToolOutput:
        self.calls.append(kwargs)
        return ToolOutput(success=True, data=f"ran {self.name}")


class _FakeRegistry:
    """Minimal stand-in for `ToolRegistry`: name -> tool plus category tags.

    `tags` maps a tool name to its "category" values; a name absent from the
    map has no categories, which the broker treats as unclassifiable.
    """

    def __init__(self, tools: dict[str, list[str]]) -> None:
        self._tools = {name: _FakeTool(name) for name in tools}
        self._tags = {name: cats for name, cats in tools.items()}

    def has(self, name: str) -> bool:
        return name in self._tools

    def resolve_by_name(self, name: str) -> _FakeTool:
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools)

    def tags_for_name(self, name: str) -> tuple[_Tag, ...]:
        return tuple(_Tag("category", c) for c in self._tags.get(name, []))

    def tool(self, name: str) -> _FakeTool:
        return self._tools[name]


_REGISTRY_TOOL_NAMES = frozenset({
    "jira__create_issue",
    "jira__search_issues",
    "jira__delete_issue",
    "slack__post_message",
    "mystery__do_thing",
})
"""Every tool name any fixture registry in this module serves.

Granted wholesale by `_principal()` so that a test about taint or budgets is
not silently short-circuited by the grant check.
"""


def _registry() -> _FakeRegistry:
    return _FakeRegistry({
        "jira__create_issue": ["write"],
        "jira__search_issues": ["read"],
    })


class _RecordingHandler:
    """Captures the call a handler actually receives.

    The narrowing under test rewrites arguments between the grant check and
    the handler, so asserting on the dispatch result alone would not show it.
    """

    def __init__(self, capability: Capability, sink: dict[str, Any]) -> None:
        self.capability = capability
        self._sink = sink

    async def handle(self, call: BrokerCall, principal: RunPrincipal) -> BrokerResult:
        self._sink["arguments"] = dict(call.arguments)
        return BrokerResult(success=True, data=[])


class _MintingAgentCreateHandler:
    """Stands in for `AgentCreateHandler`, returning a fixed new agent id."""

    capability = Capability.AGENT_CREATE

    def __init__(self, agent_id: str) -> None:
        self._agent_id = agent_id

    async def handle(self, call: BrokerCall, principal: RunPrincipal) -> BrokerResult:
        return BrokerResult(success=True, data={"agent_id": self._agent_id})


def _grant(**overrides: Any) -> RunGrant:
    """A grant over the fixture tools, for tests varying some other field."""
    return RunGrant(tool_names=frozenset(_REGISTRY_TOOL_NAMES), **overrides)


def _principal(**overrides: Any) -> RunPrincipal:
    """A principal granted the fixture registry's tools.

    The grant is explicit because empty means deny: tests about taint, dry run
    and call budgets would otherwise all fail at the grant check before
    reaching the behaviour they cover.
    """
    base = {
        "org_id": "org-1",
        "user_id": "u-1",
        "run_id": "run-1",
        "workflow_id": "wf-1",
        "grant": _grant(),
    }
    base.update(overrides)
    return RunPrincipal(**base)


def _tool_call(target: str, **arguments: Any) -> BrokerCall:
    return BrokerCall(
        capability=Capability.TOOL,
        target=target,
        arguments=arguments,
        run_id="run-1",
        step_key="ctx.tool:x#0",
    )


class TestRunGrant:
    @pytest.mark.asyncio
    async def test_denies_a_tool_outside_the_grant(self) -> None:
        registry = _registry()
        broker = build_platform_broker(tool_registry=registry)
        principal = _principal(grant=RunGrant(tool_names=frozenset({"jira__search_issues"})))

        result = await broker.dispatch(_tool_call("jira__create_issue"), principal)

        assert result.success is False
        assert "not in run grant" in (result.error or "")
        assert registry.tool("jira__create_issue").calls == []

    @pytest.mark.asyncio
    async def test_allows_a_tool_inside_the_grant(self) -> None:
        registry = _registry()
        broker = build_platform_broker(tool_registry=registry)
        principal = _principal(grant=RunGrant(tool_names=frozenset({"jira__search_issues"})))

        result = await broker.dispatch(_tool_call("jira__search_issues", q="bug"), principal)

        assert result.success is True
        assert registry.tool("jira__search_issues").calls == [{"q": "bug"}]

    @pytest.mark.asyncio
    async def test_empty_grant_denies_every_tool(self) -> None:
        """Empty is "granted nothing", not "granted everything".

        Reading it the other way meant a version saved before tool pinning, or
        any path that produced an empty grant, ran against the whole registry.
        """
        registry = _registry()
        broker = build_platform_broker(tool_registry=registry)

        result = await broker.dispatch(
            _tool_call("jira__create_issue"), _principal(grant=RunGrant()),
        )

        assert result.success is False
        assert "not in run grant" in (result.error or "")
        assert registry.tool("jira__create_issue").calls == []

    @pytest.mark.asyncio
    async def test_empty_grant_denies_agent_run(self) -> None:
        broker = build_platform_broker(tool_registry=_registry())
        call = BrokerCall(
            capability=Capability.AGENT_RUN, target="agent-x",
            arguments={"goal": "do it"}, run_id="run-1", step_key="ctx.agent#0",
        )

        result = await broker.dispatch(call, _principal(grant=RunGrant()))

        assert result.success is False
        assert "not in run grant.agent_ids" in (result.error or "")

    @pytest.mark.asyncio
    async def test_an_agent_minted_by_the_run_can_then_be_run(self) -> None:
        """`ctx.create_agent(...)` then `.run()`.

        The new id cannot be in `agent_ids`, which is pinned from source at
        commit time, so deny-by-default would otherwise make the pair
        unusable for any workflow.
        """
        broker = build_platform_broker(tool_registry=_registry())
        broker.register(_MintingAgentCreateHandler("agent-new"))
        principal = _principal(grant=_grant(can_create_agents=True))

        created = await broker.dispatch(
            BrokerCall(
                capability=Capability.AGENT_CREATE, target="helper",
                run_id="run-1", step_key="ctx.create_agent#0",
            ),
            principal,
        )
        ran = await broker.dispatch(
            BrokerCall(
                capability=Capability.AGENT_RUN, target="agent-new",
                arguments={"goal": "go"}, run_id="run-1", step_key="ctx.agent#0",
            ),
            principal,
        )

        assert created.success is True
        assert ran.success is not False or "not in run grant.agent_ids" not in (ran.error or "")

    @pytest.mark.asyncio
    async def test_an_agent_minted_by_another_run_is_not_reachable(self) -> None:
        broker = build_platform_broker(tool_registry=_registry())
        broker.register(_MintingAgentCreateHandler("agent-new"))

        await broker.dispatch(
            BrokerCall(
                capability=Capability.AGENT_CREATE, target="helper",
                run_id="run-1", step_key="ctx.create_agent#0",
            ),
            _principal(grant=_grant(can_create_agents=True)),
        )
        other = await broker.dispatch(
            BrokerCall(
                capability=Capability.AGENT_RUN, target="agent-new",
                arguments={"goal": "go"}, run_id="run-2", step_key="ctx.agent#0",
            ),
            _principal(run_id="run-2"),
        )

        assert other.success is False
        assert "not in run grant.agent_ids" in (other.error or "")

    @pytest.mark.asyncio
    async def test_agent_creation_denied_unless_granted(self) -> None:
        broker = build_platform_broker(tool_registry=_registry())
        call = BrokerCall(
            capability=Capability.AGENT_CREATE, target="new-agent",
            run_id="run-1", step_key="ctx.create_agent#0",
        )

        result = await broker.dispatch(call, _principal())

        assert result.success is False
        assert "agent creation" in (result.error or "")

    @pytest.mark.asyncio
    async def test_knowledge_search_denied_for_ungranted_collections(self) -> None:
        broker = build_platform_broker(tool_registry=_registry())
        call = BrokerCall(
            capability=Capability.KNOWLEDGE_SEARCH, target="search",
            arguments={"collections": ["kb-secret"]},
            run_id="run-1", step_key="ctx.search#0",
        )
        principal = _principal(grant=RunGrant(collection_ids=frozenset({"kb-public"})))

        result = await broker.dispatch(call, principal)

        assert result.success is False
        assert "kb-secret" in (result.error or "")

    @pytest.mark.asyncio
    async def test_knowledge_search_without_collections_is_narrowed_to_the_grant(self) -> None:
        """`ctx.search("q")` sends no `collections`.

        The subset check passes vacuously, and the handler reads an empty list
        as "every collection in the org" -- so omitting the argument used to be
        strictly more powerful than asking for a collection by name.
        """
        seen: dict[str, Any] = {}
        broker = build_platform_broker(tool_registry=_registry())
        broker.register(_RecordingHandler(Capability.KNOWLEDGE_SEARCH, seen))
        call = BrokerCall(
            capability=Capability.KNOWLEDGE_SEARCH, target="search",
            arguments={}, run_id="run-1", step_key="ctx.search#0",
        )
        principal = _principal(grant=_grant(collection_ids=frozenset({"kb-a", "kb-b"})))

        result = await broker.dispatch(call, principal)

        assert result.success is True
        assert seen["arguments"]["collections"] == ["kb-a", "kb-b"]

    @pytest.mark.asyncio
    async def test_knowledge_search_is_intersected_with_the_grant(self) -> None:
        seen: dict[str, Any] = {}
        broker = build_platform_broker(tool_registry=_registry())
        broker.register(_RecordingHandler(Capability.KNOWLEDGE_SEARCH, seen))
        call = BrokerCall(
            capability=Capability.KNOWLEDGE_SEARCH, target="search",
            arguments={"collections": ["kb-a"]},
            run_id="run-1", step_key="ctx.search#0",
        )
        principal = _principal(grant=_grant(collection_ids=frozenset({"kb-a", "kb-b"})))

        result = await broker.dispatch(call, principal)

        assert result.success is True
        assert seen["arguments"]["collections"] == ["kb-a"]


class TestDryRun:
    @pytest.mark.asyncio
    async def test_does_not_run_a_sub_agent(self) -> None:
        """A sub-agent runs with its own write tools and nothing threads
        `is_dry_run` into the child, so executing it would mutate for real."""
        seen: dict[str, Any] = {}
        broker = build_platform_broker(tool_registry=_registry())
        broker.register(_RecordingHandler(Capability.AGENT_RUN, seen))
        call = BrokerCall(
            capability=Capability.AGENT_RUN, target="agent-a",
            arguments={"goal": "ship it"}, run_id="run-1", step_key="ctx.agent#0",
        )
        principal = _principal(
            is_dry_run=True, grant=_grant(agent_ids=frozenset({"agent-a"})),
        )

        result = await broker.dispatch(call, principal)

        assert result.success is True
        assert result.data["dry_run"] is True
        assert seen == {}

    @pytest.mark.asyncio
    async def test_does_not_post_to_the_live_conversation(self) -> None:
        seen: dict[str, Any] = {}
        broker = build_platform_broker(tool_registry=_registry())
        broker.register(_RecordingHandler(Capability.CONVERSATION_EMIT, seen))
        call = BrokerCall(
            capability=Capability.CONVERSATION_EMIT, target="text",
            arguments={"message": "halfway done"},
            run_id="run-1", step_key="ctx.emit:text#0",
        )

        result = await broker.dispatch(call, _principal(is_dry_run=True))

        assert result.success is True
        assert seen == {}

    @pytest.mark.asyncio
    async def test_a_simulated_agent_creation_can_still_be_run(self) -> None:
        """The grant check precedes the dry-run branch, so a simulated
        `create_agent` has to leave behind an id its `.run()` is allowed to
        use -- otherwise the dry run dies on a denial."""
        broker = build_platform_broker(tool_registry=_registry())
        principal = _principal(is_dry_run=True, grant=_grant(can_create_agents=True))

        created = await broker.dispatch(
            BrokerCall(
                capability=Capability.AGENT_CREATE, target="helper",
                run_id="run-1", step_key="ctx.create_agent#0",
            ),
            principal,
        )
        ran = await broker.dispatch(
            BrokerCall(
                capability=Capability.AGENT_RUN, target=created.data["agent_id"],
                arguments={"goal": "go"}, run_id="run-1", step_key="ctx.agent#0",
            ),
            principal,
        )

        assert created.data["agent_id"] == "helper"
        assert ran.success is True
        assert ran.data["dry_run"] is True

    @pytest.mark.asyncio
    async def test_skips_a_bare_write_tool_without_step_metadata(self) -> None:
        """The skip is decided host-side from registry tags -- generated code
        never has to opt in via `@step(side_effect=WRITE)` for a dry run to
        stay harmless."""
        registry = _registry()
        broker = build_platform_broker(tool_registry=registry)

        result = await broker.dispatch(
            _tool_call("jira__create_issue", summary="x"), _principal(is_dry_run=True),
        )

        assert result.success is True
        assert result.data["dry_run"] is True
        assert result.data["skipped"] == "jira__create_issue"
        assert registry.tool("jira__create_issue").calls == []

    @pytest.mark.asyncio
    async def test_still_executes_reads(self) -> None:
        registry = _registry()
        broker = build_platform_broker(tool_registry=registry)

        result = await broker.dispatch(
            _tool_call("jira__search_issues", q="bug"), _principal(is_dry_run=True),
        )

        assert result.success is True
        assert registry.tool("jira__search_issues").calls == [{"q": "bug"}]

    @pytest.mark.asyncio
    async def test_untagged_tool_is_treated_as_a_write(self) -> None:
        """Failing closed matters more than dry-run fidelity: an unclassified
        tool that actually mutates would otherwise run for real."""
        registry = _FakeRegistry({"mystery__do_thing": []})
        broker = build_platform_broker(tool_registry=registry)

        result = await broker.dispatch(
            _tool_call("mystery__do_thing"), _principal(is_dry_run=True),
        )

        assert result.data["dry_run"] is True
        assert registry.tool("mystery__do_thing").calls == []


class TestTaint:
    """Injected text in a Jira ticket must not be able to drive a deletion,
    but it also must not stop the read-then-post shape most workflows have --
    a scheduled run has nobody to approve anything."""

    @staticmethod
    def _broker() -> tuple[_FakeRegistry, Any]:
        registry = _FakeRegistry({
            "jira__search_issues": ["read"],
            "slack__post_message": ["write"],
            "jira__delete_issue": ["destructive"],
        })
        return registry, build_platform_broker(tool_registry=registry)

    @pytest.mark.asyncio
    async def test_a_destructive_call_after_an_external_read_is_blocked(self) -> None:
        registry, broker = self._broker()
        principal = _principal()

        await broker.dispatch(_tool_call("jira__search_issues"), principal)
        result = await broker.dispatch(_tool_call("jira__delete_issue", key="OPS-1"), principal)

        assert result.success is False
        assert "ctx.request_approval" in (result.error or "")
        assert registry.tool("jira__delete_issue").calls == []

    @pytest.mark.asyncio
    async def test_an_ordinary_write_after_an_external_read_still_runs(self) -> None:
        registry, broker = self._broker()
        principal = _principal()

        await broker.dispatch(_tool_call("jira__search_issues"), principal)
        result = await broker.dispatch(_tool_call("slack__post_message", text="digest"), principal)

        assert result.success is True
        assert registry.tool("slack__post_message").calls == [{"text": "digest"}]

    @pytest.mark.asyncio
    async def test_taint_does_not_leak_between_runs(self) -> None:
        registry, broker = self._broker()

        await broker.dispatch(_tool_call("jira__search_issues"), _principal())
        clean_run = BrokerCall(
            capability=Capability.TOOL, target="jira__delete_issue",
            run_id="run-2", step_key="ctx.tool:x#0",
        )

        result = await broker.dispatch(clean_run, _principal(run_id="run-2"))

        assert result.success is True
        assert registry.tool("jira__delete_issue").calls == [{}]


class TestCallBudget:
    @pytest.mark.asyncio
    async def test_run_stops_after_max_calls(self) -> None:
        registry = _registry()
        broker = build_platform_broker(tool_registry=registry)
        principal = _principal(grant=_grant(max_calls=2))

        first = await broker.dispatch(_tool_call("jira__search_issues"), principal)
        second = await broker.dispatch(_tool_call("jira__search_issues"), principal)
        third = await broker.dispatch(_tool_call("jira__search_issues"), principal)

        assert first.success and second.success
        assert third.success is False
        assert "max_calls" in (third.error or "")
        assert len(registry.tool("jira__search_issues").calls) == 2

    @pytest.mark.asyncio
    async def test_budget_is_per_run_not_per_broker(self) -> None:
        registry = _registry()
        broker = build_platform_broker(tool_registry=registry)
        grant = _grant(max_calls=1)

        await broker.dispatch(_tool_call("jira__search_issues"), _principal(grant=grant))
        other_run = _principal(run_id="run-2", grant=grant)
        other_call = BrokerCall(
            capability=Capability.TOOL, target="jira__search_issues",
            run_id="run-2", step_key="ctx.tool:x#0",
        )

        assert (await broker.dispatch(other_call, other_run)).success is True


class TestMisconfiguration:
    @pytest.mark.asyncio
    async def test_missing_registry_fails_loudly_rather_than_silently(self) -> None:
        broker = build_platform_broker(tool_registry=None)

        result = await broker.dispatch(_tool_call("jira__search_issues"), _principal())

        assert result.success is False
        assert "configuration error" in (result.error or "")


class TestHandlerRegistration:
    """A capability with no handler is not a workflow error but a wiring bug:
    every `ctx.state` call in every workflow fails until it is fixed, so these
    pin the registration path rather than any one call's behaviour."""

    @pytest.mark.asyncio
    async def test_state_get_and_set_both_reach_a_handler(self) -> None:
        """Regression: the state handler used to register itself by reaching
        back into the concrete broker, so any construction path that did not
        know to call it left STATE_SET unhandled."""
        broker = build_platform_broker(tool_registry=_registry(), state_store=_FakeStateStore())
        principal = _principal()

        get_result = await broker.dispatch(
            BrokerCall(
                capability=Capability.STATE_GET, target="counter",
                run_id="run-1", step_key="ctx.state.get:counter#0",
            ),
            principal,
        )
        set_result = await broker.dispatch(
            BrokerCall(
                capability=Capability.STATE_SET, target="counter",
                arguments={"value": 1}, run_id="run-1", step_key="ctx.state.set:counter#0",
            ),
            principal,
        )

        assert get_result.success is True
        assert set_result.success is True


class _FakeStateStore:
    def __init__(self) -> None:
        self._values: dict[tuple[str, str, str], Any] = {}

    async def get(self, org_id: str, workflow_id: str, key: str) -> Any:
        return self._values.get((org_id, workflow_id, key))

    async def set(self, org_id: str, workflow_id: str, key: str, value: Any) -> None:
        self._values[(org_id, workflow_id, key)] = value
