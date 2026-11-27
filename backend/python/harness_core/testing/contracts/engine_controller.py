"""Contract suites for the ports the engine controller writes to (E0).

* ``EngineCheckpointStoreContract``: provide ``checkpoint_env`` (``CheckpointEnv``).
* ``EngineEventSinkContract``: provide ``sink_env`` (``SinkEnv``).
* ``ToolGatewayHostContract``: provide ``host_env`` (``HostEnv``): the host
  under test and a client that calls a tool through the upstream it yields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.engines.errors import EngineStateUnavailable
from harness_core.engines.events import (
    CommandStarted,
    EngineEventMeta,
    EngineState,
    EngineStateBlob,
    FileChanged,
    ToolCallSeen,
    turn_id_for,
)
from harness_core.engines.types import EngineVersion
from harness_core.interactions.ports import RunBinding
from harness_core.testing.contracts.tool_endpoint import BINDING, build_endpoint
from harness_core.workspace.types import ObjectRef, build_manifest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.engines.events import EngineEvent
    from harness_core.engines.ports import (
        EngineCheckpointStore,
        EngineEventSink,
        ToolGatewayHost,
    )
    from harness_core.gateway.types import GatewayBinding
    from harness_core.workspace.types import ObjectScope

VERSION = EngineVersion("opencode", "1.0.0", "sha256:" + "1" * 64)
STATE = EngineState(engine=VERSION, schema_version=1, payload=b'{"format":"x","sessionId":"s1","export":{}}')
RUN = RunBinding("org-1", "session-1", "run-1")


class CheckpointEnv(Protocol):
    @property
    def store(self) -> EngineCheckpointStore: ...

    @property
    def scope(self) -> ObjectScope: ...

    def tamper(self) -> None:
        """Change the stored bytes of every object, keeping their references."""
        ...

    def lose(self) -> None:
        """Remove every stored object."""
        ...

    async def shred(self) -> bool:
        """Destroy the scope's data key; ``False`` when the store does not seal."""
        ...


class EngineCheckpointStoreContract:
    @pytest.fixture
    def checkpoint_env(self) -> CheckpointEnv:
        raise NotImplementedError("provide a checkpoint_env fixture")

    async def test_cp1_save_then_load_round_trips(self, checkpoint_env: CheckpointEnv) -> None:
        blob = await checkpoint_env.store.save(checkpoint_env.scope, STATE)
        assert (blob.engine, blob.schema_version) == (VERSION, 1)
        assert await checkpoint_env.store.load(checkpoint_env.scope, blob) == STATE

    async def test_cp2_missing_bytes_are_state_missing(self, checkpoint_env: CheckpointEnv) -> None:
        blob = await checkpoint_env.store.save(checkpoint_env.scope, STATE)
        checkpoint_env.lose()
        with pytest.raises(EngineStateUnavailable) as refused:
            await checkpoint_env.store.load(checkpoint_env.scope, blob)
        assert refused.value.reason_code == "state_missing"

    async def test_cp2_changed_bytes_are_state_corrupt(self, checkpoint_env: CheckpointEnv) -> None:
        blob = await checkpoint_env.store.save(checkpoint_env.scope, STATE)
        checkpoint_env.tamper()
        with pytest.raises(EngineStateUnavailable) as refused:
            await checkpoint_env.store.load(checkpoint_env.scope, blob)
        assert refused.value.reason_code == "state_corrupt"

    async def test_cp2_a_blob_naming_other_bytes_is_state_corrupt(self, checkpoint_env: CheckpointEnv) -> None:
        blob = await checkpoint_env.store.save(checkpoint_env.scope, STATE)
        other = await checkpoint_env.store.save(
            checkpoint_env.scope, EngineState(VERSION, 1, b'{"format":"x","sessionId":"s2","export":{}}')
        )
        content = ObjectRef(other.content.object_id, blob.content.content_hash, other.content.size_bytes)
        forged = EngineStateBlob(blob.engine, blob.schema_version, content)
        with pytest.raises(EngineStateUnavailable) as refused:
            await checkpoint_env.store.load(checkpoint_env.scope, forged)
        assert refused.value.reason_code in ("state_corrupt", "state_missing")

    async def test_cp3_a_shredded_session_key_makes_the_state_unreadable(self, checkpoint_env: CheckpointEnv) -> None:
        blob = await checkpoint_env.store.save(checkpoint_env.scope, STATE)
        if not await checkpoint_env.shred():
            pytest.skip("this store does not seal")
        with pytest.raises(EngineStateUnavailable) as refused:
            await checkpoint_env.store.load(checkpoint_env.scope, blob)
        assert refused.value.reason_code == "state_missing"


