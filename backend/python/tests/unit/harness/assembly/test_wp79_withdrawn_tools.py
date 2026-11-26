"""WP79-CLAIM: a claim whose MCP server answers its listing without a tool admission granted (every
default-chat turn failed ``capability_lost`` after 10.5 minutes because Atlassian changed its ``search`` tool).

The real assembler builds the run through the real MCP loader with the hub on: the hub's listing (the only fake, at
``discover_listing_for``) is reviewed against the admin's approvals in configuration (``admit_tools``) exactly as on
a real stack. A tool the server no longer offers as approved is withheld from the attempt instead of failing it, and observed
as withdrawn by its server, so a run that had it on an earlier attempt fails naming the server (WP79-LOST). A server
that does not answer is still an outage, re-checked on the same pin (PA-7).
"""

from __future__ import annotations

import logging
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
from harness_core.journal.types import RunNotClaimable
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

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.driver.run_driver import AssembledRuntime

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
MCP_PRS, MCP_SEARCH = f"mcp@{w.MCP}/list_prs", f"mcp@{w.MCP}/search"


@pytest.fixture(autouse=True)
def _listed_at_the_claim() -> Iterator[None]:
    """WP-79's checks are the claim's: they hold where a run lists its MCP servers at the claim (lazy disclosure off). A run that lists them at
    first use holds them to the same pins then (``test_lazy_first_use.py``)."""
    with w.claim_listing():
        yield


@pytest.fixture(autouse=True)
def _hub() -> Iterator[list[MCPToolInfo]]:
    """The hub on for the claim, its listing what ``listed`` holds (empty: the server does not answer)."""
    listed: list[MCPToolInfo] = [PRS, SEARCH]

    async def listing(config: Any, credentials: Any, **_: Any) -> tuple[list[MCPToolInfo], dict[str, Any]]:  # noqa: ANN401
        if not listed:
            raise ConnectionError("connection refused")
        return [t.model_copy() for t in listed], {t.name: descriptor_of(t) for t in listed}

    with w.deployment(), \
         patch("app.harness.assembly.assembler.mcp_hub_enabled", return_value=True), \
         patch("app.agents.agent_loop.mcp_tool_loader.hub_enabled", return_value=True), \
         patch("app.agents.agent_loop.mcp_tool_loader.discover_listing_for", listing):
        yield listed


def _services() -> w.Services:
    server = {
        "instanceId": w.MCP, "name": "GitHubMCP", "displayName": "GitHub", "typeId": "github",
        "tools": [{"name": "list_prs", "fullName": "mcp_github_list_prs"}, {"name": "search", "fullName": "mcp_github_search"}],
    }
    return w.Services(w.agent_doc(mcpServers=[server]))


def _admitted_spec() -> RunSpec:
    """What admission pinned while the server offered both tools as approved."""
    approved = {"list_prs": descriptor_of(PRS).digest(), "search": descriptor_of(SEARCH).digest()}
    raw = dump_contract(w.pinned_spec(tool_ids=[MCP_PRS, MCP_SEARCH], mcp=[w.MCP]))
    raw["schemaVersion"] = 3
    for grant in raw["grants"]["tools"]:
        grant.update(descriptorDigest=approved[grant["toolId"].rsplit("/", 1)[1]], digestVersion=2)
    raw["extensions"]["x.pipeshub"]["mcp"][0].update(
        manifestDigest=ApprovedManifest(approved, "p_admin", 1).granted_digest(sorted(approved)), digestVersion=2
    )
    return validate_contract(RunSpec, raw)


async def _build(services: w.Services) -> AssembledRuntime:
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port))  # type: ignore[arg-type]
    return await assembler.build(w.claim(_admitted_spec()))


async def _observed(runtime: AssembledRuntime) -> dict[str, Any]:
    assert runtime.capability_check is not None
    try:
        return dict(await runtime.capability_check.observe())
    finally:
        if runtime.close is not None:
            await runtime.close()


