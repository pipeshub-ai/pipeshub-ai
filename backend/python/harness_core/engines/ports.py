"""Engine ports (ARCHITECTURE §4.1): adapter, session, registry, and what the controller writes to.

The adapter is stateless and picked by the registry; a session is one running
engine process in one sandbox. The controller depends on these protocols only:
engine state goes to an ``EngineCheckpointStore``, what happens to an
``EngineEventSink``, and a run's tool endpoint is served by a ``ToolGatewayHost``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

from harness_core.security.labels import SourceLedger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping
    from contextlib import AbstractAsyncContextManager

    from harness_core.context.user_compact import CompactReport
    from harness_core.engines.events import EngineEvent, EngineState, EngineStateBlob
    from harness_core.engines.native import TranscriptPart
    from harness_core.engines.profiles import AcpOptIn
    from harness_core.engines.snapshots import SnapshotReason
    from harness_core.engines.types import (
        EngineCapabilities,
        EngineKind,
        EngineLaunch,
        EngineProfile,
        EngineTask,
        EngineVersion,
        FollowUp,
        GatewayEndpoints,
        ProfileConstraint,
        SessionHealth,
    )
    from harness_core.gateway.ports import ToolEndpoint
    from harness_core.gateway.types import GatewayBinding
    from harness_core.interactions.ports import InteractionDecision, RunBinding
    from harness_core.sandbox import SandboxHandle
    from harness_core.workspace.changes import CommitReason
    from harness_core.workspace.types import ObjectRef, ObjectScope, WorkspaceManifest


class EngineSession(Protocol):
    def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        """Events of one turn, ending with ``TurnCompleted``. Buffered with a bound:
        a slow consumer slows the engine's stream instead of growing memory."""
        ...

    async def steer(self, text: str) -> None:
        """Add text to the running turn; ``SteeringUnsupported`` unless ``can_steer``."""
        ...

    async def interrupt(self) -> None: ...

    async def compact(self, focus: str | None, *, instructions: str = "") -> CompactReport:
        """The person's ``/compact [focus]`` (SLASH2 C2), between turns: the engine's own compaction of the session,
        never a user turn and never the person's text. ``instructions``: what the first turn would open the session
        with. ``CompactUnsupported`` unless ``can_compact``; ``CompactFailed`` when the engine did not finish."""
        ...

    async def answer(self, request_id: str, decision: InteractionDecision) -> None: ...

    async def capture_state(self) -> EngineState:
        """State to resume from in a fresh sandbox. Never holds credentials."""
        ...

    async def health(self) -> SessionHealth: ...

    async def close(self) -> None:
        """Stop the engine. Idempotent."""
        ...


class EngineAdapter(Protocol):
    kind: ClassVar[EngineKind]

    def capabilities(self) -> EngineCapabilities: ...

    def render_launch(
        self, profile: EngineProfile, endpoints: GatewayEndpoints, skills_dir: str
    ) -> EngineLaunch:
        """Pure: argv, env and files for the engine. Holds placeholders, never secrets."""
        ...

    async def open(
        self,
        sandbox: SandboxHandle,
        launch: EngineLaunch,
        restore: EngineState | None,
        *,
        correlation: Mapping[str, str] | None = None,
    ) -> EngineSession:
        """Start the engine. Refuses ``restore`` from another engine version or an
        unknown schema version (``EngineStateIncompatible``) before starting anything."""
        ...


class EngineRegistry(Protocol):
    def adapter_for(self, version: EngineVersion) -> EngineAdapter:
        """The adapter for an allow-listed version; ``EngineVersionNotAllowed`` otherwise."""
        ...


@runtime_checkable
class EngineSuccessors(Protocol):
    """Where a run pinned to a version the release retired continues (ADR-OPS-01 D6, UP-02)."""

    def resumed_on(self, version: EngineVersion) -> EngineVersion | None:
        """The declared successor of ``version``; ``None`` when it runs itself or nothing continues it."""
        ...


class EngineCheckpointStore(Protocol):
    """Engine state as stored: bytes in the session's object scope, so erasing the session erases it."""

    async def save(self, scope: ObjectScope, state: EngineState) -> EngineStateBlob: ...

    async def load(self, scope: ObjectScope, blob: EngineStateBlob) -> EngineState:
        """``EngineStateUnavailable`` (``state_missing``, ``state_corrupt``) when the bytes are gone or changed."""
        ...


