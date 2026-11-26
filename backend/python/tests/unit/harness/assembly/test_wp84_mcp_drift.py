"""WP84-MCP: the MCP drift cases WP-79 left open (STATUS "WP-79 evidence", open 2 and 3).

The real assembler builds the run through the real MCP loader; the only fakes are the server's listing (the hub's
``discover_listing_for``, or the turn's ``MCPSessionManager.tools`` with the hub off) and configuration.

WP84-MCP-1 hub on: a server whose listing answers with none of the run's tools left (both removed; it still offers
           another approved tool the run was not given) withholds them, observed as withdrawn by the server, instead
           of reading as an outage.
WP84-MCP-2 hub on: a server that answers its listing with no tools at all is withdrawn the same way.
WP84-MCP-3 hub on: a run that had a tool on an earlier attempt, whose server then lists none of its tools, fails at
           once ``mcp_tool_withdrawn`` naming the server, with no re-check.
WP84-MCP-4 admission under the hub reads a listing that answered with no tool as answered: nothing is granted.
WP84-MCP-5 hub off: a tool whose schema changed upstream since admission is withheld at the claim (never registered,
           so never callable at the new digest) and observed as withdrawn; the server's other tool builds on its pin.
WP84-MCP-6 hub off: every granted tool of the server changed: all withheld, the server answered.
WP84-MCP-7 hub off: a run that had the tool on an earlier attempt fails at once ``mcp_tool_withdrawn`` naming the
           server, with no re-check (D3, as under the hub).
WP84-MCP-8 hub off: a server that does not answer is still an outage, re-checked on the same pin (PA-7).
WP84-MCP-14 the model is told the server no longer offers the agent's tools, in words, not as a load failure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.agents.mcp.models import MCPToolInfo
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.mcp_hub.manifests import descriptor_of
from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.driver import CapabilityLost, RunDriver
from harness_core.failpoints import Raise, registry
from harness_core.kernel.durable.capability import TOOL_WITHDRAWN, withdrawn_mark
from harness_core.mcp_hub.manifest import ApprovedManifest
from harness_core.testing import (
    ManualClock,
    ModelScript,
    Reply,
    ScriptedModel,
    ToolCallSpec,
    Turn,
)
from harness_core.testing.durable import ScriptedModelPort
from tests.support.mcp_tool_admission import approve_tools

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.agents.agent_loop.context import AgentContext
    from harness_core.driver.run_driver import AssembledRuntime



@pytest.fixture(autouse=True)
def _listed_at_the_claim() -> Iterator[None]:
    """WP-84's checks are the claim's: they hold where a run lists its MCP servers at the claim (lazy disclosure
    off). A run that lists them at first use holds them to the same pins then (``test_lazy_first_use.py``)."""
    with w.claim_listing():
        yield


PRS = MCPToolInfo(
    name="list_prs",
    namespaced_name="mcp_github_list_prs",
    description="List pull requests.",
    input_schema={"type": "object", "properties": {"repo": {"type": "string"}}},
)
SEARCH = MCPToolInfo(
    name="search",
    namespaced_name="mcp_github_search",
    description="Search code.",
    input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
)
OTHER = MCPToolInfo(
    name="list_issues",
    namespaced_name="mcp_github_list_issues",
    description="List issues.",
    input_schema={"type": "object", "properties": {"repo": {"type": "string"}}},
)
CHANGED_SEARCH = SEARCH.model_copy(update={"input_schema": {"type": "object", "properties": {"cql": {"type": "string"}}}})
MCP_PRS, MCP_SEARCH = f"mcp@{w.MCP}/list_prs", f"mcp@{w.MCP}/search"
SERVER = {
    "instanceId": w.MCP, "name": "GitHubMCP", "displayName": "GitHub", "typeId": "github",
    "tools": [{"name": "list_prs", "fullName": "mcp_github_list_prs"}, {"name": "search", "fullName": "mcp_github_search"}],
}


def _services(*, approved: bool) -> w.Services:
    services = w.Services(w.agent_doc(mcpServers=[SERVER]))
    if approved:
        approve_tools(w.MCP, (descriptor_of(PRS), descriptor_of(SEARCH), descriptor_of(OTHER)))
    return services


def _admitted_spec(by: str) -> RunSpec:
    """What admission pinned while the server offered both tools (``by``: who governed them, an admin or live)."""
    pinned = {"list_prs": descriptor_of(PRS).digest(), "search": descriptor_of(SEARCH).digest()}
    raw = dump_contract(w.pinned_spec(tool_ids=[MCP_PRS, MCP_SEARCH], mcp=[w.MCP]))
    raw["schemaVersion"] = 3
    for grant in raw["grants"]["tools"]:
        grant.update(descriptorDigest=pinned[grant["toolId"].rsplit("/", 1)[1]], digestVersion=2)
    at = 1 if by == "p_admin" else 0
    raw["extensions"]["x.pipeshub"]["mcp"][0].update(
        manifestDigest=ApprovedManifest(pinned, by, at).granted_digest(sorted(pinned)), digestVersion=2
    )
    return validate_contract(RunSpec, raw)


@pytest.fixture
def hub_listing() -> Iterator[list[MCPToolInfo]]:
    """The hub on, its listing what ``listed`` holds; ``None`` in it: the server does not answer."""
    listed: list[Any] = [PRS, SEARCH]

    async def listing(config: Any, credentials: Any, **_: Any) -> tuple[list[MCPToolInfo], dict[str, Any]]:  # noqa: ANN401
        if None in listed:
            raise ConnectionError("connection refused")
        return [t.model_copy() for t in listed], {t.name: descriptor_of(t) for t in listed}

    with w.deployment(), \
         patch("app.harness.assembly.assembler.mcp_hub_enabled", return_value=True), \
         patch("app.agents.agent_loop.mcp_tool_loader.hub_enabled", return_value=True), \
         patch("app.agents.agent_loop.mcp_tool_loader.discover_listing_for", listing):
        yield listed


@pytest.fixture
def live_listing(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[MCPToolInfo]]:
    """The hub off: the turn's own listing (``MCPSessionManager.tools``) is what ``listed`` holds; empty: the server
    does not answer (the world's fake refuses the connection)."""
    listed: list[MCPToolInfo] = [PRS, SEARCH]
    monkeypatch.setitem(w.MCP_TOOLS, w.MCP, listed)
    with w.deployment(), \
         patch("app.harness.assembly.assembler.mcp_hub_enabled", return_value=False), \
         patch("app.agents.agent_loop.mcp_tool_loader.hub_enabled", return_value=False):
        yield listed


async def _build(services: w.Services, spec: RunSpec) -> AssembledRuntime:
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port))  # type: ignore[arg-type]
    return await assembler.build(w.claim(spec))