async def test_wp79_claim_0_both_tools_listed_as_approved_build_on_their_pins() -> None:
    observed = await _observed(await _build(_services()))
    assert isinstance(observed[f"tool:{MCP_PRS}"], str)
    assert isinstance(observed[f"tool:{MCP_SEARCH}"], str)


async def test_wp79_claim_1_a_tool_the_server_no_longer_lists_is_withheld_not_fatal(
    _hub: list[MCPToolInfo], caplog: pytest.LogCaptureFixture
) -> None:
    _hub.remove(SEARCH)
    with caplog.at_level(logging.INFO):
        observed = await _observed(await _build(_services()))
    assert isinstance(observed[f"tool:{MCP_PRS}"], str)
    assert observed[f"tool:{MCP_SEARCH}"] == withdrawn_mark("GitHub")
    assert any("run.tools_withheld" in r.getMessage() and MCP_SEARCH in r.getMessage() for r in caplog.records)


async def test_wp79_claim_2_a_tool_whose_schema_changed_upstream_is_withheld_and_never_offered(
    _hub: list[MCPToolInfo],
) -> None:
    # The recorded case: the hub held `search` as changed since the admin approved it.
    _hub[1] = SEARCH.model_copy(update={"input_schema": {"type": "object", "properties": {"cql": {"type": "string"}}}})
    runtime = await _build(_services())
    assert runtime.capability_check is not None
    catalog = runtime.capability_check._catalog  # type: ignore[attr-defined]
    assert MCP_SEARCH not in {t.tool_id for t in catalog.tools}
    names = list(catalog.registry.names())
    assert "mcp_github_list_prs" in names and "mcp_github_search" not in names
    observed = await _observed(runtime)
    assert observed[f"tool:{MCP_SEARCH}"] == withdrawn_mark("GitHub")


async def test_wp79_claim_3_a_server_that_does_not_answer_is_still_rechecked_on_the_same_pin(
    _hub: list[MCPToolInfo],
) -> None:
    _hub.clear()
    with pytest.raises(CapabilityLost) as lost:
        await _build(_services())
    assert f"tool:{MCP_SEARCH}" in lost.value.missing and f"tool:{MCP_PRS}" in lost.value.missing


class _Died(BaseException):
    pass


def _driven(port: ScriptedModelPort) -> tuple[Any, RunDriver, ManualClock]:
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock, lease_ms=30_000)
    journal.admit("run-1", org_id=w.ORG, session_id="ses-1", spec=_admitted_spec(), input_text="find the bug")
    assembler = PipesHubRuntimeAssembler(_services(), model_factory=w.model_factory(port))  # type: ignore[arg-type]
    return journal, RunDriver(journal=journal, assembler=w.ClaimAssembler(assembler), clock=clock), clock


async def test_wp79_smoke_1_a_turn_whose_server_dropped_a_tool_it_never_used_answers_at_once(
    _hub: list[MCPToolInfo],
) -> None:
    # The reproduction: admission saw `search`, the server's listing answers without it.
    _hub.remove(SEARCH)
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    journal, driver, _ = _driven(port)

    done = await driver.run_claimed(await w.claim_next(journal))

    assert done.status == "completed", done
    assert len(port.calls) == 1
    assert journal.status("run-1") == "completed"


async def test_wp79_smoke_2_a_run_that_had_the_tool_on_an_earlier_attempt_fails_at_once_naming_the_server(
    _hub: list[MCPToolInfo],
) -> None:
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
    journal, driver, clock = _driven(port)
    registry.arm("worker.model.after_response_commit", Raise(_Died))
    try:
        with pytest.raises(_Died):
            _ = await driver.run_claimed(await w.claim_next(journal))
    finally:
        registry.reset()
    _ = clock.advance(31)
    _ = journal.sweep()
    _hub.remove(SEARCH)

    lost = await driver.run_claimed(await w.claim_next(journal))

    assert (lost.status, lost.reason) == ("failed", TOOL_WITHDRAWN)
    (failed,) = [e for e in journal.events("ses-1") if e.type == "run.failed"]
    assert "GitHub" in failed.data["message"] and w.MCP not in failed.data["message"]
    assert len(port.calls) == 1


