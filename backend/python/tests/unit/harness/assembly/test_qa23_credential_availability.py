"""QA-23 (CG-23, TSC-G2; FR-2, FR-3, NFR-35): one item's credential does not decide the whole run.

Over ``RuntimeAssembler`` with the assembly test world: a run granted a Jira toolset and an MCP server.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from harness_core.driver import RunDriver, RunRefused
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolContext
from harness_core.testing import ManualClock, ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort

from . import world as w
from .test_assembler import JIRA_SEARCH, MCP_PRS, _build

if TYPE_CHECKING:
    from collections.abc import Iterator

_CTX = ToolContext("run-1", w.ORG, "t1.0", "att-1", "k")
_JIRA_KEY = f"/services/toolsets/{w.JIRA}/{w.ACTOR}"


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    with w.deployment():
        yield


def _disconnect(services: w.Services) -> None:
    """The user's disconnect: the record is gone and the store publishes the change."""
    services.config.answer("/services/toolsets/", lambda _path: None)
    services.config.notify(_JIRA_KEY)


async def test_qa23_4_a_missing_credential_makes_its_item_unavailable_and_the_run_goes_on() -> None:
    services = w.Services()
    services.config.answer("/services/toolsets/", lambda _path: None)
    runtime = await _build(services, tool_ids=[JIRA_SEARCH, MCP_PRS], mcp=[w.MCP])
    assert "mcp_github_list_prs" in set(runtime.tools.names)  # type: ignore[attr-defined]
    result = await runtime.tools.execute(ToolCall(id="c", name="jira__search_issues", arguments={"jql": "x"}), _CTX)
    assert result.status == "error" and "credential_missing" in str(result.content)
    assert "Workspace > Actions" in str(result.content), "the answer says where to connect it"


async def test_qa23_4_the_person_is_told_once_which_item_to_connect() -> None:
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock, lease_ms=30_000)
    journal.admit(
        "run-1", org_id=w.ORG, session_id="ses-1", spec=w.pinned_spec(tool_ids=[JIRA_SEARCH]), input_text="my issues?"
    )
    services = w.Services()
    services.config.answer("/services/toolsets/", lambda _path: None)
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="Connect Jira first."))])))
    driver = RunDriver(
        journal=journal,
        assembler=w.ClaimAssembler(PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port))),
        clock=clock,
    )
    done = await driver.run_claimed(await w.claim_next(journal))
    assert done.status == "completed", done
    notices = [e.data for e in journal.events("ses-1") if e.type == "x.pipeshub.run.notice"]
    assert notices == [{"code": "credential_missing", "item": "Jira", "kind": "toolset"}]


async def test_qa23_4_ws09_an_mcp_server_without_its_credential_stays_listed_and_the_run_goes_on() -> None:
    """Was "is left out": OWD-7 ("mask, don't remove") and WS-09 require the unconnected server's granted tools to
    stay listed, each answering how to connect it."""
    services = w.Services()
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr("app.agents.mcp.service.is_effective_auth_authenticated", lambda auth: False)
        runtime = await _build(services, tool_ids=[JIRA_SEARCH, MCP_PRS], mcp=[w.MCP])
    names = set(runtime.tools.names)  # type: ignore[attr-defined]
    assert {"jira__search_issues", "mcp_github_list_prs"} <= names
    assert runtime.tools.describe("mcp_github_list_prs").tool_id == MCP_PRS  # type: ignore[union-attr]


async def test_ws09_calling_an_unconnected_mcp_tool_says_where_to_connect_it() -> None:
    services = w.Services()
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr("app.agents.mcp.service.is_effective_auth_authenticated", lambda auth: False)
        runtime = await _build(services, tool_ids=[JIRA_SEARCH, MCP_PRS], mcp=[w.MCP])
        result = await runtime.tools.execute(
            ToolCall(id="c", name="mcp_github_list_prs", arguments={"repo": "x", "anything": 1}), _CTX
        )
    assert result.status == "error" and result.error_code == "auth"
    text = str(result.content)
    assert "GitHub" in text and "needs_authentication" in text
    assert "/workspace/mcp-servers/personal" in text, "the answer carries the link the person opens"
    jira = await runtime.tools.execute(ToolCall(id="j", name="jira__search_issues", arguments={"jql": "x"}), _CTX)
    assert jira.status == "ok", "the run goes on with what is connected"


async def test_ws09_an_unconnected_toolsets_call_carries_its_connect_link() -> None:
    services = w.Services()
    services.config.answer("/services/toolsets/", lambda _path: None)
    runtime = await _build(services, tool_ids=[JIRA_SEARCH])
    result = await runtime.tools.execute(ToolCall(id="c", name="jira__search_issues", arguments={"jql": "x"}), _CTX)
    assert result.status == "error" and result.error_code == "auth"
    assert "/workspace/actions" in str(result.content)


