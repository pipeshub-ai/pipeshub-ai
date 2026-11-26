"""MA1 QA37-K2: a parent's journal whose ``child.spawned`` names the child's model is a valid history and folds.

``journals/ma1_parent_with_role_child.journal.json`` is the journal the run machine writes when a parent delegates
to a sub-agent whose role sets a model: the admission authority (here the reference journal, which applies Node's
rule) writes ``child.spawned`` with ``model``. The file is kept equal to what this build records (``UPDATE_GOLDENS=1``
re-records it); ``journal-folds.json`` holds its fold for Node's ``journal-folds.test.ts``, and Node's projector
reads the same file for the model a sub-agent row shows. The field is context for readers: the fold of the run is
the same with and without it, so ``FOLD_VERSION`` does not move.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from harness_core.contracts.generated.events.child_spawned import ChildSpawnedData
from harness_core.journal import JournalEvent
from harness_core.journal.children import ChildRequest
from harness_core.kernel.durable.delegation import (
    DelegatingTool,
    NamedDelegation,
    goal_tool,
)
from harness_core.kernel.fold import FOLD_VERSION, fold_run
from harness_core.testing.history import journal_history_violations
from harness_core.testing.run_spec import minimal_run_spec
from tests.contracts.test_journal_folds import TARGET as FOLDS
from tests.contracts.test_journal_folds import fold_summary
from tests.unit.harness_core.durable.support import (
    ORG,
    RUN,
    SESSION,
    World,
    reply,
    scripted,
)

if TYPE_CHECKING:
    from harness_core.kernel.durable.delegation import (
        Decision,
        Delegated,
        DelegationView,
    )

NAME = "ma1_parent_with_role_child"
JOURNAL = Path(__file__).resolve().parents[4] / "contracts/harness/vectors/journals" / f"{NAME}.journal.json"
QUERY = "Find what changed in the Q3 plan"
ROLE_MODEL = {"provider": "anthropic", "model": "claude-haiku-4-5", "role": "researcher"}


class _ResearcherDelegation(NamedDelegation):
    """Every delegated child runs as the ``researcher`` sub-agent."""

    def decide(self, call: Delegated, view: DelegationView) -> Decision:
        request = super().decide(call, view)
        assert isinstance(request, ChildRequest)
        return dataclasses.replace(request, definition={"role": "Researcher", "toolNames": []})


async def record() -> dict[str, Any]:
    world = World()
    world.journal.configured_models["anthropic:claude-haiku-4-5"] = "mk-b"
    world.journal.admit(
        RUN,
        org_id=ORG,
        session_id=SESSION,
        spec=minimal_run_spec(max_children=1, fold_version=FOLD_VERSION),
        input_text=QUERY,
        role_routes={"researcher": "anthropic:claude-haiku-4-5"},
    )
    _, parent_port = scripted(
        reply("", ("delegate", {"goal": "Read the Q3 plan and list what changed", "task_id": "research"})),
        reply("The Q3 plan moved the launch to November."),
    )
    delegation = _ResearcherDelegation({"delegate": DelegatingTool(goal_tool("delegate", "Hand a task over"))})
    parent = world.kernel(parent_port, children=world.journal, delegation=delegation)
    assert (await world.run(parent)).reason == "join"
    (child,) = world.journal.children_of(RUN)
    _, child_port = scripted(reply("The launch moved to November."))
    assert (await world.run(world.kernel(child_port), child)).status == "completed"
    assert (await world.run(parent)).status == "completed"
    return {
        "scenario": NAME,
        "query": QUERY,
        "runId": RUN,
        "sessionId": SESSION,
        "events": [e.envelope() for e in world.events(run_id=RUN)],
    }


def _dump(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def _kept() -> list[JournalEvent]:
    return [JournalEvent.from_envelope(raw) for raw in json.loads(JOURNAL.read_text(encoding="utf-8"))["events"]]


async def test_qa37_k2_the_golden_journal_is_what_a_parent_with_a_role_child_journals() -> None:
    actual = _dump(await record())
    if os.environ.get("UPDATE_GOLDENS") == "1":
        _ = JOURNAL.write_text(actual, encoding="utf-8")
        return
    assert JOURNAL.exists(), f"missing {JOURNAL.name}; record it with UPDATE_GOLDENS=1"
    assert actual == JOURNAL.read_text(encoding="utf-8"), (
        f"{JOURNAL.name} drifted; re-record with UPDATE_GOLDENS=1 (then journal-folds.json) only if intended"
    )


def test_qa37_k2_child_spawned_names_the_roles_model_and_the_journal_is_a_valid_history() -> None:
    events = _kept()
    [spawned] = [e for e in events if e.type == "child.spawned"]
    assert ChildSpawnedData.model_validate(spawned.data).model_dump(mode="json", exclude_none=True)["model"] == ROLE_MODEL
    assert [str(v) for v in journal_history_violations(events)] == []


def test_qa37_k2_the_run_folds_with_its_child_and_the_fold_is_recorded_for_node() -> None:
    summary = fold_summary(fold_run(_kept(), run_id=RUN))
    assert summary["end"] == {"status": "completed", "detail": "answered"}
    assert [status for _, status in summary["children"]] == ["completed"]  # type: ignore[union-attr, misc]
    recorded = {j["name"]: j for j in json.loads(FOLDS.read_text(encoding="utf-8"))["journals"]}
    assert recorded[NAME]["summary"] == summary


def test_qa37_k2_the_fold_is_the_same_without_the_model() -> None:
    raw: list[dict[str, Any]] = json.loads(JOURNAL.read_text(encoding="utf-8"))["events"]
    stripped = [
        {**e, "data": {k: v for k, v in e["data"].items() if k != "model"}} if e["type"] == "child.spawned" else e
        for e in raw
    ]
    assert stripped != raw
    assert fold_run([JournalEvent.from_envelope(e) for e in stripped], run_id=RUN) == fold_run(_kept(), run_id=RUN)
    assert FOLD_VERSION == 8