async def _observed(runtime: AssembledRuntime) -> tuple[dict[str, Any], list[str], set[str]]:
    """The attempt's observation, its registry's names and its catalog's tool ids."""
    assert runtime.capability_check is not None
    catalog = runtime.capability_check._catalog  # type: ignore[attr-defined]
    try:
        return dict(await runtime.capability_check.observe()), list(catalog.registry.names()), {t.tool_id for t in catalog.tools}
    finally:
        if runtime.close is not None:
            await runtime.close()


class _Died(BaseException):
    pass


def _driven(services: w.Services, spec: RunSpec) -> tuple[Any, RunDriver, ManualClock, ScriptedModelPort]:
    port = ScriptedModelPort(
        ScriptedModel(
            ModelScript(
                id="t",
                turns=[
                    Turn(reply=Reply(tool_calls=[ToolCallSpec(name="mcp_github_search", args={"q": "bug"})])),
                    Turn(reply=Reply(text="never")),
                ],
            )
        )
    )
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock, lease_ms=30_000)
    journal.admit("run-1", org_id=w.ORG, session_id="ses-1", spec=spec, input_text="find the bug")
    assembler = PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port))  # type: ignore[arg-type]
    return journal, RunDriver(journal=journal, assembler=w.ClaimAssembler(assembler), clock=clock), clock, port


async def _first_attempt_dies_after_using_search(journal: Any, driver: RunDriver, clock: ManualClock) -> None:  # noqa: ANN401
    registry.arm("worker.model.after_response_commit", Raise(_Died))
    try:
        with pytest.raises(_Died):
            _ = await driver.run_claimed(await w.claim_next(journal))
    finally:
        registry.reset()
    _ = clock.advance(31)
    _ = journal.sweep()


def _assert_withdrawn_at_once(journal: Any, lost: Any) -> None:  # noqa: ANN401
    assert (lost.status, lost.reason) == ("failed", TOOL_WITHDRAWN)
    events = journal.events("ses-1")
    (failed,) = [e for e in events if e.type == "run.failed"]
    assert "GitHub" in failed.data["message"] and w.MCP not in failed.data["message"]
    assert not [e for e in events if e.type == "run.yielded" and e.data.get("reason") == "capability_wait"]


async def test_wp84_mcp_1_a_server_that_lists_none_of_the_approved_tools_withholds_them(
    hub_listing: list[MCPToolInfo],
) -> None:
    hub_listing[:] = [OTHER]
    observed, names, granted = await _observed(await _build(_services(approved=True), _admitted_spec("p_admin")))
    assert observed[f"tool:{MCP_PRS}"] == withdrawn_mark("GitHub")
    assert observed[f"tool:{MCP_SEARCH}"] == withdrawn_mark("GitHub")
    assert not {MCP_PRS, MCP_SEARCH} & granted
    assert "mcp_github_list_prs" not in names and "mcp_github_search" not in names


