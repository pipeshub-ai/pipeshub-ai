"""CS-1..CS-7 (P5.9b): ``EngineController`` takes and restores sandbox snapshots (fake broker, ``FakeEngine``)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import pytest

from harness_core.engines.background import LIST_SCRIPT, STOP_SCRIPT
from harness_core.engines.controller import MODEL_ROUTE, TOOLS_ROUTE, placeholder_for
from harness_core.engines.memory import InMemoryEngineEventSink
from harness_core.engines.snapshots import (
    SNAPSHOT_FOREIGN,
    SNAPSHOT_MISSING,
    SNAPSHOT_SECRET,
    ObjectStoreSandboxSnapshots,
    SnapshotPolicy,
    read_archive,
)
from harness_core.testing.contracts.sandbox_snapshots import tar_of
from harness_core.workspace.memory import InMemoryBinaryObjectStore, InMemoryKeyProvider
from harness_core.workspace.sealing import SealedObjectStore
from harness_core.workspace.types import ObjectRef, ObjectScope
from tests.unit.harness_core.engines.test_controller import (
    KEY,
    RUN,
    SCOPE,
    WatchingAdapter,
    World,
    task,
)

POLICY = SnapshotPolicy(("/tmp/.cache", "/tmp/matplotlib"), max_bytes=1024 * 1024)
CACHE = "/tmp/.cache/tool/index.json"
GITHUB = "ghp_" + "a1b2c3d4e5" * 4
ATTEMPT_TOKEN = "attempt-token-value-7f3e9a"


@dataclass
class OrderedSink(InMemoryEngineEventSink):
    """Records which sink method ran when, so the snapshot's place in the turn shows."""

    order: list[str] = field(default_factory=list[str])

    async def workspace_saved(self, run, manifest, reason):  # type: ignore[no-untyped-def] # noqa: ANN201
        self.order.append(f"save:{reason}")
        await super().workspace_saved(run, manifest, reason)

    async def checkpointed(self, run, blob):  # type: ignore[no-untyped-def] # noqa: ANN201
        self.order.append("checkpoint")
        await super().checkpointed(run, blob)

    async def snapshotted(self, run, snapshot, reason):  # type: ignore[no-untyped-def] # noqa: ANN201
        self.order.append(f"snapshot:{reason}")
        await super().snapshotted(run, snapshot, reason)


class PlantingAdapter(WatchingAdapter):
    """``FakeEngine`` that, as a tool it ran would, leaves ``planted`` in the sandbox when it opens: a secret
    the engine's prompt never carries (the egress proxy would refuse that request first)."""

    def __init__(self, planted: dict[str, bytes]) -> None:
        super().__init__()
        self.planted = planted

    async def open(self, sandbox, launch, restore, *, correlation=None):  # type: ignore[no-untyped-def] # noqa: ANN201
        for path, data in self.planted.items():
            sandbox.files[path] = data
        return await super().open(sandbox, launch, restore, correlation=correlation)


def snapshot_world(
    *, policy: SnapshotPolicy | None = POLICY, sealed: bool = True, planted: dict[str, bytes] | None = None
) -> World:
    objects = InMemoryBinaryObjectStore()
    keys = InMemoryKeyProvider()
    inner = SealedObjectStore(objects, keys) if sealed else objects
    world = World(
        objects=objects,
        adapter=PlantingAdapter(planted or {}),
        sink=OrderedSink(),
        config_overrides={"snapshot": policy} if policy is not None else {},
        snapshot_store=ObjectStoreSandboxSnapshots(inner),
    )
    world.keys = keys  # type: ignore[attr-defined]
    return world


def _events(caplog: pytest.LogCaptureFixture, name: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "harness_event", "") == name]


async def _files(world: World, ref: ObjectRef) -> dict[str, bytes]:
    assert world.snapshot_store is not None
    archive = read_archive(await world.snapshot_store.load(SCOPE, ref), POLICY)
    return {e.name: e.data for e in archive.entries if e.data is not None}


async def test_cs1_without_a_policy_nothing_is_snapshotted() -> None:
    world = snapshot_world(policy=None)
    outcome = await world.controller.run_turn(world.run(), task(0, f"write {CACHE} warm\nsay ok"))
    assert outcome.reason == "completed"
    assert world.sink.snapshots == []
    # The process listing (LCFIX) runs whatever the policy; no snapshot capture or restore does.
    probes = (LIST_SCRIPT, STOP_SCRIPT)
    assert not any(
        call[0] == "exec" and call[2][:1] in (("sh",), ("tar",)) and call[2][2:3] not in ((p,) for p in probes)
        for call in world.broker.calls
    )