async def test_wp79_return_1_a_tool_withheld_at_the_first_attempt_stays_withheld_when_its_server_lists_it_again(
    _hub: list[MCPToolInfo], caplog: pytest.LogCaptureFixture
) -> None:
    # WP79-FU-5: the run never held `search`; its server lists it again before the next attempt. That is no change:
    # the run goes on without it, and the next run offers it.
    port = ScriptedModelPort(
        ScriptedModel(
            ModelScript(
                id="t",
                turns=[
                    Turn(reply=Reply(tool_calls=[ToolCallSpec(name="mcp_github_search", args={"q": "bug"})])),
                    Turn(reply=Reply(text="done")),
                ],
            )
        )
    )
    _hub.remove(SEARCH)
    journal, driver, clock = _driven(port)
    registry.arm("worker.model.after_response_commit", Raise(_Died))
    try:
        with pytest.raises(_Died):
            _ = await driver.run_claimed(await w.claim_next(journal))
    finally:
        registry.reset()
    _ = clock.advance(31)
    _ = journal.sweep()
    _hub.append(SEARCH)

    with caplog.at_level(logging.INFO):
        done = await driver.run_claimed(await w.claim_next(journal))

    assert done.status == "completed", done
    events = journal.events("ses-1")
    assert [e for e in events if e.type == "run.yielded"] == [], "no capability wait"
    offered = {tool.name for call in port.calls for tool in call.tools or ()}
    assert "mcp_github_search" not in offered and "mcp_github_list_prs" in offered
    (outcome,) = [e for e in events if e.type == "tool.outcome"]
    assert outcome.data["status"] == "error", "the call planned without the tool is not sent to the server"
    assert any("run.tools_withheld" in r.getMessage() and MCP_SEARCH in r.getMessage() for r in caplog.records)


async def test_wp79_return_3_a_tool_the_run_held_withdrawn_then_returned_still_fails_at_once(
    _hub: list[MCPToolInfo],
) -> None:
    # D3 unchanged: held at the first attempt, gone at the second, the run fails naming the server; its return at a
    # later attempt does not undo that.
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
    journal, driver, clock = _driven(port)
    registry.arm("worker.model.after_response_commit", Raise(_Died))
    try:
        with pytest.raises(_Died):
            _ = await driver.run_claimed(await w.claim_next(journal))
    finally:
        registry.reset()
    _ = clock.advance(31)
    _ = journal.sweep()
    _hub.remove(SEARCH)

    lost = await driver.run_claimed(await w.claim_next(journal))
    _hub.append(SEARCH)

    assert (lost.status, lost.reason) == ("failed", TOOL_WITHDRAWN)
    assert journal.status("run-1") == "failed", "its return does not reopen the run"
    with pytest.raises(RunNotClaimable):
        await w.claim_next(journal)


async def test_wp79_return_4_withholding_takes_the_tool_out_of_the_attempt(_hub: list[MCPToolInfo]) -> None:
    runtime = await _build(_services())
    check = runtime.capability_check
    assert check is not None
    try:
        await check.withhold({f"tool:{MCP_SEARCH}": withdrawn_mark("GitHub")})
        assert "mcp_github_search" not in {s.name for s in runtime.tools.schemas() or ()}
        observed = dict(await check.observe())
    finally:
        if runtime.close is not None:
            await runtime.close()
    assert observed[f"tool:{MCP_SEARCH}"] == withdrawn_mark("GitHub")
    assert isinstance(observed[f"tool:{MCP_PRS}"], str)
    _hub.remove(SEARCH)
    without = await _observed(await _build(_services()))
    assert observed == without, "an attempt that withholds a returned tool observes what one built without it did"
