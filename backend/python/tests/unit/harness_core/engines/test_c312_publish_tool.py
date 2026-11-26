"""C3.12 (WS-15, SEC-FS-02): ``ph publish``'s gateway call, ``pipeshub.artifact_publish``, answered by the engine
controller's endpoint (PUB-1..PUB-7).

The call saves the workspace first, as every gateway call does, so the file is published as a draft by the time the
answer says so; the answer is what became of it. The artifact is the same one a file written to ``out/`` makes.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.controller import OUTPUT_ACTIVE_CONTENT
from harness_core.engines.publish_tool import (
    ARTIFACT_PUBLISH_TOOL,
    INVALID_OUTPUT_PATH,
    OUTPUT_MISSING,
    output_path,
)
from harness_core.testing.contracts.tool_endpoint import BINDING, claims_for
from tests.unit.harness_core.engines.test_controller import World, task

if TYPE_CHECKING:
    from harness_core.engines.controller import EngineTurnOutcome
    from harness_core.gateway.types import McpResult


async def _publish_in_turn(world: World, before: str, path: str) -> tuple[McpResult, EngineTurnOutcome]:
    """``ph publish``'s call from inside a held turn, after the script's ``before`` lines ran."""
    runner = asyncio.ensure_future(world.controller.run_turn(world.run(), task(0, f"{before}\nhold\nsay ok".lstrip())))
    await asyncio.wait_for(world.gateway.holding.wait(), 5)
    answer = await world.host.call(ARTIFACT_PUBLISH_TOOL, {"path": path})
    world.gateway.release()
    return answer, await asyncio.wait_for(runner, 5)


@pytest.fixture
def world() -> World:
    return World()


async def test_pub1_the_call_publishes_the_file_as_a_draft_and_says_so(world: World) -> None:
    answer, outcome = await _publish_in_turn(world, "write out/report.md # Q3", "report.md")
    assert outcome.reason == "completed"
    assert [s.reason for s in world.sink.saves] == ["before_effect"]
    assert (answer.is_error, answer.disposition) == (False, "executed")
    told = json.loads(answer.text)
    [draft] = [p for p in outcome.published if p.path == "report.md"]
    assert told == {"path": "report.md", "artifactId": draft.artifact_id, "version": draft.version, "lifecycle": "draft"}
    # The same artifact a file written to out/ makes: a draft during the turn, final at its end.
    history = await world.publications.history(world.run().binding)
    assert [(c.name, c.lifecycle) for c in history.cards] == [("report.md", "draft"), ("report.md", "final")]
    # Answered by the controller, never run as one of the run's tools.
    assert world.endpoint_world.runs == []


async def test_pub2_a_refused_file_is_named_with_its_reason(world: World) -> None:
    answer, _ = await _publish_in_turn(world, "write out/page.html <p>x</p>", "page.html")
    assert (answer.is_error, answer.reason_code) == (True, OUTPUT_ACTIVE_CONTENT)
    assert answer.text == "page.html was not published (output active content)."


async def test_pub3_a_file_that_is_not_there_is_named(world: World) -> None:
    answer, _ = await _publish_in_turn(world, "", "nothing.md")
    assert (answer.is_error, answer.reason_code, answer.retryable) == (True, OUTPUT_MISSING, True)
    assert answer.text == "There is no file nothing.md in the outputs folder to publish."


async def test_pub4_a_file_an_earlier_turn_published_and_nobody_changed_is_unchanged(world: World) -> None:
    await world.seed({"out/kept.md": b"kept"})
    answer, _ = await _publish_in_turn(world, "", "kept.md")
    assert json.loads(answer.text) == {"path": "kept.md", "unchanged": True}


@pytest.mark.parametrize(
    "path", ["../escape.md", "/etc/passwd", "a/../../b", "a//b", ".", "", "a\\b", "nul\x00l", "x" * 513]
)
def test_pub5_sec_fs_02_a_path_that_leaves_the_outputs_folder_is_refused(path: str) -> None:
    assert output_path({"path": path}) is None


@pytest.mark.parametrize(("path", "normalized"), [("report.md", "report.md"), ("charts/q3.png", "charts/q3.png")])
def test_pub5_a_plain_relative_path_is_kept(path: str, normalized: str) -> None:
    assert output_path({"path": path}) == normalized


async def test_pub6_an_invalid_path_is_refused_before_the_turn_saves(world: World) -> None:
    answer, _ = await _publish_in_turn(world, "write out/a.md x", "../a.md")
    assert (answer.is_error, answer.reason_code) == (True, INVALID_OUTPUT_PATH)
    assert answer.text.startswith("Name a file under the outputs folder by its relative path")
    assert "before_effect" not in [s.reason for s in world.sink.saves]


async def test_pub8_between_turns_the_call_is_refused_plainly(world: World) -> None:
    _ = await world.controller.run_turn(world.run(), task(0, "say ok"))
    assert world.host.mounted == 0
    endpoint = world.run().tools
    answer = await endpoint.call_tool(claims_for(BINDING), ARTIFACT_PUBLISH_TOOL, {"path": "a.md"})
    # The run's own endpoint, outside a turn, has no such tool: nothing is published.
    assert answer.is_error


async def test_pub7_the_tool_is_never_listed_to_the_engine(world: World) -> None:
    _ = await world.controller.run_turn(world.run(), task(0, "say ok"))
    assert ARTIFACT_PUBLISH_TOOL not in set(world.gateway.model_calls[0].tools)
