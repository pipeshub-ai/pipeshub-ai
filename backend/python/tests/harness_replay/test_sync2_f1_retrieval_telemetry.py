"""SYNC2-F1: a turn with ``includeRetrievalContext`` streams the same measurement frames on both loops.

Today's loop writes ``retrieval_context`` (per prefetch and retrieval call) and ``run_usage`` CUSTOM frames; a
durable run journals them as ``x.pipeshub.telemetry.recorded``, which Node's projector shows as the same frames. Each
case drives both loops on one script and compares the frames' values; a resumed attempt reports only what is new.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from harness_core.testing import ModelScript
from harness_core.testing.model_script import Reply, ToolCallSpec, Turn
from tests.harness_replay import durable_driver, legacy_driver
from tests.harness_replay.legacy_driver import CHAT_ROUTE, DriveSpec
from tests.harness_replay.wire import drive_captured, first_difference
from tests.harness_replay.world import Doc, WorldFixtures

TELEMETRY = "x.pipeshub.telemetry.recorded"
NAMES = ("retrieval_context", "run_usage")
DOCS = [
    Doc(id="pol-travel", title="Travel policy", text="Book economy for flights under 6 hours."),
    Doc(id="pol-leave", title="Leave policy", text="Full-time employees get 25 days of paid leave."),
]


def _spec(*, opted_in: bool, chat: bool = False) -> DriveSpec:
    route = {"route": CHAT_ROUTE, "chat_mode": "internal_search"} if chat else {}
    return DriveSpec(
        **route,  # pyright: ignore[reportArgumentType]
        scenario="sync2-f1-telemetry",
        query="What is the travel policy?",
        fixtures=WorldFixtures(knowledge=list(DOCS)),
        knowledge_search=True,
        fake_toolsets=(),
        include_retrieval_context=opted_in,
    )


def _script() -> ModelScript:
    return ModelScript(
        id="search-then-answer",
        turns=[
            Turn(reply=Reply(tool_calls=[ToolCallSpec(id="s1", name="knowledgegraph__search", args={"query": "travel"})])),
            Turn(reply=Reply(tool_calls=[ToolCallSpec(id="s2", name="knowledgegraph__search", args={"query": "leave"})])),
            Turn(reply=Reply(text="Economy under 6 hours.")),
        ],
    )


def _legacy_frames(sse: str) -> list[tuple[str, Any]]:
    frames: list[tuple[str, Any]] = []
    for line in sse.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            data = json.loads(line[len("data:") :])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("type") == "CUSTOM" and data.get("name") in NAMES:
            frames.append((data["name"], data["value"]))
    return frames


def _ids_masked(frames: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    """The executor's call ids are fresh per run on both loops; every other field must match."""
    return [(name, {**value, "toolCallId": "id"} if "toolCallId" in value else value) for name, value in frames]


def _durable_frames(events: list[Any]) -> list[tuple[str, Any]]:
    return [(e.data["name"], e.data["value"]) for e in events if e.type == TELEMETRY]


@pytest.mark.parametrize(
    ("chat", "names"),
    [
        (False, ["retrieval_context", "retrieval_context", "run_usage"]),
        # `/chat/stream` reports its prefetch first; each search still reports the record it surfaced (main #4124).
        (True, ["retrieval_context", "retrieval_context", "retrieval_context", "run_usage"]),
    ],
)
async def test_sync2_f1_an_opted_in_turn_streams_the_same_frames_on_both_loops(chat: bool, names: list[str]) -> None:
    legacy = await drive_captured(legacy_driver.drive, _spec(opted_in=True, chat=chat), _script())
    durable = await drive_captured(durable_driver.drive, _spec(opted_in=True, chat=chat), _script())
    assert first_difference(legacy.requests, durable.requests) is None
    expected = _legacy_frames(legacy.result.sse)
    assert [name for name, _ in expected] == names
    assert any(value.get("records") for _, value in expected), "a search reached the model"
    frames = _durable_frames(list(durable.result.events))
    if chat:
        # `model` is the request's modelName: the durable rig pins one, the legacy chat rig's body sends none.
        assert frames[-1][1].pop("model") == "scripted-model"
    assert _ids_masked(frames) == _ids_masked(expected)


@pytest.mark.parametrize("opted_in", [False])
async def test_sync2_f1_off_by_default_journals_nothing(opted_in: bool) -> None:
    durable = await drive_captured(durable_driver.drive, _spec(opted_in=opted_in), _script())
    assert _durable_frames(list(durable.result.events)) == []
