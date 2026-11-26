"""P6.4 KM-1..KM-8: a child granted the workspace works on a fork; its parent
merges the fork at the join, before admitting a later wave, once per child."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.failpoints import Raise
from harness_core.journal.children import ChildRequest, child_workspace
from harness_core.kernel.core.messages import ToolMessage
from harness_core.kernel.durable.delegation import (
    Decision,
    Delegated,
    DelegatedOutcome,
    DelegationView,
    Wait,
    delegate_effect,
    goal_tool,
    is_success,
)
from harness_core.testing import armed
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.workspace import file
from harness_core.testing.run_spec import minimal_run_spec
from harness_core.workspace import ChangeSet
from harness_core.workspace.forks import StoreChildWorkspaces, session_scope
from harness_core.workspace.memory import (
    InMemoryWorkspaceStore,
    InMemoryWorkspaceWriters,
)

from .support import ORG, RUN, SESSION, Crash, World, reply, scripted

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.effects.types import ToolEffect
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.core.tool_schema import ToolSchema
    from harness_core.kernel.durable import DurableKernel
    from harness_core.testing.durable import ScriptedModelPort
    from harness_core.workspace.forks import ChildMergeReport

WS = f"ws:{SESSION}"
SCOPE = session_scope(ORG, SESSION)


class _Waves:
    """`delegate{goal, task_id, after?}`: waits for the batch's calls named in `after`;
    the outcome shows the child's output and the merge report."""

    def __init__(self) -> None:
        self.reports: list[ChildMergeReport | None] = []

    def delegates(self, call: ToolCall) -> bool:
        return call.name == "delegate"

    def decide(self, call: Delegated, view: DelegationView) -> Decision:
        args = call.call.arguments
        after = [str(a) for a in args.get("after") or []]  # pyright: ignore[reportGeneralTypeIssues]
        by_task = {str(d.call.arguments.get("task_id")): d for d in (*view.earlier, *view.batch)}
        open_ = [by_task[a].invocation_id for a in after if by_task[a].state != "joined"]
        if open_:
            return Wait(tuple(open_))
        return ChildRequest(
            invocation_id=call.invocation_id,
            goal=str(args.get("goal")),
            task_id=str(args.get("task_id")),
        )

    async def outcome(
        self, call: ToolCall, document: JsonValue, *, workspace: ChildMergeReport | None = None
    ) -> DelegatedOutcome:
        self.reports.append(workspace)
        output = document.get("output") if isinstance(document, dict) else None
        shown: dict[str, JsonValue] = {"output": output}
        if workspace is not None:
            shown["workspace"] = workspace.as_json()
        return DelegatedOutcome(json.dumps(shown), error=not is_success(document))

    def effect(self, name: str) -> ToolEffect:
        return delegate_effect(name)

    def schemas(self) -> list[ToolSchema]:
        return [goal_tool("delegate", "Hand one task to a sub-agent")]


def _mounting_spec(max_children: int = 4) -> RunSpec:
    """A spec that mounts the session workspace (as engine and code runs will pin it)."""
    spec = dump_contract(minimal_run_spec(max_children=max_children))
    spec["extensions"] = {
        "x.pipeshub": {
            "knowledge": {"apps": [], "kb": [], "strictScope": False},
            "mcp": [],
            "skills": [],
            "mounts": ["/workspace"],
            "via": "session",
        }
    }
    return validate_contract(RunSpec, spec)


class _Forks:
    """The world, its workspace store and writer leases, and the parent's merge port."""

    def __init__(self) -> None:
        clock = ManualClock(datetime(2026, 10, 3, tzinfo=UTC))
        self.store = InMemoryWorkspaceStore(clock=clock)
        self.writers = InMemoryWorkspaceWriters(clock=clock)
        self.merges = 0
        self.world = World(workspaces=self.store)
        self.world.admit(spec=_mounting_spec())
        outer = self

        class _Counting(StoreChildWorkspaces):
            async def merge(self, token, child):  # type: ignore[no-untyped-def]  # noqa: ANN202
                outer.merges += 1
                return await super().merge(token, child)

        self.port = _Counting(lambda _token: (self.store, self.writers))
        self.waves = _Waves()

    def parent(self, port: ScriptedModelPort) -> DurableKernel:
        return self.world.kernel(
            port, children=self.world.journal, delegation=self.waves, child_workspaces=self.port
        )

    async def seed(self, *names: str) -> None:
        head = await self.store.head(SCOPE, WS)
        _ = await self.store.commit(
            SCOPE,
            WS,
            base_revision=head.revision,
            changes=ChangeSet.of(*(file(n, n) for n in names)),
            reason="seed",
        )

    def fork_of(self, child: str) -> str:
        pinned = child_workspace(validate_contract(RunSpec, self.world.journal._runs[child].spec))  # pyright: ignore[reportPrivateUsage]
        assert pinned is not None
        return pinned.workspace_id

    async def write(self, child: str, path: str, text: str) -> None:
        """What a child's engine saves on its fork (E0's autosave)."""
        fork = self.fork_of(child)
        head = await self.store.head(SCOPE, fork)
        _ = await self.store.commit(
            SCOPE, fork, base_revision=head.revision, changes=ChangeSet.of(file(path, text)), reason="autosave"
        )

    async def run_child(self, child: str, text: str = "done") -> None:
        _, port = scripted(reply(text))
        assert (await self.world.run(self.world.kernel(port), child)).status == "completed"

    async def files(self, workspace: str = WS) -> dict[str, str]:
        head = await self.store.head(SCOPE, workspace)
        return {p: e.content.content_hash for p, e in head.files.items()}


