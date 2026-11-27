"""ANSWER-CONTRACT-FU-3 (ADR-RUN-03 D2): a job is offered ``ask_user`` only when its parent can ask a person.

Node pins it on the job at admission (``x.pipeshub.parentAsks``: a person's run, or a job of one; never a schedule's
or a webhook's firing). Here the worker's own engine assembly, over a run claimed from a live Node, lists the job's
gateway tools: the question tool is there only with the pin true, so a job of a run nobody watches never ends
waiting on an answer, and reports what it did not find in its result instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from app.harness.assembly.context import pinned_parent_asks
from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.gateway.asking import ASK_USER
from harness_core.gateway.types import GatewayClaims
from harness_core.testing.engines.durable import engine_profile, engine_run_spec
from tests.unit.harness.assembly import world
from tests.unit.harness.engines.test_qa6fix_engine_memory import (
    _Engine,  # pyright: ignore[reportPrivateUsage]
)
from tests.unit.harness.test_node_journal_contract import node_server

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.unit.harness.test_node_journal_contract import (
        _NodeServer,  # pyright: ignore[reportPrivateUsage]
    )

__all__ = ["node_server"]

UNSET = object()


@pytest.fixture
def _deployment() -> Iterator[None]:
    with world.deployment():
        yield


@pytest.fixture
def engine(node_server: _NodeServer, _deployment: None) -> _Engine:
    return _Engine(node_server)


def _spec(kind: str, parent_asks: object) -> RunSpec:
    executor = engine_run_spec().executor
    assert executor is not None
    pinned = cast("dict[str, Any]", dump_contract(world.pinned_spec(tool_ids=[], executor=dump_contract(executor))))
    pinned["runKind"] = kind
    if parent_asks is not UNSET:
        pinned["extensions"]["x.pipeshub"]["parentAsks"] = parent_asks
    return validate_contract(RunSpec, pinned)


async def _listed(engine: _Engine, spec: RunSpec) -> set[str]:
    run_id = f"run-fu3-{spec.run_kind}-{id(spec)}"
    node = engine._node  # pyright: ignore[reportPrivateUsage]
    node.admit(run_id, session_id=f"session-{run_id}", input_text="say hello", spec=spec)
    claim = await node.journal.claim(run_id, worker_id="worker-fu3")
    assert claim is not None
    runtime = await engine.assembler.build(engine._kernel().attempt_of(claim, engine_profile()))  # pyright: ignore[reportPrivateUsage]
    try:
        claims = GatewayClaims(runtime.tool_binding, kid="fu3", expires_at=4_102_444_800)
        return {tool.name for tool in await runtime.tools.list_tools(claims)}
    finally:
        if runtime.close is not None:
            await runtime.close()


@pytest.mark.parametrize(
    ("kind", "parent_asks", "asks"),
    [
        # A job of a person's run: its question is the chat's card (ADR-RUN-03 D2).
        ("child", True, True),
        # A job of a schedule's or a webhook's run: nobody to answer it.
        ("child", False, False),
        # A job pinned before FU-3, or by a Node whose workers did not read the pin: not offered.
        ("child", UNSET, False),
        # A person's own run asks whatever the pin says (a root run carries none).
        ("interactive", UNSET, True),
    ],
)
async def test_fu3_a_job_asks_only_for_a_parent_that_can(engine: _Engine, kind: str, parent_asks: object, asks: bool) -> None:
    spec = _spec(kind, parent_asks)
    assert pinned_parent_asks(spec) is (parent_asks is True)
    assert (ASK_USER in await _listed(engine, spec)) is asks
