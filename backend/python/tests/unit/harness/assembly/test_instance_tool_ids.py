"""DR-1..DR-3, OJ-2, OJ-3, LP-1 (durable worker) (P2.2): two instances of one toolset type on one run.

The real tool loader, instance creator, credential resolution and catalog run; only the stores, the
client factory and the model are faked (``world``).
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from harness_core.driver import CapabilityLost
from harness_core.effects import (
    EffectCall,
    EffectRunner,
    RequestEffectLog,
    ToolExecution,
    args_digest,
)
from harness_core.gateway import RunToolEndpoint
from harness_core.gateway.memory import InMemoryCitationSink
from harness_core.invoker import InvocationScope
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolContext
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.contracts.tool_endpoint import BINDING, claims_for
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.ids import SeededIds
from harness_core.tool_identity import connector_tool_name, scoped_mcp_tool_name

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.driver import AssembledRuntime

SECOND = "inst-77"
A_SEARCH, B_SEARCH = (f"jira@{i}/search_issues" for i in (w.JIRA, SECOND))
A_CREATE, B_CREATE = (f"jira@{i}/create_issue" for i in (w.JIRA, SECOND))
A_NAME, B_NAME = (connector_tool_name("jira", i, "search_issues", shared=True) for i in (w.JIRA, SECOND))
A_CREATE_NAME, B_CREATE_NAME = (connector_tool_name("jira", i, "create_issue", shared=True) for i in (w.JIRA, SECOND))
CTX = ToolContext("run-1", w.ORG, "t1.0", "att-1", "k")


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("INSTANCE_TOOL_IDS", "true")

    async def search_issues(self: Any, jql: str) -> str:  # noqa: ANN401
        return f'{{"token": "{self.client["config"]["auth"]["token"]}", "jql": "{jql}"}}'

    w.replace_tool(monkeypatch, w.FakeJira, "search_issues", search_issues)
    with w.deployment():
        yield


def _jira(instance: str, name: str) -> dict[str, Any]:
    return {
        "name": "jira",
        "instanceId": instance,
        "instanceName": name,
        "displayName": "Jira",
        "tools": [
            {"name": "search_issues", "fullName": "jira.search_issues"},
            {"name": "create_issue", "fullName": "jira.create_issue"},
        ],
    }


def _services(toolsets: list[dict[str, Any]], approvals: Any = None) -> w.Services:  # noqa: ANN401
    services = w.Services(agent=w.agent_doc(toolsets=toolsets, mcpServers=[]))
    if approvals is not None:
        services.config.put("/services/agentHarness/approvals", approvals)

    def credentials(path: str) -> dict[str, Any]:
        instance = path.split("/services/toolsets/", 1)[1].split("/", 1)[0]
        return {"isAuthenticated": True, "auth": {"token": f"token-{instance}"}}

    services.config.answer("/services/toolsets/", credentials)
    services.config.put(
        "/services/toolset-instances", [{"_id": i, "orgId": w.ORG, "toolsetType": "jira"} for i in (w.JIRA, SECOND)]
    )
    return services


async def _build(services: w.Services, tool_ids: list[str]) -> AssembledRuntime:
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port))
    return await assembler.build(w.claim(w.pinned_spec(tool_ids=tool_ids)))  # type: ignore[arg-type]


TWO = [_jira(w.JIRA, "Acme Jira"), _jira(SECOND, "Beta Jira")]


async def test_dr1_both_instances_are_in_the_catalog_under_their_own_ids() -> None:
    runtime = await _build(_services(TWO), [A_SEARCH, B_SEARCH])
    assert A_NAME != B_NAME
    assert {A_NAME, B_NAME} <= set(runtime.tools.names)  # type: ignore[attr-defined]
    assert runtime.tools.describe(A_NAME).tool_id == A_SEARCH  # type: ignore[union-attr]
    assert runtime.tools.describe(B_NAME).tool_id == B_SEARCH  # type: ignore[union-attr]
    assert "jira__search_issues" not in runtime.tools.names  # type: ignore[attr-defined]


async def test_lp1_each_call_reaches_its_own_instances_credentials() -> None:
    runtime = await _build(_services(TWO), [A_SEARCH, B_SEARCH])
    a = await runtime.tools.execute(ToolCall(id="a", name=A_NAME, arguments={"jql": "x"}), CTX)
    b = await runtime.tools.execute(ToolCall(id="b", name=B_NAME, arguments={"jql": "x"}), CTX)
    assert f"token-{w.JIRA}" in str(a.content) and f"token-{SECOND}" not in str(a.content)
    assert f"token-{SECOND}" in str(b.content)


async def test_lp1_the_model_is_told_which_instance_a_tool_belongs_to() -> None:
    runtime = await _build(_services(TWO), [A_SEARCH, B_SEARCH])
    schemas = {s.name: s for s in runtime.tools.schemas() or []}  # type: ignore[union-attr]
    assert schemas[A_NAME].description.startswith("[Acme Jira] ")
    assert schemas[B_NAME].description.startswith("[Beta Jira] ")


async def test_dr2_an_approval_rule_on_one_instance_does_not_reach_the_other() -> None:
    runtime = await _build(
        _services(TWO, {"default": {"tools": {A_CREATE: "deny"}}}), [A_CREATE, B_CREATE]
    )
    call = ToolCall(id="c", name=A_CREATE_NAME, arguments={"title": "t"})
    assert runtime.approval(call, runtime.tools.describe(A_CREATE_NAME)) == "deny"  # type: ignore[arg-type]
    assert runtime.approval(call, runtime.tools.describe(B_CREATE_NAME)) == "allow"  # type: ignore[arg-type]


async def test_dr3_the_run_tool_gateway_lists_both_and_routes_each_call_to_its_instance() -> None:
    runtime = await _build(_services(TWO), [A_SEARCH, B_SEARCH])
    endpoint = RunToolEndpoint(
        binding=BINDING,
        catalog=runtime.tools,  # type: ignore[arg-type]
        effects=EffectRunner(RequestEffectLog(), runtime.approval),
        scope=InvocationScope("run-1", w.ORG, "att-1"),
        ids=SeededIds(),
        citations=InMemoryCitationSink(),
    )
    listed = {t.name for t in await endpoint.list_tools(claims_for(BINDING))}
    assert {A_NAME, B_NAME} <= listed
    a = await endpoint.call_tool(claims_for(BINDING), A_NAME, {"jql": "q"})
    b = await endpoint.call_tool(claims_for(BINDING), B_NAME, {"jql": "q"})
    assert f"token-{w.JIRA}" in a.text and f"token-{SECOND}" in b.text
    assert not (a.is_error or b.is_error)


async def test_one_instance_keeps_todays_names_with_the_flag_on() -> None:
    runtime = await _build(_services([_jira(w.JIRA, "Acme Jira")]), [A_SEARCH])
    assert "jira__search_issues" in runtime.tools.names  # type: ignore[attr-defined]
    assert runtime.tools.describe("jira__search_issues").tool_id == A_SEARCH  # type: ignore[union-attr]


async def _create(runtime: AssembledRuntime, log: RequestEffectLog, invocation: str) -> str:
    effect = runtime.tools.describe("jira__create_issue")  # type: ignore[union-attr]
    assert effect is not None
    call = ToolCall(id=invocation, name="jira__create_issue", arguments={"title": "t"})
    result = await EffectRunner(log, runtime.approval).run(
        EffectCall("run-1", invocation, call, effect, args_digest(call.arguments)), CTX, runtime.tools.execute
    )
    return result.disposition


async def test_oj2_a_type_keyed_pin_finds_the_toolset_its_agent_was_migrated_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[str] = []

    async def create_issue(self: Any, title: str) -> str:  # noqa: ANN401
        created.append(title)
        return '{"id": "J-1"}'

    w.replace_tool(monkeypatch, w.FakeJira, "create_issue", create_issue)
    legacy = "jira@default/create_issue"
    log = RequestEffectLog()
    # An earlier attempt of the run, pinned before its agent was migrated, journaled the call
    # under the type-keyed id it was granted.
    after = await _build(_services([_jira(w.JIRA, "Acme Jira")]), [legacy])
    earlier = after.tools.describe("jira__create_issue")  # type: ignore[union-attr]
    assert earlier is not None
    call = ToolCall(id="inv-1", name="jira__create_issue", arguments={"title": "t"})

    async def journaled(call: ToolCall, ctx: ToolContext) -> ToolExecution:
        created.append("t")
        return ToolExecution(status="ok", content='{"id": "J-1"}')

    await EffectRunner(log, lambda *_: "allow").run(
        EffectCall("run-1", "inv-1", call, dataclasses.replace(earlier, tool_id=legacy), args_digest(call.arguments)),
        CTX,
        journaled,
    )
    # The agent is now bound to its instance; the run is resumed by a new attempt.
    assert after.tools.describe("jira__create_issue").tool_id == legacy  # type: ignore[union-attr]
    assert await _create(after, log, "inv-1") == "recorded"
    assert created == ["t"]


async def test_oj3_a_type_keyed_pin_two_instances_could_answer_is_lost_not_guessed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING), pytest.raises(CapabilityLost) as lost:
        _ = await _build(_services(TWO), ["jira@default/search_issues"])
    assert lost.value.missing == ("tool:jira@default/search_issues",)
    unresolved = [r for r in caplog.records if "run.tool_grant_unresolved" in r.getMessage()]
    assert len(unresolved) == 1
    assert unresolved[0].__dict__["harness"]["reasonCode"] == "tool_instance_ambiguous"


async def test_lp3_flag_off_two_instances_load_as_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INSTANCE_TOOL_IDS", "false")
    runtime = await _build(_services(TWO), [A_SEARCH])
    assert "jira__search_issues" in runtime.tools.names  # type: ignore[attr-defined]
    assert A_NAME not in runtime.tools.names  # type: ignore[attr-defined]


def _github(instance: str, name: str) -> dict[str, Any]:
    return {
        "instanceId": instance,
        "name": name,
        "displayName": name,
        "typeId": "github",
        "tools": [{"name": "list_prs", "fullName": "mcp_github_list_prs"}],
    }


async def test_lp4_two_mcp_instances_of_one_type_are_distinct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(w.MCP_TOOLS, "mcp-2", w.MCP_TOOLS[w.MCP])
    agent = w.agent_doc(toolsets=[], mcpServers=[_github(w.MCP, "Work GitHub"), _github("mcp-2", "OSS GitHub")])
    ids = [f"mcp@{w.MCP}/list_prs", "mcp@mcp-2/list_prs"]
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    runtime = await w.first_use(await PipesHubRuntimeAssembler(
        w.Services(agent=agent), model_factory=w.model_factory(port)
    ).build(w.claim(w.pinned_spec(tool_ids=ids, mcp=[w.MCP, "mcp-2"]))))  # type: ignore[arg-type]
    tools = runtime.tools
    names = {tools.describe(n).tool_id: n for n in tools.names if n.startswith("mcp_")}  # type: ignore[attr-defined, union-attr]
    assert set(names) == set(ids)
    assert names[ids[0]] == scoped_mcp_tool_name("github", w.MCP, "list_prs")
    assert names[ids[1]] == scoped_mcp_tool_name("github", "mcp-2", "list_prs")
    schemas = {s.name: s.description for s in tools.schemas() or []}  # type: ignore[union-attr]
    assert schemas[names[ids[1]]].startswith("[OSS GitHub] ")