async def test_ws09_the_catalog_marks_both_kinds_needs_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.harness.assembly import assembler

    loaded: list[Any] = []
    original = assembler.load_run_tools

    async def spy(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        tools = await original(*args, **kwargs)
        loaded.append(tools)
        return tools

    monkeypatch.setattr(assembler, "load_run_tools", spy)
    services = w.Services()
    services.config.answer("/services/toolsets/", lambda _path: None)
    # Its own context: undone before the deployment's patch of the same name, which `monkeypatch` may outlive.
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr("app.agents.mcp.service.is_effective_auth_authenticated", lambda auth: False)
        _ = await _build(services, tool_ids=[JIRA_SEARCH, MCP_PRS], mcp=[w.MCP])
    catalog = loaded[0].catalog
    assert catalog.needs_authentication() == {"jira__search_issues", "mcp_github_list_prs"}
    observed = catalog.granted_digests()
    # WP-64: only the mark that it waits for a sign-in, never its stand-in descriptor's digest, so a later attempt
    # that finds the sign-in made (or withdrawn) is the same capability.
    from harness_core.kernel.durable.capability import NEEDS_CONNECT

    assert observed[f"tool:{MCP_PRS}"] == NEEDS_CONNECT
    assert observed[f"tool:{JIRA_SEARCH}"] == NEEDS_CONNECT


async def test_ws09_a_connected_run_marks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.harness.assembly import assembler

    loaded: list[Any] = []
    original = assembler.load_run_tools

    async def spy(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        tools = await original(*args, **kwargs)
        loaded.append(tools)
        return tools

    monkeypatch.setattr(assembler, "load_run_tools", spy)
    _ = await _build(w.Services(), tool_ids=[JIRA_SEARCH, MCP_PRS], mcp=[w.MCP])
    assert loaded[0].catalog.needs_authentication() == frozenset()


async def test_qa23_4_an_item_marked_required_still_refuses_admission() -> None:
    agent = w.agent_doc()
    for toolset in agent["toolsets"]:
        toolset["required"] = True
    services = w.Services(agent)
    services.config.answer("/services/toolsets/", lambda _path: None)
    with pytest.raises(RunRefused) as refused:
        _ = await _build(services, tool_ids=[JIRA_SEARCH])
    assert refused.value.error_type == "toolset_config_missing"


async def test_qa23_5_a_credential_revoked_mid_run_fails_the_next_call_typed() -> None:
    services = w.Services()
    runtime = await _build(services, tool_ids=[JIRA_SEARCH])
    first = await runtime.tools.execute(ToolCall(id="c1", name="jira__search_issues", arguments={"jql": "x"}), _CTX)
    assert first.status == "ok", first.content
    _disconnect(services)
    again = await runtime.tools.execute(ToolCall(id="c2", name="jira__search_issues", arguments={"jql": "y"}), _CTX)
    assert again.status == "error", again.content
    assert "credential" in str(again.content) and "secret-jira-token" not in str(again.content)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """A toolset liveness of this test's own, on a clock it moves."""
    from app.agents.agent_loop import instance_creator, mcp_liveness

    held = _Clock()
    fresh = mcp_liveness.CredentialLiveness(
        ttl_s=30.0, clock=held, watched=("/services/toolsets/",), voids_all=("/services/toolset-instances",)
    )
    monkeypatch.setattr(instance_creator, "toolset_liveness", fresh)
    return held


async def _jira(runtime: Any, call_id: str) -> Any:  # noqa: ANN401
    return await runtime.tools.execute(ToolCall(id=call_id, name="jira__search_issues", arguments={"jql": call_id}), _CTX)


async def test_qa23_f3_a_confirmed_credential_costs_the_call_no_read(clock: _Clock) -> None:
    services = w.Services()
    runtime = await _build(services, tool_ids=[JIRA_SEARCH])
    before = services.config.reads.count(_JIRA_KEY)
    assert (await _jira(runtime, "c1")).status == "ok"
    assert (await _jira(runtime, "c2")).status == "ok"
    assert services.config.reads.count(_JIRA_KEY) == before, "the load's read confirmed it"


async def test_qa23_f3_a_lost_notification_is_caught_after_the_ttl(clock: _Clock) -> None:
    services = w.Services()
    runtime = await _build(services, tool_ids=[JIRA_SEARCH])
    services.config.answer("/services/toolsets/", lambda _path: None)
    assert (await _jira(runtime, "c1")).status == "ok", "within the TTL the confirmation stands"
    clock.now = 31.0
    refused = await _jira(runtime, "c2")
    assert refused.status == "error" and "credential_unavailable" in str(refused.content)


async def test_qa23_f3_an_uninstalled_instance_voids_every_confirmation(clock: _Clock) -> None:
    services = w.Services()
    runtime = await _build(services, tool_ids=[JIRA_SEARCH])
    services.config.put("/services/toolset-instances", [])
    services.config.notify("/services/toolset-instances")
    refused = await _jira(runtime, "c1")
    assert refused.status == "error" and "Workspace > Actions" in str(refused.content)


async def test_qa23_f3_a_reconnect_mid_attempt_is_used_by_the_next_run_not_the_old_client(clock: _Clock) -> None:
    services = w.Services()
    runtime = await _build(services, tool_ids=[JIRA_SEARCH])
    _disconnect(services)
    assert (await _jira(runtime, "c1")).status == "error"
    services.config.answer("/services/toolsets/", lambda _key: {"isAuthenticated": True, "auth": {"token": "new"}})
    services.config.notify(_JIRA_KEY)
    again = await _jira(runtime, "c2")
    assert again.status == "error", "the tools still hold the client built with the disconnected credential"
    fresh = await _build(services, tool_ids=[JIRA_SEARCH])
    assert (await _jira(fresh, "c3")).status == "ok"