class SandboxSnapshotStore(Protocol):
    """Sandbox snapshots (P5.9b): tar bytes in the session's object scope, content-addressed, never another
    session's; erasing the session erases them."""

    async def save(self, scope: ObjectScope, archive: bytes) -> ObjectRef: ...

    async def load(self, scope: ObjectScope, ref: ObjectRef) -> bytes:
        """``SnapshotUnavailable`` (``snapshot_missing``, ``snapshot_corrupt``, ``snapshot_foreign``)."""
        ...

    async def content_hash(self, scope: ObjectScope, archive: bytes) -> str:
        """The ``content_hash`` a ``save`` of ``archive`` would carry, storing nothing."""
        ...


class EngineEventSink(Protocol):
    """Where an engine turn's events, workspace saves and checkpoints go (a journal, a request, a test).

    An event already recorded for the same ``(turn_id, seq)`` is dropped: a re-driven turn
    produces the same command ids (ARCHITECTURE §4.8).
    """

    async def event(self, run: RunBinding, event: EngineEvent) -> None: ...

    async def workspace_saved(self, run: RunBinding, manifest: WorkspaceManifest, reason: CommitReason) -> None: ...

    async def checkpointed(self, run: RunBinding, blob: EngineStateBlob) -> None: ...

    async def snapshotted(self, run: RunBinding, snapshot: ObjectRef, reason: SnapshotReason) -> None:
        """A sandbox snapshot was stored (P5.9b): the run's latest is what its next attempt restores."""
        ...


@runtime_checkable
class NativeTranscriptSink(Protocol):
    """A sink that keeps a turn's native transcript (C2.39): each part sealed in the run's content store, so
    erasing the conversation erases it with the rest of its journal."""

    async def native_transcript(self, run: RunBinding, part: TranscriptPart) -> None: ...


class ToolGatewayHost(Protocol):
    """Serves one run's tool endpoint for as long as the context is open.

    Yields the base URL the egress proxy forwards that sandbox's tool-gateway
    requests to (``EgressDestination.upstream``); after the context, nothing answers there.
    """

    def serve(self, endpoint: ToolEndpoint, binding: GatewayBinding) -> AbstractAsyncContextManager[str]: ...


class ToolListings(Protocol):
    """What the Tool Gateway saw of one attempt: how many times it served that sandbox the run's tool list, and how
    many MCP handshakes (``initialize`` or ``server/discover``) it answered that sandbox."""

    def listed(self) -> int: ...

    def connected(self) -> int: ...


@runtime_checkable
class WitnessesToolGateway(Protocol):
    """A session that cannot ask its engine whether it holds the tool gateway (Gemini CLI over ACP, E2E3-F6) and
    asks the gateway instead: the controller hands it the attempt's ``ToolListings`` before the first turn."""

    def witness_tool_gateway(self, listings: ToolListings) -> None: ...


@runtime_checkable
class RestartsFromSummary(Protocol):
    """A session whose engine cannot compact itself (ACP has no such request) but which the harness can restart: a
    fresh engine session in the same sandbox, its first prompt a summary of the conversation so far. MG-10's overflow
    chain uses it only when the engine's own ``compact`` is unsupported; the person's ``/compact`` stays
    ``can_compact``'s. ``CompactFailed`` when there is nothing to restart."""

    async def restart_from_summary(self, task: EngineTask | FollowUp) -> CompactReport: ...


class EngineCeilingSource(Protocol):
    """Each org's engine profile ceiling (E4a), read afresh on every call.

    ``None`` when the org set none. Raises ``ProfileCeilingUnavailable`` when the ceiling
    cannot be read (the caller refuses rather than run on the release default) and
    ``ProfileInvalid`` when what is stored is not a narrowing.
    """

    async def ceiling(self, org_id: str) -> ProfileConstraint | None: ...


@dataclass(frozen=True, slots=True)
class AcpEngineOptIn:
    """The edition's ACP opt-in (``AcpOptIn``): ``release`` unless the edition lets an org's ceiling decide."""

    by: AcpOptIn


RELEASED_ACP_ENGINES = AcpEngineOptIn("release")
ORG_NAMED_ACP_ENGINES = AcpEngineOptIn("org")


__all__ = [
    "ORG_NAMED_ACP_ENGINES",
    "RELEASED_ACP_ENGINES",
    "AcpEngineOptIn",
    "NativeTranscriptSink",
    "SourceLedger",
    "EngineAdapter",
    "EngineCeilingSource",
    "EngineCheckpointStore",
    "EngineEventSink",
    "EngineRegistry",
    "EngineSuccessors",
    "EngineSession",
    "SandboxSnapshotStore",
    "ToolGatewayHost",
    "ToolListings",
    "RestartsFromSummary",
    "WitnessesToolGateway",
]