async def test_wp84_mcp_2_a_server_that_lists_no_tools_at_all_withholds_them(hub_listing: list[MCPToolInfo]) -> None:
    hub_listing.clear()
    observed, _, _ = await _observed(await _build(_services(approved=True), _admitted_spec("p_admin")))
    assert observed[f"tool:{MCP_PRS}"] == withdrawn_mark("GitHub")
    assert observed[f"tool:{MCP_SEARCH}"] == withdrawn_mark("GitHub")


async def test_wp84_mcp_3_a_run_that_had_a_tool_fails_at_once_when_its_server_lists_none(
    hub_listing: list[MCPToolInfo],
) -> None:
    journal, driver, clock, port = _driven(_services(approved=True), _admitted_spec("p_admin"))
    await _first_attempt_dies_after_using_search(journal, driver, clock)
    hub_listing.clear()

    lost = await driver.run_claimed(await w.claim_next(journal))

    _assert_withdrawn_at_once(journal, lost)
    assert len(port.calls) == 1


def _resolved_from(context: AgentContext) -> Any:  # noqa: ANN401
    """The attached server as the access resolver resolves it for the run's owner."""
    from app.agents.agent_loop.mcp_access import ResolvedMCPServer

    async def resolve(_: AgentContext) -> list[ResolvedMCPServer]:
        server = context.mcp_servers[0]
        return [
            ResolvedMCPServer(
                instance_id=w.MCP, name="GitHubMCP", display_name="GitHub", instance={
                    "_id": w.MCP, "orgId": w.ORG, "createdBy": w.ACTOR, "name": "GitHubMCP", "typeId": "github",
                    "transport": "sse", "authMode": "none",
                    "createdAt": 0,
                },
                auth={}, owner_id=w.ACTOR, credential_owner=w.ACTOR, attached_tools=server.get("tools"),
            )
        ]

    return resolve


async def test_wp84_mcp_5_hub_off_a_tool_changed_since_admission_is_withheld_never_registered(
    live_listing: list[MCPToolInfo],
) -> None:
    live_listing[1] = CHANGED_SEARCH
    observed, names, granted = await _observed(await _build(_services(approved=False), _admitted_spec("live")))
    assert observed[f"tool:{MCP_SEARCH}"] == withdrawn_mark("GitHub")
    assert isinstance(observed[f"tool:{MCP_PRS}"], str)
    assert MCP_PRS in granted and MCP_SEARCH not in granted
    assert "mcp_github_list_prs" in names and "mcp_github_search" not in names


async def test_wp84_mcp_6_hub_off_every_granted_tool_changed_withholds_all(live_listing: list[MCPToolInfo]) -> None:
    live_listing[:] = [PRS.model_copy(update={"description": "List pull requests, and close them."}), CHANGED_SEARCH]
    observed, names, _ = await _observed(await _build(_services(approved=False), _admitted_spec("live")))
    assert observed[f"tool:{MCP_PRS}"] == withdrawn_mark("GitHub")
    assert observed[f"tool:{MCP_SEARCH}"] == withdrawn_mark("GitHub")
    assert not [n for n in names if n.startswith("mcp_github_")]


async def test_wp84_mcp_7_hub_off_a_run_that_had_the_tool_fails_at_once_naming_the_server(
    live_listing: list[MCPToolInfo],
) -> None:
    journal, driver, clock, port = _driven(_services(approved=False), _admitted_spec("live"))
    await _first_attempt_dies_after_using_search(journal, driver, clock)
    live_listing[1] = CHANGED_SEARCH

    lost = await driver.run_claimed(await w.claim_next(journal))

    _assert_withdrawn_at_once(journal, lost)
    assert len(port.calls) == 1


async def test_wp84_mcp_8_hub_off_a_server_that_does_not_answer_is_rechecked_on_the_same_pin(
    live_listing: list[MCPToolInfo],
) -> None:
    live_listing.clear()
    with pytest.raises(CapabilityLost) as lost:
        await _build(_services(approved=False), _admitted_spec("live"))
    assert {f"tool:{MCP_PRS}", f"tool:{MCP_SEARCH}"} <= set(lost.value.missing)


def test_wp84_mcp_14_the_model_is_told_plainly_that_the_server_no_longer_offers_its_tools() -> None:
    from app.agents.agent_loop.mcp_tool_loader import NO_TOOLS_OFFERED
    from app.modules.agents.capability_summary import (
        _build_mcp_failures_section,  # pyright: ignore[reportPrivateUsage]
    )

    parts: list[str] = []
    _build_mcp_failures_section(
        {"mcp_tool_load_failures": [{"instanceId": w.MCP, "name": "GitHub", "reason": NO_TOOLS_OFFERED}]}, parts=parts
    )
    assert "- GitHub: it no longer offers the tools this agent was given" in "\n".join(parts)
