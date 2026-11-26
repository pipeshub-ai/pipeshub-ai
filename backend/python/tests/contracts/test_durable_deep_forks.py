"""P6.4 DM-2, DM-3: a deep run whose sub-agents work on workspace forks.

The real deep orchestrator (``DeepDelegation``, its waves and programmatic
dispatch) over the in-memory journal, which forks a child's workspace at
admission as Node does, and the parent's merge at each join. What a child's
engine would save on its fork is written after the child's attempt.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.messages import ToolCall
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.journal.children import child_workspace
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.workspace import file
from harness_core.workspace import ChangeSet
from harness_core.workspace.forks import StoreChildWorkspaces, session_scope
from harness_core.workspace.memory import (
    InMemoryWorkspaceStore,
    InMemoryWorkspaceWriters,
)
from tests.contracts.deep_scenarios import (
    ORCHESTRATOR,
    PASSED,
    PLAN_CRITIC,
    RESULT_CRITIC,
    DeepScenario,
    DeepScript,
)
from tests.contracts.test_durable_deep_goldens import (
    SESSION,
    deep_spec,
    durable_answer,
    run_deep_durably,
)
from tests.unit.harness.assembly import world as w

if TYPE_CHECKING:
    from harness_core.workspace.types import WorkspaceManifest

WS = f"ws:{SESSION}"
SCOPE = session_scope(w.ORG, SESSION)
STEPS = {
    "notes": "Write the release notes for version 2.",
    "changelog": "Write the changelog for version 2.",
    "summary": "Summarize the release for the team.",
}


def _release(s: DeepScript) -> None:
    s.agent("intent").add_text("Prepare the version 2 release documents.")
    o = s.agent(ORCHESTRATOR)
    o.add_tool_call(ToolCall(id="call-plan", name="create_plan", arguments={
        "steps": [
            {"id": "notes", "description": STEPS["notes"], "domain": "writer", "tool_names": []},
            {"id": "changelog", "description": STEPS["changelog"], "domain": "writer", "tool_names": []},
            {
                "id": "summary",
                "description": STEPS["summary"],
                "domain": "writer",
                "tool_names": [],
                "depends_on": ["notes", "changelog"],
            },
        ],
        "confidence": "high",
    }))
    o.add_tool_call(ToolCall(id="call-critique", name="critique_plan", arguments={"plan": "notes, changelog, summary"}))
    s.agent(PLAN_CRITIC).add_structured(PASSED)
    for key, goal in STEPS.items():
        s.child(key, goal).add_text(f"{key} written.")
    o.add_tool_call(ToolCall(id="call-verify", name="verify_result", arguments={"output": "Release documents done."}))
    s.agent(RESULT_CRITIC).add_structured(PASSED)
    o.add_text("The version 2 release documents are ready.")


RELEASE = DeepScenario("deep_release_forks", "Prepare the version 2 release documents.", _release)


def _mounting_spec() -> RunSpec:
    spec = deep_spec().model_dump(mode="json", by_alias=True, exclude_none=True)
    extension = copy.deepcopy(spec["extensions"]["x.pipeshub"])
    extension["mounts"] = ["/workspace"]
    spec["extensions"]["x.pipeshub"] = extension
    return validate_contract(RunSpec, spec)


class _Release:
    def __init__(self, writes: dict[str, dict[str, str]]) -> None:
        clock = ManualClock(datetime(2026, 10, 3, tzinfo=UTC))
        self.store = InMemoryWorkspaceStore(clock=clock)
        self.writers = InMemoryWorkspaceWriters(clock=clock)
        self.merger = StoreChildWorkspaces(lambda _token: (self.store, self.writers))
        self.writes = writes
        self.bases: dict[str, WorkspaceManifest] = {}
        self.forks: dict[str, str] = {}

    async def child_ran(self, journal: Any, child: str) -> None:  # noqa: ANN401
        run = journal._runs[child]  # pyright: ignore[reportPrivateUsage]
        if run.status != "completed" or child in self.forks.values():
            return
        step = next(k for k, goal in STEPS.items() if goal in run.input_text)
        pinned = child_workspace(validate_contract(RunSpec, run.spec))
        assert pinned is not None and pinned.fork_of == WS
        self.forks[step] = pinned.workspace_id
        self.bases[step] = await self.store.head(SCOPE, pinned.workspace_id)
        changes = ChangeSet.of(*(file(path, text) for path, text in self.writes.get(step, {}).items()))
        if not changes.is_empty:
            base = self.bases[step]
            _ = await self.store.commit(
                SCOPE, pinned.workspace_id, base_revision=base.revision, changes=changes, reason="turn_end"
            )

    async def run(self) -> Any:  # noqa: ANN401
        return await run_deep_durably(
            RELEASE,
            spec=_mounting_spec(),
            workspaces=self.store,
            child_workspaces=self.merger,
            child_ran=self.child_ran,
        )


def _hash(text: str) -> str:
    return file("x", text).content.content_hash


def _tool_texts(run: Any) -> list[str]:  # noqa: ANN401
    calls = run.script.agents[ORCHESTRATOR].calls
    return [
        str(m.content)
        for m in calls[-1]["messages"]
        if getattr(m, "role", None) == "tool"
    ]


async def test_dm2_a_later_wave_sees_both_files_of_the_earlier_wave() -> None:
    release = _Release({
        "notes": {"docs/NOTES.md": "notes v2"},
        "changelog": {"CHANGELOG.md": "changes v2"},
        "summary": {"docs/SUMMARY.md": "summary v2"},
    })
    run = await release.run()
    assert await durable_answer(run) == ("The version 2 release documents are ready.", "answered")
    assert len(run.children()) == 3
    summary_base = release.bases["summary"]
    assert {p: e.content.content_hash for p, e in summary_base.files.items()} == {
        "docs/NOTES.md": _hash("notes v2"),
        "CHANGELOG.md": _hash("changes v2"),
    }
    head = await release.store.head(SCOPE, WS)
    assert {p: e.content.content_hash for p, e in head.files.items()} == {
        "docs/NOTES.md": _hash("notes v2"),
        "CHANGELOG.md": _hash("changes v2"),
        "docs/SUMMARY.md": _hash("summary v2"),
    }
    # One revision per merge, each child merged once.
    assert head.revision == 3
    spawned = [e for e in run.events() if e.type == "child.spawned"]
    assert len(spawned) == 3
    texts = _tool_texts(run)
    assert sum("[WORKSPACE] merged" in t for t in texts) == 3


async def test_dm3_a_conflict_reaches_the_orchestrator() -> None:
    release = _Release({
        "notes": {"RELEASE.md": "from notes"},
        "changelog": {"RELEASE.md": "from changelog"},
    })
    run = await release.run()
    assert await durable_answer(run) == ("The version 2 release documents are ready.", "answered")
    texts = _tool_texts(run)
    conflict = [t for t in texts if "[WORKSPACE] partial" in t]
    assert len(conflict) == 1
    assert "RELEASE.md (both_changed)" in conflict[0]
    loser = "changelog" if "changelog" in release.forks and release.forks["changelog"] in conflict[0] else "notes"
    assert release.forks[loser] in conflict[0]
    head = await release.store.head(SCOPE, WS)
    winner = "notes" if loser == "changelog" else "changelog"
    assert head.files["RELEASE.md"].content.content_hash == _hash(f"from {winner}")
