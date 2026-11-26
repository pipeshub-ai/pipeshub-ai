"""P6.4 CF-8: the workspace a run writes, read from its pinned spec; the
reference journal pins a child's fork as Node does (CF-1, CF-2, CF-6)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.journal import InMemoryJournal
from harness_core.journal.children import (
    ChildGrantRequest,
    ChildRequest,
    child_workspace,
    run_workspace,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.workspace import file
from harness_core.testing.run_spec import minimal_run_spec
from harness_core.workspace import ChangeSet
from harness_core.workspace.forks import session_scope
from harness_core.workspace.memory import InMemoryWorkspaceStore

if TYPE_CHECKING:
    from harness_core.journal.types import AttemptToken

EXTENSION: dict[str, Any] = {
    "knowledge": {"apps": [], "kb": [], "strictScope": False},
    "mcp": [],
    "skills": [],
    "mounts": ["/workspace"],
    "via": "session",
}


def _spec(**extension: Any) -> RunSpec:  # noqa: ANN401
    spec = dump_contract(minimal_run_spec(max_children=2))
    spec["extensions"] = {"x.pipeshub": {**EXTENSION, **extension}}
    return validate_contract(RunSpec, spec)


def test_cf8_the_workspace_a_run_writes() -> None:
    assert run_workspace(_spec(), "s1") == "ws:s1"
    assert run_workspace(_spec(mounts=[]), "s1") is None
    assert run_workspace(minimal_run_spec(), "s1") is None
    pinned = _spec(workspace={"workspaceId": "ws:s1:fork:c1", "forkOf": {"workspaceId": "ws:s1", "revision": 3}})
    assert run_workspace(pinned, "s1") == "ws:s1:fork:c1"
    fork = child_workspace(pinned)
    assert fork is not None and (fork.workspace_id, fork.fork_of, fork.revision) == ("ws:s1:fork:c1", "ws:s1", 3)
    assert child_workspace(_spec()) is None


async def _claimed(journal: InMemoryJournal, run_id: str) -> AttemptToken:
    claim = await journal.claim(run_id, worker_id="w")
    assert claim is not None
    return claim.token


async def test_cf1_cf2_cf6_the_reference_journal_forks_as_node_does() -> None:
    clock = ManualClock(datetime(2026, 10, 3, tzinfo=UTC))
    store = InMemoryWorkspaceStore(clock=clock)
    journal = InMemoryJournal(clock=clock, workspaces=store)
    journal.admit("parent", org_id="o", session_id="s1", spec=_spec(), input_text="go")
    scope = session_scope("o", "s1")
    _ = await store.commit(scope, "ws:s1", base_revision=0, changes=ChangeSet.of(file("a", "a")), reason="seed")
    parent = await _claimed(journal, "parent")
    forked = await journal.spawn(parent, ChildRequest("t1.0", "edit", ChildGrantRequest(max_children=1)))
    bare = await journal.spawn(parent, ChildRequest("t2.0", "read", ChildGrantRequest(mounts=())))
    child_spec = validate_contract(RunSpec, journal._runs[forked.child_run_id].spec)  # pyright: ignore[reportPrivateUsage]
    pin = child_workspace(child_spec)
    assert pin is not None
    assert (pin.workspace_id, pin.fork_of, pin.revision) == (f"ws:s1:fork:{forked.child_run_id}", "ws:s1", 1)
    assert set((await store.head(scope, pin.workspace_id)).files) == {"a"}
    bare_spec = validate_contract(RunSpec, journal._runs[bare.child_run_id].spec)  # pyright: ignore[reportPrivateUsage]
    assert child_workspace(bare_spec) is None
    assert (await journal.spawn(parent, ChildRequest("t1.0", "edit", ChildGrantRequest(max_children=1)))).created is False

    child = await _claimed(journal, forked.child_run_id)
    grand = await journal.spawn(child, ChildRequest("g1", "deeper"))
    grand_spec = validate_contract(RunSpec, journal._runs[grand.child_run_id].spec)  # pyright: ignore[reportPrivateUsage]
    grand_pin = child_workspace(grand_spec)
    assert grand_pin is not None and grand_pin.fork_of == pin.workspace_id
    assert grand_pin.workspace_id == f"ws:s1:fork:{grand.child_run_id}"
