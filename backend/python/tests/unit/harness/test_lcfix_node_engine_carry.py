"""LCFIX on a live Node: a Coder turn's second ``code_task`` child is admitted with the engine state the first one
checkpointed (``x.pipeshub.engineCarry``), so its kernel opens restored, and a forked conversation's is not.

Node is real (``journal-http-server.ts``: the internal routes, ``ChildRunService`` and its ``engineCarry`` over the
sealed in-memory journal); this process plays the worker over HTTP.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.engines.durable.carry import pinned_carry
from harness_core.engines.profiles import profile_json
from harness_core.engines.types import EngineVersion
from harness_core.journal.children import (
    ChildExecutorRequest,
    ChildGrantRequest,
    ChildRequest,
)
from harness_core.journal.types import CommitRequest, Complete, NewEvent
from harness_core.testing.contracts.children import RESULT
from harness_core.testing.engines.durable import engine_profile
from harness_core.testing.run_spec import minimal_run_spec
from tests.unit.harness.test_node_journal_contract import (  # noqa: F401 - the fixture
    NodeHarness,
    node_server,
)

if TYPE_CHECKING:
    from harness_core.journal.types import Claim
    from tests.unit.harness.test_node_journal_contract import (
        _NodeServer,  # pyright: ignore[reportPrivateUsage]
    )

OPENCODE = EngineVersion(kind="opencode", version="1.18.34", binary_digest="sha256:" + "a" * 64)  # type: ignore[arg-type]


def _coder_turn_spec() -> RunSpec:
    """A Coder agent's turn: the loop, with the engine authority its code_task child narrows (CTL-6)."""
    raw = minimal_run_spec(max_children=2, executor={"kind": "loop"}).model_dump(mode="json", by_alias=True, exclude_none=True)
    raw["model"]["policy"] = [{"provider": "openai", "model": "gpt-5"}]
    raw["extensions"] = {
        "x.pipeshub": {
            "knowledge": {"apps": [], "kb": [], "strictScope": False},
            "mcp": [],
            "skills": [],
            "mounts": [],
            "via": "session",
            "codeTask": {"profile": profile_json(replace(engine_profile(), engine=OPENCODE))},
        }
    }
    return validate_contract(RunSpec, raw)


async def _commit(harness: NodeHarness, claim: Claim, kind: str, events: tuple[NewEvent, ...], transition: Complete | None) -> None:
    _ = await harness.journal.commit(CommitRequest(f"{claim.attempt_id}:{kind}:1", claim.token, events, transition))


async def _code_task(harness: NodeHarness, turn: str, session: str, *, finish: bool) -> Claim:
    """One Coder turn's code_task child, claimed; with ``finish``, it checkpoints and both runs complete."""
    harness.admit(turn, session_id=session, spec=_coder_turn_spec())
    parent = await harness.journal.claim(turn, worker_id="w-loop")
    assert parent is not None
    admitted = await harness.node.spawn(
        parent.token,
        ChildRequest(invocation_id=f"{turn}.code", goal="edit notes.md", grants=ChildGrantRequest(), executor=ChildExecutorRequest()),
    )
    child = await harness.journal.claim(admitted.child_run_id, worker_id="w-engine")
    assert child is not None
    if finish:
        checkpointed = NewEvent(
            "engine.checkpointed",
            {
                "blob": {"objectId": f"state-{turn}", "contentHash": "sha256:" + "b" * 64, "sizeBytes": 42},
                "engine": {"kind": "opencode", "version": "1.18.34", "binaryDigest": OPENCODE.binary_digest},
                "schemaVersion": 1,
                "turnId": f"turn-of-{turn}",
            },
            critical=False,
        )
        await _commit(harness, child, "complete", (checkpointed,), Complete("answered", child_result=RESULT))
        await _commit(harness, parent, "complete", (), Complete("answered"))
    return child


async def test_lcfix_the_second_code_task_is_admitted_with_the_first_ones_engine_state(node_server: _NodeServer) -> None:  # noqa: F811
    harness = NodeHarness(node_server)
    first = await _code_task(harness, "turn-1", "ses-lc", finish=True)
    assert pinned_carry(first.spec, engine_kind="opencode") is None
    second = await _code_task(harness, "turn-2", "ses-lc", finish=False)
    carried = pinned_carry(second.spec, engine_kind="opencode")
    assert carried is not None, dump_contract(second.spec)
    run_id, state, snapshot = carried
    assert (run_id, state.content.object_id, snapshot) == (first.run_id, "state-turn-1", None)
    # Another engine kind decides nothing, and a forked conversation is another session.
    assert pinned_carry(second.spec, engine_kind="codex") is None
    forked = await _code_task(harness, "turn-f", "ses-lc-fork", finish=False)
    assert pinned_carry(forked.spec, engine_kind="opencode") is None
