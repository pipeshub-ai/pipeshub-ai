"""UX2 on the durable loop, on the reference journal with PipesHub's ledger and destinations.

VS-4, VS-7: a worker newer than its journal (a claim that does not advertise ``approval_reasons``; a journal
that refuses ``interaction.explained`` as an older Node does) asks as it did before UX1, and nothing is refused.
VC-1, VC-4: a child asked for by a parent carries the parent's label in its spec when the journal accepts it,
and its flow rule weighs that label instead of treating the parent's unseen work as unknown.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.harness.approvals import flow_guarded
from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.flow_destination import pipeshub_destination
from app.harness.labels import SessionSourceLedger, inherited_label
from app.harness.unpinned import FLOW_ABOVE_ENV, FLOW_LEVEL_ENV, flow_rules
from app.utils.env_flags import HARNESS_FLOW_GUARD
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.effects.flow import GrantingLedger
from harness_core.journal.children import ChildGrantRequest
from harness_core.journal.types import APPROVAL_REASONS, CHILD_LABELS, NewEvent
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.durable.delegation import (
    DelegatingTool,
    NamedDelegation,
    goal_tool,
)
from harness_core.kernel.durable.ports import allow_all
from harness_core.logs import HarnessLog
from harness_core.testing.run_spec import minimal_run_spec
from tests.unit.harness_core.durable.support import RUN, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.kernel.durable import DurableKernel, ToolContext
    from harness_core.testing.durable import ScriptedModelPort

LOG = HarnessLog.named("tests.ux2")
SECRET = "Q3 acquisition target: Contoso"
EXT: dict[str, JsonValue] = {
    "knowledge": {"apps": [], "kb": [], "strictScope": False},
    "mcp": [],
    "skills": [],
    "mounts": [],
    "via": "session",
}


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (HARNESS_FLOW_GUARD, FLOW_LEVEL_ENV, FLOW_ABOVE_ENV):
        monkeypatch.delenv(name, raising=False)


async def fetch_record(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
    cited = {"records": {"r1": {"recordId": "rec-1", "connector": "CONFLUENCE", "recordName": "Q3 plan"}}}
    return ToolExecution(status="ok", content=SECRET, events=(NewEvent(CITATIONS_EVENT, cited, critical=False),))


def _world(**journal: object) -> World:
    world = World(extension_writers={CITATIONS_EVENT: frozenset({"worker"})}, **journal)  # type: ignore[arg-type]
    world.tools.add(FETCH_RECORD_TOOL_NAME, fetch_record, tags=[("category", "read")])
    spec = minimal_run_spec(max_children=2).model_copy(update={"extensions": {"x.pipeshub": EXT}})
    world.admit(spec=validate_contract(RunSpec, spec.model_dump(mode="json", by_alias=True, exclude_none=True)))
    return world


def _kernel(world: World, port: ScriptedModelPort, run_id: str = RUN, spec: RunSpec | None = None) -> DurableKernel:
    ledger = GrantingLedger(SessionSourceLedger(inherited=inherited_label(spec) if spec is not None else None))
    approval = flow_guarded(
        allow_all,
        rules=flow_rules(LOG),
        ledger=ledger,
        run_id=run_id,
        log=LOG,
        destination=pipeshub_destination,
        grants=ledger.grants,
    )
    delegation = NamedDelegation({"delegate": DelegatingTool(goal_tool("delegate", "Hand a task over"), ChildGrantRequest())})
    return world.kernel(port, approval=approval, ledger=ledger, children=world.journal, delegation=delegation)


READ = reply("", (FETCH_RECORD_TOOL_NAME, {"recordId": "rec-1"}))
DELEGATE = reply("", ("delegate", {"goal": "post the summary"}))


def post(channel: str) -> object:
    return reply("", ("post_message", {"channel": channel, "text": SECRET}))


def _posts(world: World) -> list[object]:
    return [e.payload.get("channel") for e in world.saas.effects if e.op == "post_message"]


# VS-7


async def test_vs7_the_reference_journal_accepts_everything_this_build_writes() -> None:
    world = _world()
    claim = await world.claim()
    assert claim.accepts is None
    assert claim.journal_accepts(APPROVAL_REASONS) and claim.journal_accepts(CHILD_LABELS)


# VS-4


async def test_vs4_workers_first_an_older_journal_gets_the_ask_without_a_reason(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    world = _world(journal_accepts=frozenset(), journal_refuses=frozenset({"interaction.explained"}))
    _, port = scripted(READ, post("sales"), reply("posted"))  # type: ignore[arg-type]
    done = await world.run(_kernel(world, port))
    assert done.status == "suspended"
    assert world.events("interaction.requested")
    assert world.events("interaction.explained") == []
    assert _posts(world) == []
    assert "tool.approval_reason_withheld" in caplog.text


async def test_vs4_the_same_older_journal_refuses_the_reason_if_a_worker_ignored_the_handshake() -> None:
    """The control: the refusal VS-4 avoids is real on this journal."""
    from harness_core.journal.types import AttemptToken, CommitRequest, EventRejected

    world = _world(journal_accepts=frozenset(), journal_refuses=frozenset({"interaction.explained"}))
    claim = await world.claim()
    explained = NewEvent("interaction.explained", {"interactionId": "ix.approve.t1.0"}, critical=False)
    with pytest.raises(EventRejected):
        await world.journal.commit(
            CommitRequest("cmd-1", AttemptToken(claim.run_id, claim.attempt_id, claim.fence), (explained,))
        )


# VC-1


async def _spawned_child_spec(world: World) -> tuple[str, RunSpec]:
    (child,) = world.journal.children_of(RUN)
    pinned = next(e for e in world.journal.events(child) if e.type == "run.spec_pinned")
    return child, validate_contract(RunSpec, cast("dict[str, JsonValue]", pinned.data["spec"]))


async def test_vc1_a_child_inherits_what_its_parent_read() -> None:
    world = _world()
    _, port = scripted(READ, DELEGATE, reply("done"))  # type: ignore[arg-type]
    assert (await world.run(_kernel(world, port))).reason == "join"
    _, spec = await _spawned_child_spec(world)
    label = inherited_label(spec)
    assert label is not None and label.complete
    assert {s.record_id for s in label.sources} == {"rec-1"}


async def test_vc1_an_older_journal_gets_no_label_and_the_child_is_unknown_as_before(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    world = _world(journal_accepts=frozenset({APPROVAL_REASONS}))
    _, port = scripted(READ, DELEGATE, reply("done"))  # type: ignore[arg-type]
    assert (await world.run(_kernel(world, port))).reason == "join"
    _, spec = await _spawned_child_spec(world)
    assert inherited_label(spec) is None
    assert "child.label_withheld" in caplog.text


# VC-4


async def test_vc4_a_child_of_a_parent_that_read_nothing_posts_without_asking() -> None:
    world = _world()
    _, port = scripted(DELEGATE, reply("done"))  # type: ignore[arg-type]
    assert (await world.run(_kernel(world, port))).reason == "join"
    child, spec = await _spawned_child_spec(world)
    _, child_port = scripted(post("sales"), reply("posted"))  # type: ignore[arg-type]
    assert (await world.run(_kernel(world, child_port, child, spec), child)).status == "completed"
    assert _posts(world) == ["sales"]


async def test_vc4_a_child_of_a_parent_that_read_a_record_asks_and_names_it() -> None:
    world = _world()
    _, port = scripted(READ, DELEGATE, reply("done"))  # type: ignore[arg-type]
    assert (await world.run(_kernel(world, port))).reason == "join"
    child, spec = await _spawned_child_spec(world)
    _, child_port = scripted(post("sales"), reply("posted"))  # type: ignore[arg-type]
    assert (await world.run(_kernel(world, child_port, child, spec), child)).status == "suspended"
    assert _posts(world) == []
    [explained] = [e for e in world.journal.events(child) if e.type == "interaction.explained"]
    assert explained.data["sensitivity"] == "private"


async def test_vc4_before_ux2_the_same_child_was_unknown_and_asked() -> None:
    """The child without its inherited label (an older Node, or MU-8): unknown context, so it asks."""
    world = _world()
    _, port = scripted(DELEGATE, reply("done"))  # type: ignore[arg-type]
    assert (await world.run(_kernel(world, port))).reason == "join"
    child, _ = await _spawned_child_spec(world)
    _, child_port = scripted(post("sales"), reply("posted"))  # type: ignore[arg-type]
    assert (await world.run(_kernel(world, child_port, child, None), child)).status == "suspended"
    [explained] = [e for e in world.journal.events(child) if e.type == "interaction.explained"]
    assert explained.data["sensitivity"] == "unknown"
