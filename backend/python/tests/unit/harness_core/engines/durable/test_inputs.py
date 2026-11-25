"""E1b IN-*: conversation artifacts as a durable engine run's input files (EV2-26)."""

from __future__ import annotations

import dataclasses
import itertools
from typing import TYPE_CHECKING, override

import pytest

from harness_core.artifacts.memory import InMemoryArtifactStore
from harness_core.engines.durable.inputs import StoreInputSource
from harness_core.interactions.ports import RunBinding
from harness_core.journal.types import TERMINAL_STATUSES, InputArtifactRef
from harness_core.testing.contracts.engine_inputs import (
    MAX_INPUT_BYTES,
    TENANT,
    EngineInputSourceContract,
)
from harness_core.testing.engines.durable import (
    ORG,
    SESSION,
    EngineWorld,
    ScriptedEngineAdapter,
)
from harness_core.testing.sandbox.fake import FakeSandboxHandle
from harness_core.workspace.forks import session_scope
from harness_core.workspace.memory import InMemoryBinaryObjectStore
from harness_core.workspace.types import ObjectScope

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.durable.ports import EngineInputSource
    from harness_core.engines.events import EngineState
    from harness_core.engines.ports import EngineSession
    from harness_core.engines.types import EngineLaunch
    from harness_core.sandbox.ports import SandboxHandle
    from harness_core.workspace.types import ObjectRef

RUN = "run-inputs"
WORKSPACE = f"ws:{SESSION}"


class _MemoryInputs:
    def __init__(self) -> None:
        self.store = InMemoryArtifactStore()
        self.objects = InMemoryBinaryObjectStore()
        self.unreadable: set[str] = set()
        self._ids = itertools.count(1)
        self._source = StoreInputSource(
            self.store, self.objects, object_scope=lambda b: session_scope(b.tenant, b.scope),
            readable=lambda artifact_id: artifact_id not in self.unreadable, max_input_bytes=MAX_INPUT_BYTES,
        )

    @property
    def source(self) -> EngineInputSource:
        return self._source

    async def add(self, name: str, content: bytes, *, session: str = "conv-in", readable: bool = True) -> str:
        stored, _ = await self.store.register_output(
            RunBinding(TENANT, session, "upload"), artifact_id=f"art-{next(self._ids)}", name=name,
            mime_type="text/plain", content=content, lifecycle="final",
        )
        if not readable:
            self.unreadable.add(stored.artifact_id)
        return stored.artifact_id

    async def add_version(self, artifact_id: str, content: bytes) -> int:
        binding = RunBinding(TENANT, "conv-in", "upload")
        current = await self.store.current(binding, artifact_id)
        stored, _ = await self.store.add_version(
            binding, artifact_id, content=content, mime_type="text/plain", expected_version=current.version,
            lifecycle="final",
        )
        return stored.version

    async def discard(self, artifact_id: str) -> None:
        await self.store.discard(RunBinding(TENANT, "conv-in", "upload"), artifact_id)

    async def stored(self, ref: ObjectRef) -> bytes:
        return await self.objects.get(session_scope(TENANT, "conv-in"), ref)


class TestStoreInputSource(EngineInputSourceContract):
    @pytest.fixture
    @override
    def input_world(self) -> _MemoryInputs:
        return _MemoryInputs()


class _Opening(ScriptedEngineAdapter):
    """Records the workspace files and their bytes each session finds when it opens (before the engine runs)."""

    def __init__(self) -> None:
        super().__init__()
        self.opened: list[dict[str, bytes]] = []

    @override
    async def open(  # pyright: ignore[reportIncompatibleMethodOverride] - the scripted adapter's open is untyped
        self,
        sandbox: SandboxHandle,
        launch: EngineLaunch,
        restore: EngineState | None,
        *,
        correlation: Mapping[str, str] | None = None,
    ) -> EngineSession:
        assert isinstance(sandbox, FakeSandboxHandle)
        root = launch.workdir.rstrip("/") + "/"
        self.opened.append({p.removeprefix(root): bytes(f) for p, f in sandbox.files.items() if p.startswith(root)})
        return await super().open(sandbox, launch, restore, correlation=correlation)  # pyright: ignore[reportReturnType]


def world(journal_accepts: frozenset[str] | None = None) -> tuple[EngineWorld, _Opening]:
    adapter = _Opening()
    w = EngineWorld(adapter=adapter, journal_accepts=journal_accepts)
    w.config = dataclasses.replace(w.config, autosave_changes=2)
    return w, adapter


async def upload(w: EngineWorld, name: str, content: bytes) -> str:
    stored, _ = await w.artifacts.register_output(
        RunBinding(ORG, SESSION, "upload"), artifact_id=f"art-{name}", name=name, mime_type="text/markdown",
        content=content, lifecycle="final",
    )
    return stored.artifact_id