def _texts(port: ScriptedModelPort) -> list[dict[str, JsonValue]]:
    return [json.loads(m.text) for m in port.calls[-1].messages if isinstance(m, ToolMessage)]


def _hash(text: str) -> str:
    return file("x", text).content.content_hash


async def test_km1_a_childs_files_land_in_its_parent_at_the_join() -> None:
    f = _Forks()
    await f.seed("readme.md")
    _, port = scripted(reply("", ("delegate", {"goal": "edit", "task_id": "a"})), reply("final"))
    assert (await f.world.run(f.parent(port))).reason == "join"
    (child,) = f.world.journal.children_of(RUN)
    assert f.fork_of(child) == f"{WS}:fork:{child}"
    assert await f.files(f.fork_of(child)) == await f.files()
    await f.write(child, "src/app.py", "v1")
    await f.run_child(child)
    assert (await f.world.run(f.parent(port))).status == "completed"
    (shown,) = _texts(port)
    assert shown["workspace"] == {"status": "merged", "merged": ["src/app.py"]}
    assert await f.files() == {"readme.md": _hash("readme.md"), "src/app.py": _hash("v1")}
    assert (await f.store.head(SCOPE, WS)).revision == 2


async def test_r27rest_fu1_the_merge_journals_how_it_changed_each_path() -> None:
    """The parent's ``workspace.committed`` for the join says which files the child added, so the activity drawer
    does not call them modified; the model's merge report is unchanged."""
    f = _Forks()
    await f.seed("readme.md")
    _, port = scripted(reply("", ("delegate", {"goal": "edit", "task_id": "a"})), reply("final"))
    _ = await f.world.run(f.parent(port))
    (child,) = f.world.journal.children_of(RUN)
    await f.write(child, "readme.md", "edited")
    await f.write(child, "out/q3.csv", "new")
    await f.run_child(child)
    assert (await f.world.run(f.parent(port))).status == "completed"
    (shown,) = _texts(port)
    assert shown["workspace"] == {"status": "merged", "merged": ["out/q3.csv", "readme.md"]}
    committed = [e.data for e in f.world.journal.events(SESSION) if e.type == "workspace.committed" and e.run_id == RUN]
    assert committed == [
        {
            "workspaceId": WS,
            "revision": 2,
            "changed": ["/out/q3.csv", "/readme.md"],
            "kinds": {"/out/q3.csv": "added", "/readme.md": "modified"},
        }
    ]


async def test_km2_a_child_without_a_workspace_is_never_merged() -> None:
    f = _Forks()
    f.world = World(workspaces=f.store)
    f.world.admit(spec=minimal_run_spec(max_children=2))
    _, port = scripted(reply("", ("delegate", {"goal": "x", "task_id": "a"})), reply("final"))
    _ = await f.world.run(f.parent(port))
    (child,) = f.world.journal.children_of(RUN)
    assert child_workspace(validate_contract(RunSpec, f.world.journal._runs[child].spec)) is None  # pyright: ignore[reportPrivateUsage]
    await f.run_child(child)
    assert (await f.world.run(f.parent(port))).status == "completed"
    assert f.merges == 0 and f.waves.reports == [None]
    assert "workspace" not in _texts(port)[0]


async def test_km3_a_failed_child_is_not_merged_and_says_so() -> None:
    f = _Forks()
    _, port = scripted(reply("", ("delegate", {"goal": "x", "task_id": "a"})), reply("final"))
    _ = await f.world.run(f.parent(port))
    (child,) = f.world.journal.children_of(RUN)
    await f.write(child, "half.txt", "half")
    _, failing = scripted()
    assert (await f.world.run(f.world.kernel(failing), child)).status == "failed"
    assert (await f.world.run(f.parent(port))).status == "completed"
    (shown,) = _texts(port)
    # WP-56 (WP56-UNFIN-1): still not merged, but what it saved is kept in a folder of its own, and said where.
    folder = "Unfinished work/2026-10-03 00.00.00 UTC"
    assert shown["workspace"] == {
        "status": "not_merged",
        "reason": "child_failed",
        "fork": {"workspaceId": f.fork_of(child), "revision": 2},
        "unfinished": {"folder": folder, "files": ["half.txt"], "count": 1},
    }
    half = (await f.files(f.fork_of(child)))["half.txt"]
    assert await f.files() == {f"{folder}/half.txt": half}
    committed = [e.data for e in f.world.journal.events(SESSION) if e.type == "workspace.committed" and e.run_id == RUN]
    assert committed == [
        {
            "workspaceId": WS,
            "revision": 1,
            "changed": [f"/{folder}/half.txt"],
            "kinds": {f"/{folder}/half.txt": "added"},
        }
    ]


