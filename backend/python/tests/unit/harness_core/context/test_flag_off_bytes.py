"""P4 FL-2: with the context compiler off, every model request the kernel builds is
byte-equal to what it built before P4 (goldens recorded at 722d9bc58).

Record with ``UPDATE_GOLDENS=1`` only when a change to today's request assembly is intended.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.journal import LegacyImport
from harness_core.kernel.durable.audience import HistoryAudience
from harness_core.testing.run_spec import minimal_run_spec
from tests.unit.harness_core.durable.support import ORG, SESSION, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from harness_core.kernel.durable.ports import ModelCall

GOLDENS = Path(__file__).parent / "goldens" / "flag_off"
ALICE, BOB = "p_alice", "p_bob"


def _dump(call: ModelCall) -> dict[str, Any]:
    return {
        "system": call.system,
        "tools": None if call.tools is None else [t.model_dump(mode="json") for t in call.tools],
        "messages": [m.model_dump(mode="json") for m in call.messages],
        "inputIndex": call.input_index,
        "slots": dict(call.slots),
    }


def _admit(world: World, run_id: str, principal: str, text: str, **extra: Any) -> None:  # noqa: ANN401
    world.journal.admit(
        run_id,
        org_id=ORG,
        session_id=SESSION,
        spec=minimal_run_spec(),
        input_text=text,
        principal_ref=principal,
        **extra,
    )


async def _tools_across_runs(world: World) -> list[ModelCall]:
    _admit(world, "run-1", ALICE, "find the issue and file it")
    _, first = scripted(
        reply("looking", ("lookup", {"q": "bug"})),
        reply("", ("lookup", {"q": "a"}), ("lookup", {"q": "b"})),
        reply("filing", ("create_issue", {"title": "bug"})),
        reply("filed it"),
    )
    assert (await world.run(world.kernel(first), "run-1")).status == "completed"
    _admit(world, "run-2", ALICE, "and the second one?")
    _, second = scripted(reply("", ("lookup", {"q": "second"})), reply("done"))
    assert (await world.run(world.kernel(second), "run-2")).status == "completed"
    return [*first.calls, *second.calls]


async def _shared_session(world: World) -> list[ModelCall]:
    _admit(world, "run-a", ALICE, "alice asks")
    _, alice = scripted(reply("", ("lookup", {"q": "secret"})), reply("alice's answer"))
    assert (await world.run(world.kernel(alice), "run-a")).status == "completed"
    _admit(world, "run-b", BOB, "bob asks", session_role="collaborator")
    _, bob = scripted(reply("bob's answer"))
    stubbed = HistoryAudience(principal=BOB, owner=ALICE, own_runs=frozenset())
    assert (await world.run(world.kernel(bob, audience=stubbed), "run-b")).status == "completed"
    _admit(world, "run-c", BOB, "bob again", session_role="collaborator")
    _, again = scripted(reply("ok"))
    passed = HistoryAudience(principal=BOB, owner=ALICE, own_runs=frozenset({"run-b"}), visible_runs=frozenset({"run-a"}))
    assert (await world.run(world.kernel(again, audience=passed), "run-c")).status == "completed"
    return [*bob.calls, *again.calls]


async def _imported_history(world: World) -> list[ModelCall]:
    legacy: list[dict[str, Any]] = [
        {"role": "user_query", "content": "earlier question"},
        {"role": "bot_response", "content": "earlier answer"},
    ]
    _admit(world, "run-i", ALICE, "follow up", imported=LegacyImport(legacy, 2))
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("answered"))
    assert (await world.run(world.kernel(port, system="You are a test agent."), "run-i")).status == "completed"
    return port.calls


SCENARIOS: dict[str, Callable[[World], Awaitable[list[ModelCall]]]] = {
    "tools_across_runs": _tools_across_runs,
    "shared_session": _shared_session,
    "imported_history": _imported_history,
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
async def test_fl2_requests_with_the_compiler_off_are_byte_equal_to_before(name: str) -> None:
    calls = await SCENARIOS[name](World())
    rendered = json.dumps([_dump(c) for c in calls], indent=1, sort_keys=True) + "\n"
    path = GOLDENS / f"{name}.json"
    if os.environ.get("UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(rendered, encoding="utf-8")
        return
    assert path.exists(), f"missing golden {path}; record it with UPDATE_GOLDENS=1"
    assert rendered == path.read_text(encoding="utf-8")
