"""Round 14 (WP-67 ``ph publish`` x WP-69 SEC-FS-02): a published name passes the one shared safe-name check.

``ph publish`` names a deliverable; ``pipeshub.artifact_publish`` publishes it from the outputs folder; a save
publishes whatever is there. All three hold the name to ``harness_core.workspace.safe_names.check_name_path``, pinned
by ``contracts/harness/vectors/safe-names.json`` (``outputPaths``). ``ph`` runs in the sandbox on the standard library
alone, so it carries the same rules and the same vectors pin it, as they pin Node.

R14-PUB-1 the gateway's publish path takes exactly what the shared check takes, in NFC, and refuses the rest.
R14-PUB-2 a name no computer can hold as one file (``CON.txt``) is refused before the turn saves, saying why.
R14-PUB-3 a token for another binding is refused before the turn saves, as ``ph inputs get``'s call is.
R14-PUB-4 ``ph publish`` refuses the same names with the same reasons and writes nothing (see the ph test).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from harness_core.engines.publish_tool import (
    ARTIFACT_PUBLISH_TOOL,
    INVALID_OUTPUT_PATH,
    output_path,
)
from harness_core.gateway.errors import BindingMismatch
from harness_core.gateway.types import McpResult
from harness_core.interactions.ports import RunBinding
from harness_core.workspace.safe_names import check_name_path
from tests.unit.harness_core.engines.test_controller import World, task

VECTORS = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts/harness/vectors/safe-names.json").read_text(encoding="utf-8")
)["outputPaths"]


@pytest.mark.parametrize(("raw", "canonical"), VECTORS["accepted"])
def test_r14_pub_1_a_name_the_shared_check_takes_is_published_in_nfc(raw: str, canonical: str) -> None:
    assert check_name_path(raw) == canonical
    assert output_path({"path": raw}) == canonical


@pytest.mark.parametrize(("raw", "reason"), VECTORS["refused"])
def test_r14_pub_1_a_name_the_shared_check_refuses_is_never_published(raw: str, reason: str) -> None:
    assert output_path({"path": raw}) is None, reason


async def _publish_in_turn(world: World, path: str) -> McpResult:
    runner = asyncio.ensure_future(world.controller.run_turn(world.run(), task(0, "write out/a.md x\nhold\nsay ok")))
    await asyncio.wait_for(world.gateway.holding.wait(), 5)
    try:
        return await world.host.call(ARTIFACT_PUBLISH_TOOL, {"path": path})
    finally:
        world.gateway.release()
        _ = await asyncio.wait_for(runner, 5)


@pytest.fixture
def world() -> World:
    return World()


@pytest.mark.parametrize(("name", "said"), [("CON.txt", "device name"), ("report.", "ends with a dot"), ("a:b.csv", "drive")])
async def test_r14_pub_2_a_name_no_computer_can_hold_is_refused_before_the_save_saying_why(
    world: World, name: str, said: str
) -> None:
    answer = await _publish_in_turn(world, name)
    assert (answer.is_error, answer.reason_code) == (True, INVALID_OUTPUT_PATH)
    assert said in answer.text, answer.text
    assert "before_effect" not in [s.reason for s in world.sink.saves]


async def test_r14_pub_3_a_token_for_another_binding_never_saves_or_publishes() -> None:
    from harness_core.engines.controller import (
        _SavingEndpoint,  # pyright: ignore[reportPrivateUsage]
    )

    binding = RunBinding("org-1", "session-1", "run-pub3")
    endpoint = _SavingEndpoint(object(), binding)  # type: ignore[arg-type]
    saved: list[str] = []

    async def before() -> None:
        saved.append("before_effect")

    endpoint.before = before
    endpoint.publication = lambda path: McpResult(path, is_error=False, disposition="executed", retryable=False)
    claims = type("Claims", (), {"binding": RunBinding("org-1", "session-1", "another-run")})()
    with pytest.raises(BindingMismatch):
        _ = await endpoint.call_tool(claims, ARTIFACT_PUBLISH_TOOL, {"path": "a.md"})  # type: ignore[arg-type]
    assert saved == []
    mine = type("Claims", (), {"binding": binding})()
    answer = await endpoint.call_tool(mine, ARTIFACT_PUBLISH_TOOL, {"path": "a.md"})  # type: ignore[arg-type]
    assert (answer.text, saved) == ("a.md", ["before_effect"])