class SinkEnv(Protocol):
    @property
    def sink(self) -> EngineEventSink: ...

    async def recorded(self, run: RunBinding) -> Sequence[EngineEvent]:
        """The events the sink kept for ``run``, in order, once the turn is over (a journal sink batches)."""
        ...

    async def saves(self, run: RunBinding) -> Sequence[int]:
        """Revisions the sink recorded as saved for ``run``, in order."""
        ...


class EngineEventSinkContract:
    @pytest.fixture
    def sink_env(self) -> SinkEnv:
        raise NotImplementedError("provide a sink_env fixture")

    # What a person sees move; text and reasoning deltas may wait for the turn's own commit (a journal sink's).
    async def test_es1_events_saves_and_checkpoints_in_order(self, sink_env: SinkEnv) -> None:
        turn = turn_id_for(RUN.run_id, 0)
        events: list[EngineEvent] = [
            CommandStarted(EngineEventMeta(turn, 0), "c1", "ls"),
            FileChanged(EngineEventMeta(turn, 1), "c1", "a.txt", "added"),
            ToolCallSeen(EngineEventMeta(turn, 2), "t1", "search", "completed"),
        ]
        for event in events:
            await sink_env.sink.event(RUN, event)
        # A save follows a commit, so its revision is at least 1 (the journal refuses a revision 0).
        saved = build_manifest("ws:session-1", revision=1, parent_revision=0, files={}, created_at_ms=0)
        await sink_env.sink.workspace_saved(RUN, saved, "turn_end")
        assert list(await sink_env.recorded(RUN)) == events
        assert list(await sink_env.saves(RUN)) == [1]

    async def test_es2_a_redriven_event_is_recorded_once(self, sink_env: SinkEnv) -> None:
        turn = turn_id_for(RUN.run_id, 3)
        first = CommandStarted(EngineEventMeta(turn, 0), "c1", "ls")
        await sink_env.sink.event(RUN, first)
        await sink_env.sink.event(RUN, CommandStarted(EngineEventMeta(turn, 0), "c1", "ls"))
        other_run = RunBinding("org-1", "session-1", "run-2")
        await sink_env.sink.event(other_run, first)
        assert list(await sink_env.recorded(RUN)) == [first]
        assert list(await sink_env.recorded(other_run)) == [first]


class HostEnv(Protocol):
    @property
    def host(self) -> ToolGatewayHost: ...

    async def call(
        self, upstream: str, binding: GatewayBinding, name: str, arguments: Mapping[str, JsonValue]
    ) -> str | None:
        """The tool's text through ``upstream``; ``None`` when nothing answers there."""
        ...


@dataclass
class _Served:
    upstream: str


class ToolGatewayHostContract:
    @pytest.fixture
    def host_env(self) -> HostEnv:
        raise NotImplementedError("provide a host_env fixture")

    async def test_tg1_a_mounted_endpoint_answers_through_its_upstream_until_unmounted(self, host_env: HostEnv) -> None:
        world = build_endpoint()
        async with host_env.host.serve(world.endpoint, BINDING) as upstream:
            text = await host_env.call(upstream, BINDING, "lookup", {"q": "q3"})
            assert text == "lookup:q3"
            served = _Served(upstream)
        assert world.runs == ["lookup"]
        assert await host_env.call(served.upstream, BINDING, "lookup", {"q": "q4"}) is None
        assert world.runs == ["lookup"]

    async def test_tg1_two_mounts_get_two_upstreams(self, host_env: HostEnv) -> None:
        first, second = build_endpoint(), build_endpoint()
        async with host_env.host.serve(first.endpoint, BINDING) as a, host_env.host.serve(second.endpoint, BINDING) as b:
            assert a != b
            assert await host_env.call(b, BINDING, "lookup", {"q": "x"}) == "lookup:x"
        assert (first.runs, second.runs) == ([], ["lookup"])


__all__ = [
    "CheckpointEnv",
    "EngineCheckpointStoreContract",
    "EngineEventSinkContract",
    "HostEnv",
    "SinkEnv",
    "ToolGatewayHostContract",
]
