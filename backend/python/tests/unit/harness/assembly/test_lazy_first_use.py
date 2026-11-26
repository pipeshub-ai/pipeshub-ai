"""R26-LAZY stage (b), ADR-OWD-07 D-LAZY-3..6: a run's MCP server is listed at its first use, and pinned there.

The claim lists no server: its granted tools are named and deferred. The first time the run loads one (a tool
search answering it) or calls one, the server is listed once for the attempt; what that listing served is journalled
with the outcome as the run's pin, and a resumed attempt holds the server to it. A server whose first listing fails
is told then, and the run goes on without it. MCP stays behind the tool search whatever the grant's size.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.agents.agent_loop.mcp_lazy import NOTICE, PINS_SLOT
from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from harness_core.journal.types import JournalEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolContext
from harness_core.kernel.durable.ports import ModelCall
from harness_core.kernel.durable.slots import PRE_RUN_SLOT_PREFIX
from harness_core.kernel.fold import RunState
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.driver.run_driver import AssembledRuntime

MCP_PRS = f"mcp@{w.MCP}/list_prs"
JIRA_SEARCH = f"jira@{w.JIRA}/search_issues"
PRS = "mcp_github_list_prs"
CTX = ToolContext("run-1", w.ORG, "t1.0", "att-1", "k")


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    with w.deployment():
        yield


@pytest.fixture
def listings(_deployment: None) -> Iterator[list[str]]:
    """Every listing a server is asked for, by instance (the world's session discovery).

    Patched over the world's own patch and undone before it (R27SEAM-FU-1): ``monkeypatch`` is set up by the root
    conftest before ``_deployment``, so its undo ran after the world's and put the world's fake listing back on
    ``MCPSessionManager`` for every later test of the process."""
    seen: list[str] = []
    real = MCPSessionManager.tools

    async def counted(self: MCPSessionManager, server: Any, namespace: str) -> Any:  # noqa: ANN401
        seen.append(server.instance_id)
        return await real(self, server, namespace)

    with patch.object(MCPSessionManager, "tools", counted):
        yield seen


async def _build(**spec: object) -> AssembledRuntime:
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port))
    return await assembler.build(w.claim(w.pinned_spec(**spec)))  # type: ignore[arg-type]


async def test_fu1_the_claim_lists_no_server_and_defers_its_granted_tools(listings: list[str]) -> None:
    runtime = await _build(tool_ids=[JIRA_SEARCH, MCP_PRS], mcp=[w.MCP])
    assert listings == []
    assert PRS in runtime.tools.names  # type: ignore[attr-defined]
    assert runtime.tools.describe(PRS) is None, "nothing reads a deferred tool's effect before its first use"
    assert runtime.capability_check is not None
    observed = await runtime.capability_check.observe()
    assert not [k for k in observed if "mcp" in k], "a lazy server is not observed at an attempt's start"


async def test_fu2_a_search_that_answers_the_server_lists_it_once_and_journals_the_pin(listings: list[str]) -> None:
    runtime = await _build(tool_ids=[JIRA_SEARCH, MCP_PRS], mcp=[w.MCP])
    found = await runtime.tools.execute(
        ToolCall(id="c1", name="search_tools", arguments={"query": "pull requests"}), CTX
    )
    assert found.status == "ok"
    matches = {m["name"]: m for m in json.loads(str(found.content))["matches"]}
    assert matches[PRS]["inputSchema"]["properties"] == {"repo": {"type": "string"}}
    assert listings == [w.MCP]
    pins = found.slots[PINS_SLOT]
    assert set(pins) == {w.MCP} and set(pins[w.MCP]) == {"list_prs"}  # type: ignore[index, arg-type]
    again = await runtime.tools.execute(
        ToolCall(id="c2", name="search_tools", arguments={"query": "pull requests"}), CTX
    )
    assert listings == [w.MCP], "one listing per attempt"
    assert PINS_SLOT not in again.slots, "the pin is journalled once"
    assert runtime.tools.describe(PRS).tool_id == MCP_PRS  # type: ignore[union-attr]


async def test_fu3_a_call_with_no_search_first_lists_the_server_before_it_runs(listings: list[str]) -> None:
    runtime = await _build(tool_ids=[MCP_PRS], mcp=[w.MCP])
    assert runtime.prepare_calls is not None
    await runtime.prepare_calls([PRS])
    assert listings == [w.MCP]
    effect = runtime.tools.describe(PRS)
    assert effect is not None and effect.tool_id == MCP_PRS
    ran = await runtime.tools.execute(ToolCall(id="c1", name=PRS, arguments={"repo": "a/b"}), CTX)
    # The world's server is reachable for listings only: the call reached the tool (not a refusal of an unknown one).
    assert ran.status == "error" and "Couldn't reach" in str(ran.content)
    assert set(ran.slots[PINS_SLOT]) == {w.MCP}  # type: ignore[arg-type]
    assert listings == [w.MCP]


def _saved(pins: dict[str, dict[str, str]]) -> JournalEvent:
    return JournalEvent(
        org_id=w.ORG, session_id="ses-1", stream_id="run-1", seq=1, run_id="run-1", type="loop.state_saved",
        critical=True, ts="2026-11-11T00:00:00Z", writer="worker", data={"slots": {PINS_SLOT: pins}},
    )


async def test_fu4_a_resumed_attempt_refuses_a_tool_that_changed_since_its_first_use(listings: list[str]) -> None:
    runtime = await _build(tool_ids=[MCP_PRS], mcp=[w.MCP])
    assert runtime.restore is not None
    await runtime.restore.restore([_saved({w.MCP: {"list_prs": "sha256:" + "0" * 64}})])
    refused = await runtime.tools.execute(ToolCall(id="c1", name=PRS, arguments={"repo": "a/b"}), CTX)
    assert refused.status == "error"
    assert "changed the tool list_prs since this chat first used it" in str(refused.content)
    assert listings == [w.MCP]
    assert PINS_SLOT not in refused.slots, "the run keeps its first pin"


async def test_fu4_a_resumed_attempt_runs_a_tool_held_at_its_pin(listings: list[str]) -> None:
    first = await _build(tool_ids=[MCP_PRS], mcp=[w.MCP])
    assert first.prepare_calls is not None
    await first.prepare_calls([PRS])
    pinned = (await first.tools.execute(ToolCall(id="c1", name=PRS, arguments={}), CTX)).slots[PINS_SLOT]
    resumed = await _build(tool_ids=[MCP_PRS], mcp=[w.MCP])
    assert resumed.restore is not None
    await resumed.restore.restore([_saved(pinned)])  # type: ignore[arg-type]
    ran = await resumed.tools.execute(ToolCall(id="c2", name=PRS, arguments={"repo": "a/b"}), CTX)
    assert "Couldn't reach" in str(ran.content), "the call reached the tool at its pin"


async def test_fu5_a_failed_first_listing_is_told_and_the_run_goes_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(w.MCP_TOOLS, w.MCP, [])
    runtime = await _build(tool_ids=[JIRA_SEARCH, MCP_PRS], mcp=[w.MCP])
    refused = await runtime.tools.execute(ToolCall(id="c1", name=PRS, arguments={}), CTX)
    assert refused.status == "error" and "didn't answer" in str(refused.content)
    notices = [e.data for e in refused.events if e.type == NOTICE]
    assert notices == [{"code": "mcp_unavailable", "item": "GitHub", "reason": "unreachable"}]
    ok = await runtime.tools.execute(ToolCall(id="c2", name="jira__search_issues", arguments={"jql": "x"}), CTX)
    assert ok.status == "ok"


async def test_r27_seam_a_server_waiting_for_approval_is_told_with_its_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """The admin's link in the chat opens the server to approve: the notice names its instance (an opaque id)."""
    runtime = await _build(tool_ids=[MCP_PRS], mcp=[w.MCP])

    async def unapproved(self: MCPToolProvider, server: Any, context: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        return [], "mcp_tool_unapproved"

    monkeypatch.setattr(MCPToolProvider, "list_server", unapproved)
    refused = await runtime.tools.execute(ToolCall(id="c1", name=PRS, arguments={}), CTX)
    assert refused.status == "error"
    notices = [e.data for e in refused.events if e.type == NOTICE]
    assert notices == [{"code": "mcp_unavailable", "item": "GitHub", "reason": "not_approved", "instance": w.MCP}]


async def test_fu6_tools_the_scanner_holds_at_first_listing_are_told(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = await _build(tool_ids=[MCP_PRS], mcp=[w.MCP])
    real = MCPToolProvider.list_server

    async def flagged(self: MCPToolProvider, server: Any, context: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        found = await real(self, server, context, *args, **kwargs)
        context.mcp_tool_load_failures.append({
            "instanceId": server.instance_id, "name": server.name, "reason": "mcp_tool_description_flagged",
            "tools": ["delete_repo"],
        })
        return found

    monkeypatch.setattr(MCPToolProvider, "list_server", flagged)
    ran = await runtime.tools.execute(ToolCall(id="c1", name=PRS, arguments={}), CTX)
    assert [e.data for e in ran.events if e.type == NOTICE] == [
        {"code": "mcp_tool_held", "item": "GitHub", "tools": ["delete_repo"]}
    ]


async def test_fu7_mcp_stays_behind_the_search_under_the_threshold_and_other_tools_stay_listed() -> None:
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    claim = w.claim(w.pinned_spec(tool_ids=[JIRA_SEARCH, MCP_PRS], mcp=[w.MCP]), query="find the bug")
    runtime = await PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port)).build(claim)
    slots: dict[str, Any] = {}
    for step in runtime.pre_run:
        slots[PRE_RUN_SLOT_PREFIX + step.key] = await step.run("find the bug")
    shaped = await runtime.shaper.shape(  # type: ignore[union-attr]
        ModelCall([], None, None, slots), 1, RunState(run_id="run-1", slots=slots)
    )
    listed = {t.name for t in shaped.tools or []}
    assert PRS not in listed
    assert {"search_tools", "fetch_tools", "jira__search_issues"} <= listed



async def _whole(**spec: object) -> AssembledRuntime:
    """The default chat's grant: the server attached with no tool list, granted whole (D-LAZY-2)."""
    agent = w.agent_doc(mcpServers=[{"instanceId": w.MCP, "name": "GitHubMCP", "displayName": "GitHub", "typeId": "github"}])
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(w.Services(agent=agent), model_factory=w.model_factory(port))
    return await assembler.build(w.claim(w.pinned_spec(**spec)))  # type: ignore[arg-type]


async def test_fu9_a_server_granted_whole_is_listed_by_a_search_that_names_it_and_its_tools_join_the_run(
    listings: list[str],
) -> None:
    runtime = await _whole(tool_ids=[JIRA_SEARCH], whole=[w.MCP])
    assert listings == [], "a cold server granted whole is not listed at the claim"
    assert PRS not in runtime.tools.names  # type: ignore[attr-defined]
    found = await runtime.tools.execute(
        ToolCall(id="c1", name="search_tools", arguments={"query": "GitHub pull requests"}), CTX
    )
    assert listings == [w.MCP]
    assert PRS in {m["name"] for m in json.loads(str(found.content))["matches"]}
    assert set(found.slots[PINS_SLOT]) == {w.MCP}  # type: ignore[arg-type]
    effect = runtime.tools.describe(PRS)
    assert effect is not None and effect.tool_id == MCP_PRS
    # The kernel unwraps call_tool before the port runs the call.
    ran = await runtime.tools.execute(ToolCall(id="c2", name=PRS, arguments={}), CTX)
    assert "Couldn't reach" in str(ran.content), "the grown tool runs, at the pin its listing gave"


async def test_fu10_a_search_that_does_not_name_a_cold_server_lists_nothing(listings: list[str]) -> None:
    runtime = await _whole(tool_ids=[JIRA_SEARCH], whole=[w.MCP])
    await runtime.tools.execute(ToolCall(id="c1", name="search_tools", arguments={"query": "weather forecast"}), CTX)
    assert listings == []


async def test_fu11_a_call_by_the_servers_own_tool_name_is_a_first_use(listings: list[str]) -> None:
    """R27REST-FU-2: a call naming a granted server's tool by its bare name (a CLI's ``ph tools call list_prs``, a name
    from the source list) lists the server once, builds the tool and runs it under its own name."""
    runtime = await _build(tool_ids=[MCP_PRS], mcp=[w.MCP])
    assert runtime.prepare_calls is not None
    renamed = await runtime.prepare_calls(["list_prs"])
    assert renamed == {"list_prs": PRS}
    assert listings == [w.MCP]
    ran = await runtime.tools.execute(ToolCall(id="c1", name="list_prs", arguments={"repo": "a/b"}), CTX)
    assert "Couldn't reach" in str(ran.content), "the call reached the tool"
    assert listings == [w.MCP]


async def test_fu12_a_server_granted_whole_is_listed_by_a_direct_call_of_one_of_its_tools(listings: list[str]) -> None:
    runtime = await _whole(tool_ids=[JIRA_SEARCH], whole=[w.MCP])
    ran = await runtime.tools.execute(ToolCall(id="c1", name="list_prs", arguments={}), CTX)
    assert listings == [w.MCP]
    assert "Couldn't reach" in str(ran.content), ran.content
    assert set(ran.slots[PINS_SLOT]) == {w.MCP}  # type: ignore[arg-type]


async def test_fu13_a_name_no_granted_server_has_stays_refused(listings: list[str]) -> None:
    runtime = await _build(tool_ids=[MCP_PRS], mcp=[w.MCP])
    refused = await runtime.tools.execute(ToolCall(id="c1", name="delete_everything", arguments={}), CTX)
    assert refused.status == "error"
    assert listings == [], "a name the granted servers' known tools do not hold lists nothing"


async def test_fu14_the_goal_steps_grounding_check_holds_the_runs_lazy_servers(listings: list[str]) -> None:
    """QAFINAL-FU-GROUNDLAZY: the grounding check reaches a lazy server's search through the attempt's own first-use
    path; built without it, the check saw no MCP search at all and named only the person's knowledge."""
    from app.harness.assembly.loop import GoalStep

    runtime = await _build(tool_ids=[MCP_PRS], mcp=[w.MCP])
    (step,) = [s for s in runtime.pre_run if isinstance(s, GoalStep)]
    lazy = step._lazy  # pyright: ignore[reportPrivateUsage]
    assert lazy is not None and lazy.owns(PRS)
    assert listings == []