async def test_cs2_turn_end_snapshots_after_the_final_save_and_before_the_checkpoint() -> None:
    world = snapshot_world()
    outcome = await world.controller.run_turn(world.run(), task(0, f"write {CACHE} warm\nwrite notes.md hi\nsay ok"))
    assert outcome.reason == "completed"
    [record] = world.sink.snapshots
    assert record.reason == "turn_end"
    assert await _files(world, record.snapshot) == {CACHE.lstrip("/"): b"warm"}
    assert isinstance(world.sink, OrderedSink)
    assert world.sink.order[-3:] == ["save:turn_end", "snapshot:turn_end", "checkpoint"]


async def test_cs3_an_autosave_point_snapshots_and_an_unchanged_one_is_not_written_again() -> None:
    world = snapshot_world()
    writes = "\n".join(f"write /tmp/.cache/f{i:02d} {i}" for i in range(20))
    _ = await world.controller.run_turn(world.run(), task(0, f"{writes}\nsay ok"))
    assert [s.reason for s in world.sink.snapshots] == ["autosave"]
    assert len(await _files(world, world.sink.snapshots[0].snapshot)) == 20


async def test_cs4_a_snapshot_is_restored_before_the_engine_opens_and_not_recorded_again() -> None:
    world = snapshot_world()
    _ = await world.controller.run_turn(world.run(), task(0, f"write {CACHE} warm\nsay ok"))
    [taken] = world.sink.snapshots
    assert world.broker.sandboxes == {}
    second = await world.controller.run_turn(world.run(attempt=2, snapshot=taken.snapshot), task(1, "say again"))
    assert second.reason == "completed"
    assert isinstance(world.adapter, WatchingAdapter)
    assert world.adapter.files_at_open[-1][CACHE] == b"warm"
    assert world.sink.snapshots == [taken]


@pytest.mark.parametrize("damage", ["missing", "foreign"])
async def test_cs5_an_unusable_snapshot_never_stops_the_turn(damage: str, caplog: pytest.LogCaptureFixture) -> None:
    # Unsealed, so another session's object is readable by ref and only the envelope stands in the way.
    world = snapshot_world(sealed=False)
    assert world.snapshot_store is not None
    if damage == "missing":
        ref = ObjectRef("sha256:" + "1" * 64, "sha256:" + "1" * 64, 10)
    else:
        other = ObjectScope(SCOPE.tenant_id, "ws:another-session")
        ref = await world.snapshot_store.save(other, tar_of({CACHE[1:]: b"x"}))
    with caplog.at_level(logging.WARNING):
        outcome = await world.controller.run_turn(world.run(snapshot=ref), task(0, "say ok"))
    assert outcome.reason == "completed"
    [unusable] = _events(caplog, "engine.snapshot.unusable")
    expected = {"missing": SNAPSHOT_MISSING, "foreign": SNAPSHOT_FOREIGN}[damage]
    assert unusable.harness["reasonCode"] == expected  # type: ignore[attr-defined]
    assert isinstance(world.adapter, WatchingAdapter)
    assert CACHE not in world.adapter.files_at_open[-1]


@pytest.mark.parametrize("secret", ["pattern", "model_placeholder", "tools_placeholder", "run_token"])
async def test_cs6_a_snapshot_holding_a_secret_is_refused_and_logged_without_it(
    secret: str, caplog: pytest.LogCaptureFixture
) -> None:
    attempt = f"{RUN}.a1"
    value = {
        "pattern": GITHUB,
        "model_placeholder": placeholder_for(KEY, attempt, MODEL_ROUTE),
        "tools_placeholder": placeholder_for(KEY, attempt, TOOLS_ROUTE),
        "run_token": ATTEMPT_TOKEN,
    }[secret]
    world = snapshot_world(planted={"/tmp/.cache/cfg/hosts.yml": f"token={value}\n".encode()})
    with caplog.at_level(logging.DEBUG):
        outcome = await world.controller.run_turn(world.run(secrets=lambda: (ATTEMPT_TOKEN,)), task(0, f"write {CACHE} warm\nsay ok"))
    assert outcome.reason == "completed"
    assert world.sink.snapshots == []
    [refused] = _events(caplog, "engine.snapshot.refused")
    assert refused.harness["reasonCode"] == SNAPSHOT_SECRET  # type: ignore[attr-defined]
    for record in caplog.records:
        text = record.getMessage() + repr(getattr(record, "harness", {}))
        assert value not in text and "/tmp/.cache" not in text and "hosts.yml" not in text


async def test_cs7_a_cancelled_turn_takes_no_snapshot() -> None:
    world = snapshot_world()
    run = world.run()
    runner = asyncio.ensure_future(world.controller.run_turn(run, task(0, f"write {CACHE} warm\nhold\nsay never")))
    await asyncio.wait_for(world.gateway.holding.wait(), 5)
    assert await world.controller.cancel(run, reason="user")
    outcome = await asyncio.wait_for(runner, 5)
    assert outcome.cancelled
    assert world.sink.snapshots == []
