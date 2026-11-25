"""R27REST-FU-1: ``workspace.committed`` says how each path changed (``kinds``), from every writer.

The activity drawer read every path of a commit as modified, so a file a job's merge added to the conversation
showed as "Modified". The kind is known where the commit is made: an engine save's diff against its parent, and
Node's merge plan against the target it merged into. The real-Node half of the merge is the workspace contract's
``test_wf8`` (memory and Node), and the driver's join is ``test_child_forks.py::test_r27rest_fu1_*``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from pydantic import ValidationError

from app.harness.workspace import node_store
from harness_core.contracts.generated.events.workspace_committed import (
    WorkspaceCommittedData,
)
from harness_core.engines.durable.sink import JournalEngineEventSink
from harness_core.interactions.ports import RunBinding
from harness_core.kernel.durable.driver import _merge_committed  # pyright: ignore[reportPrivateUsage]
from harness_core.testing.contracts.workspace import file
from harness_core.testing.engines.durable import RecordingPresenter
from harness_core.workspace import ChangeSet, diff_files
from harness_core.workspace.events import workspace_committed
from harness_core.workspace.forks import ChildMergeReport
from harness_core.workspace.types import build_manifest

if TYPE_CHECKING:
    from harness_core.journal.types import NewEvent

BEFORE = {"kept.txt": file("kept.txt", "k"), "gone.txt": file("gone.txt", "g")}


def test_fu1_1_a_change_set_names_its_kinds_against_what_it_changes() -> None:
    changes = ChangeSet.of(file("kept.txt", "v2"), file("new.csv", "n"), deletes=frozenset({"gone.txt"}))
    assert changes.kinds(BEFORE) == {"kept.txt": "modified", "new.csv": "added", "gone.txt": "deleted"}


def test_fu1_2_a_diff_names_the_same_kinds() -> None:
    after = {"kept.txt": file("kept.txt", "v2"), "new.csv": file("new.csv", "n")}
    assert diff_files(BEFORE, after).kinds() == {"kept.txt": "modified", "new.csv": "added", "gone.txt": "deleted"}


def test_fu1_3_the_event_is_the_contracts_and_names_paths_from_the_root() -> None:
    event = workspace_committed("ws:s", 3, ["a.md", "b.md"], {"a.md": "added", "b.md": "deleted"})
    assert event.type == "workspace.committed"
    assert event.data == {
        "workspaceId": "ws:s",
        "revision": 3,
        "changed": ["/a.md", "/b.md"],
        "kinds": {"/a.md": "added", "/b.md": "deleted"},
    }
    WorkspaceCommittedData.model_validate(event.data)


def test_fu1_4_a_path_of_unknown_kind_is_journaled_without_one() -> None:
    """An older Node's merge names no kinds: the event is today's, and a reader shows those paths as modified."""
    assert "kinds" not in workspace_committed("ws:s", 2, ["a.md"], {}).data
    partial = workspace_committed("ws:s", 2, ["a.md", "b.md"], {"b.md": "added"}).data
    assert partial["kinds"] == {"/b.md": "added"}


def test_fu1_5_the_contract_refuses_an_unknown_kind_and_a_relative_key() -> None:
    base = {"workspaceId": "ws:s", "revision": 1, "changed": ["/a.md"]}
    WorkspaceCommittedData.model_validate(base)
    with pytest.raises(ValidationError):
        WorkspaceCommittedData.model_validate({**base, "kinds": {"/a.md": "renamed"}})
    with pytest.raises(ValidationError):
        WorkspaceCommittedData.model_validate({**base, "kinds": {"a.md": "added"}})


async def test_fu1_6_an_engine_save_journals_its_kinds_against_the_saved_parent() -> None:
    batches: list[list[NewEvent]] = []

    async def commit(cid: str, events: list[NewEvent]) -> None:
        batches.append(events)

    sink = JournalEngineEventSink("r", "r", RecordingPresenter(), commit)
    sink.begin("trn_x", 1)
    run = RunBinding("o", "s", "r")
    first = build_manifest("ws:s", revision=1, parent_revision=None, files=BEFORE, created_at_ms=0)
    second = build_manifest(
        "ws:s",
        revision=2,
        parent_revision=1,
        files={"kept.txt": file("kept.txt", "v2"), "new.csv": file("new.csv", "n")},
        created_at_ms=1,
    )
    await sink.workspace_saved(run, first, "autosave")
    await sink.workspace_saved(run, second, "turn_end")
    committed = [e.data for batch in batches for e in batch if e.type == "workspace.committed"]
    assert [c.get("kinds") for c in committed] == [
        {"/gone.txt": "added", "/kept.txt": "added"},
        {"/gone.txt": "deleted", "/kept.txt": "modified", "/new.csv": "added"},
    ]


def _report(**fields: object) -> ChildMergeReport:
    defaults: dict[str, object] = {"child_run_id": "c1", "fork_id": "ws:s:fork:c1", "into": "ws:s", "status": "merged"}
    return ChildMergeReport(revision=4, **{**defaults, **fields})  # pyright: ignore[reportArgumentType]


def test_fu1_7_a_joined_childs_merge_journals_nodes_kinds() -> None:
    (event,) = _merge_committed(_report(merged=("a.md", "out/q3.csv"), kinds={"out/q3.csv": "added", "a.md": "modified"}))
    assert event.data["kinds"] == {"/a.md": "modified", "/out/q3.csv": "added"}


def test_fu1_8_a_failed_childs_kept_work_is_added() -> None:
    report = _report(status="not_merged", unfinished="Unfinished work/x", kept=("half.txt",))
    (event,) = _merge_committed(report)
    assert event.data["kinds"] == {"/Unfinished work/x/half.txt": "added"}


def test_fu1_9_a_worker_reads_nodes_merge_kinds_and_tolerates_an_older_node() -> None:
    kinds = node_store._kinds  # pyright: ignore[reportPrivateUsage]
    assert kinds({"a.md": "added", "b.md": "deleted"}) == {"a.md": "added", "b.md": "deleted"}
    assert kinds(None) == {}
    assert kinds(cast("object", {"a.md": "renamed", "b.md": "modified"})) == {"b.md": "modified"}