async def test_km4_a_crash_after_the_merge_reports_the_same_and_merges_once() -> None:
    f = _Forks()
    _, port = scripted(reply("", ("delegate", {"goal": "x", "task_id": "a"})), reply("final"))
    _ = await f.world.run(f.parent(port))
    (child,) = f.world.journal.children_of(RUN)
    await f.write(child, "a.txt", "a")
    await f.run_child(child)
    with armed({"worker.join.after_merge": Raise(Crash)}):
        await f.world.crash(f.parent(port))
    assert (await f.store.head(SCOPE, WS)).revision == 1
    assert (await f.world.run(f.parent(port))).status == "completed"
    assert (await f.store.head(SCOPE, WS)).revision == 1
    assert f.merges == 2
    (shown,) = _texts(port)
    assert shown["workspace"] == {"status": "merged", "merged": ["a.txt"]}
    assert len(f.world.events("tool.outcome")) == 1


async def test_km5_a_crash_between_the_childs_end_and_the_merge_merges_once() -> None:
    f = _Forks()
    _, port = scripted(reply("", ("delegate", {"goal": "x", "task_id": "a"})), reply("final"))
    _ = await f.world.run(f.parent(port))
    (child,) = f.world.journal.children_of(RUN)
    await f.write(child, "a.txt", "a")
    await f.run_child(child)
    with armed({"worker.join.before_merge": Raise(Crash)}):
        await f.world.crash(f.parent(port))
    assert await f.files() == {}
    assert (await f.world.run(f.parent(port))).status == "completed"
    assert await f.files() == {"a.txt": _hash("a")}
    assert (await f.store.head(SCOPE, WS)).revision == 1


async def test_km6_siblings_changing_one_path_keep_the_first_and_report_the_second() -> None:
    f = _Forks()
    await f.seed("shared.txt")
    _, port = scripted(
        reply("", ("delegate", {"goal": "x", "task_id": "a"}), ("delegate", {"goal": "y", "task_id": "b"})),
        reply("final"),
    )
    _ = await f.world.run(f.parent(port))
    first, second = f.world.journal.children_of(RUN)
    await f.write(first, "shared.txt", "from a")
    await f.write(second, "shared.txt", "from b")
    await f.write(second, "b-only.txt", "b")
    for child in (first, second):
        await f.run_child(child)
    assert (await f.world.run(f.parent(port))).status == "completed"
    one, two = _texts(port)
    assert one["workspace"] == {"status": "merged", "merged": ["shared.txt"]}
    assert two["workspace"] == {
        "status": "partial",
        "merged": ["b-only.txt"],
        "conflicts": [{"path": "shared.txt", "kind": "both_changed"}],
        "kept": "parent",
        "fork": {"workspaceId": f.fork_of(second), "revision": 3},
    }
    assert (await f.files())["shared.txt"] == _hash("from a")
    theirs = await f.store.revision(SCOPE, f.fork_of(second), 3)
    assert theirs.files["shared.txt"].content.content_hash == _hash("from b")


async def test_km7_a_later_wave_forks_the_workspace_with_the_earlier_waves_files() -> None:
    f = _Forks()
    _, port = scripted(
        reply(
            "",
            ("delegate", {"goal": "x", "task_id": "a"}),
            ("delegate", {"goal": "y", "task_id": "b"}),
            ("delegate", {"goal": "z", "task_id": "c", "after": ["a", "b"]}),
        ),
        reply("final"),
    )
    assert (await f.world.run(f.parent(port))).reason == "join"
    a, b = f.world.journal.children_of(RUN)
    await f.write(a, "a.txt", "a")
    await f.write(b, "b.txt", "b")
    await f.run_child(a)
    await f.run_child(b)
    assert (await f.world.run(f.parent(port))).reason == "join"
    *_, c = f.world.journal.children_of(RUN)
    assert await f.files(f.fork_of(c)) == {"a.txt": _hash("a"), "b.txt": _hash("b")}
    await f.write(c, "c.txt", "c")
    await f.run_child(c)
    assert (await f.world.run(f.parent(port))).status == "completed"
    assert await f.files() == {"a.txt": _hash("a"), "b.txt": _hash("b"), "c.txt": _hash("c")}
    assert [r.status if r else None for r in f.waves.reports] == ["merged", "merged", "merged"]


async def test_km8_another_writer_of_the_parent_workspace_blocks_the_merge_typed() -> None:
    f = _Forks()
    _, port = scripted(reply("", ("delegate", {"goal": "x", "task_id": "a"})), reply("final"))
    _ = await f.world.run(f.parent(port))
    (child,) = f.world.journal.children_of(RUN)
    await f.write(child, "a.txt", "a")
    await f.run_child(child)
    _ = await f.writers.acquire(SCOPE, WS, holder="code-task-run", ttl_ms=600_000)
    assert (await f.world.run(f.parent(port))).status == "completed"
    (shown,) = _texts(port)
    assert shown["workspace"]["status"] == "not_merged"  # type: ignore[index]
    assert shown["workspace"]["reason"] == "writer_busy"  # type: ignore[index]
    assert await f.files() == {}