async def new_version(w: EngineWorld, artifact_id: str, content: bytes) -> None:
    binding = RunBinding(ORG, SESSION, "upload")
    current = await w.artifacts.current(binding, artifact_id)
    _ = await w.artifacts.add_version(
        binding, artifact_id, content=content, mime_type="text/markdown", expected_version=current.version,
        lifecycle="final",
    )


async def versions(w: EngineWorld, artifact_id: str) -> list[bytes]:
    binding = RunBinding(ORG, SESSION, "check")
    current = await w.artifacts.current(binding, artifact_id)
    return [await w.artifacts.content(binding, artifact_id, v) for v in range(1, current.version + 1)]


async def finish(w: EngineWorld, run_id: str) -> str:
    for _ in range(6):
        if w.journal.status(run_id) in TERMINAL_STATUSES:
            break
        if w.journal.status(run_id) == "running":
            w.expire()
        _ = await w.attempt(run_id)
    return str(w.journal.status(run_id))


async def test_in2_the_first_turn_finds_its_inputs_under_in_pinned_and_saved() -> None:
    w, adapter = world()
    notes = await upload(w, "notes.md", b"draft one")
    w.admit(RUN, "say read", inputs=[InputArtifactRef(notes)])
    assert await finish(w, RUN) == "completed"
    assert adapter.opened == [{"in/notes.md": b"draft one"}]
    (started,) = [e.data for e in w.events(RUN) if e.type == "turn.started"]
    (pin,) = started["inputs"]  # type: ignore[misc]
    assert {k: pin[k] for k in ("name", "artifactId", "version", "mimeType")} == {  # type: ignore[index]
        "name": "notes.md", "artifactId": notes, "version": 1, "mimeType": "text/markdown",
    }
    commits = [e.data["changed"] for e in w.events(RUN) if e.type == "workspace.committed"]
    assert commits == [["/in/notes.md"]]


@pytest.mark.parametrize("case", ["unreadable", "unknown"])
async def test_in3_a_refused_input_fails_the_run_and_acquires_nothing(case: str) -> None:
    # A Node that keeps no skipped inputs (R36COVER-FU-3 ``skipped_inputs``): with one, the job goes on without it.
    w, _ = world(journal_accepts=frozenset())
    notes = await upload(w, "notes.md", b"x")
    if case == "unreadable":
        assert w.unreadable is not None
        w.unreadable.add(notes)
    ref = InputArtifactRef(notes if case == "unreadable" else "art-missing")
    w.admit(RUN, "say read", inputs=[ref])
    disposition = await w.attempt(RUN)
    assert (disposition.status, disposition.reason) == ("failed", "engine_input_unavailable")
    assert w.broker.acquisitions == 0
    assert "turn.started" not in w.types(RUN)


async def test_in4_inputs_with_no_source_bound_fail_the_run() -> None:
    w, _ = world()
    w.unreadable = None
    notes = await upload(w, "notes.md", b"x")
    w.admit(RUN, "say read", inputs=[InputArtifactRef(notes)])
    disposition = await w.attempt(RUN)
    assert (disposition.status, disposition.reason) == ("failed", "engine_inputs_unsupported")


async def test_in5_a_redrive_before_the_seed_seeds_the_pinned_version() -> None:
    w, adapter = world()
    notes = await upload(w, "notes.md", b"v1")
    w.admit(RUN, "say read", inputs=[InputArtifactRef(notes)])
    assert await w.crash_at(RUN, "engine.turn.after_acquire")
    await new_version(w, notes, b"v2 written meanwhile")
    assert await finish(w, RUN) == "completed"
    assert adapter.opened == [{"in/notes.md": b"v1"}]


async def test_in5_a_redrive_after_the_seed_seeds_nothing_again() -> None:
    w, adapter = world()
    notes = await upload(w, "notes.md", b"v1")
    w.admit(RUN, "write in/notes.md changed by the engine\nwrite x.md x\nsay done", inputs=[InputArtifactRef(notes)])
    assert await w.crash_at(RUN, "engine.turn.after_autosave")
    assert await finish(w, RUN) == "completed"
    head = await w.store.head(ObjectScope(ORG), WORKSPACE)
    assert await w.objects.get(session_scope(ORG, SESSION), head.files["in/notes.md"].content) == b"changed by the engine"
    assert adapter.opened[-1]["in/notes.md"] == b"changed by the engine"
    seeds = [e for e in w.events(RUN) if e.type == "workspace.committed" and e.data["changed"] == ["/in/notes.md"]]
    assert len(seeds) == 1


async def test_in6_an_edited_input_is_the_next_version_of_its_artifact() -> None:
    w, _ = world()
    notes = await upload(w, "notes.md", b"draft one")
    w.admit(RUN, "write out/notes.md draft two\nsay edited", inputs=[InputArtifactRef(notes)])
    assert await finish(w, RUN) == "completed"
    assert await versions(w, notes) == [b"draft one", b"draft two"]
    assert [a.name for a in w.artifacts.listing(RunBinding(ORG, SESSION, "check"))] == ["notes.md"]
