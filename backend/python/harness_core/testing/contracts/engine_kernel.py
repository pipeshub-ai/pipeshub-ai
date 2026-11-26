"""Contract suites for what the durable engine kernel asks its host for (E1).

* ``EngineRunPresenterContract``: provide ``presenter`` (an ``EngineRunPresenter``). Every
  presented line is a non-critical event, numbered only from the stamp it was given, and no
  method raises on content it cannot show.
* ``EngineRuntimeAssemblerContract``: provide ``assembler_env`` (``AssemblerEnv``). What it
  builds serves the run's grant through the attempt's effect log, names the idempotency of every
  granted tool, and reads back what it stores.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.artifacts.types import ArtifactCard
from harness_core.engines.durable.ports import ProgressStamp
from harness_core.engines.events import (
    CommandFinished,
    CommandOutput,
    CommandStarted,
    EngineEventMeta,
    FileChanged,
    MessageCompleted,
    SubAgentStarted,
    TextDelta,
    ToolCallSeen,
    TurnCompleted,
    UsageReported,
)
from harness_core.gateway.asking import ASK_USER, asks_person
from harness_core.gateway.types import GatewayClaims
from harness_core.workspace.types import empty_manifest

if TYPE_CHECKING:
    from harness_core.engines.durable.ports import (
        EngineAttempt,
        EngineRunPresenter,
        EngineRuntimeAssembler,
    )
    from harness_core.journal.types import NewEvent

META = EngineEventMeta("trn_contract", 0)
EVENTS = (
    TextDelta(META, "m", "partial"),
    MessageCompleted(META, "m", "done"),
    CommandStarted(META, "c1", "python report.py"),
    CommandOutput(META, "c1", "x" * 50_000),
    CommandFinished(META, "c1", 0, "completed"),
    FileChanged(META, "c1", "out/report.md", "added"),
    ToolCallSeen(META, "t1", "search", "started"),
    SubAgentStarted(META, "s1", "t2", "helper"),
    UsageReported(META, 10, 5),
    TurnCompleted(META, "completed", "fin"),
)


def _stamp() -> tuple[ProgressStamp, list[int]]:
    numbers = itertools.count(100)
    handed: list[int] = []

    def allocate() -> int:
        n = next(numbers)
        handed.append(n)
        return n

    return ProgressStamp("run-contract", "trn_contract", allocate), handed


class EngineRunPresenterContract:
    @pytest.fixture
    def presenter(self) -> EngineRunPresenter:
        raise NotImplementedError("provide a presenter fixture")

    async def test_every_line_is_non_critical_and_numbered_from_its_stamp(self, presenter: EngineRunPresenter) -> None:
        stamp, handed = _stamp()
        lines: list[NewEvent] = [
            *presenter.started(stamp, "Write the Q3 report"),
            *(line for event in EVENTS for line in presenter.event(stamp, event)),
            *await presenter.saved(stamp, empty_manifest("ws:s"), "turn_end"),
            *presenter.artifact(
                stamp,
                ArtifactCard("a1", 1, "report.md", "text/markdown", 10, "file", "draft"),
            ),
            *presenter.approval(stamp, "ix.approve.k1", "create", {"name": "x"}),
            *presenter.finished(stamp, success=True, reason=None, output="done"),
        ]
        assert all(not line.critical for line in lines)
        assert len(handed) == len(set(handed))

    async def test_text_deltas_and_turn_ends_are_never_lines(self, presenter: EngineRunPresenter) -> None:
        stamp, _ = _stamp()
        assert presenter.event(stamp, TextDelta(META, "m", "x")) == []
        assert presenter.event(stamp, TurnCompleted(META, "completed")) == []


class AssemblerEnv(Protocol):
    @property
    def assembler(self) -> EngineRuntimeAssembler: ...

    async def attempt(self) -> EngineAttempt:
        """An attempt of an admitted engine run, as the kernel hands it over."""
        ...


class EngineRuntimeAssemblerContract:
    @pytest.fixture
    def assembler_env(self) -> AssemblerEnv:
        raise NotImplementedError("provide an assembler_env fixture")

    async def test_the_endpoint_serves_the_grant_and_names_every_tools_idempotency(
        self, assembler_env: AssemblerEnv
    ) -> None:
        attempt = await assembler_env.attempt()
        runtime = await assembler_env.assembler.build(attempt)
        try:
            claims = GatewayClaims(runtime.tool_binding, kid="contract", expires_at=4_102_444_800)
            listed = await runtime.tools.list_tools(claims)
            # The grant, and the gateway's own ``ask_user`` when the run has a person to ask (C3.70).
            own = {ASK_USER} if asks_person(attempt.claim.spec) else set[str]()
            names = [tool.name for tool in listed]
            assert len([n for n in names if n not in own]) == runtime.grant_size
            assert own <= set(names)
            assert len(runtime.idempotent) >= runtime.grant_size
            assert runtime.controller.capabilities_for(attempt.profile.engine) is not None
        finally:
            if runtime.close is not None:
                await runtime.close()

    async def test_the_content_store_reads_back_what_it_stores(self, assembler_env: AssemblerEnv) -> None:
        runtime = await assembler_env.assembler.build(await assembler_env.attempt())
        try:
            ref = await runtime.content.put(b"final text")
            assert await runtime.content.get(ref) == b"final text"
        finally:
            if runtime.close is not None:
                await runtime.close()


__all__ = [
    "AssemblerEnv",
    "EngineRunPresenterContract",
    "EngineRuntimeAssemblerContract",
]
