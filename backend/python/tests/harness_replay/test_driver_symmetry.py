"""DUR1-R1..R4: the legacy and durable drivers show the model one task for one spec.

EV4 compared the two loops through these drivers while the legacy driver gave the model no context length (the
SMALL prompt tier) and no time, and the durable one a 128k non-reasoning model (MID, worked examples) and the wall
clock; the durable driver's earlier turns never reached the model, and any record id the fixture lacked came back
"still being indexed". Each case drives both drivers on one spec and compares what the model was sent.
"""

from __future__ import annotations

import json

import pytest

from harness_core.testing import ModelScript
from harness_core.testing.model_script import Reply, ToolCallSpec, Turn
from tests.harness_replay import durable_driver, legacy_driver
from tests.harness_replay.knowledge_base import CLIENT_TIME, CLIENT_TIMEZONE
from tests.harness_replay.legacy_driver import DriveSpec
from tests.harness_replay.wire import drive_captured, first_difference
from tests.harness_replay.world import Doc, WorldFixtures

DOCS = [
    Doc(id="pol-travel", title="Travel policy", text="Book economy for flights under 6 hours."),
    Doc(id="pol-leave", title="Leave policy", text="Full-time employees get 25 days of paid leave."),
]
WORKED_EXAMPLES = "## Worked Examples"
TIME_CONTEXT = "## Time context"


def _spec(**overrides: object) -> DriveSpec:
    spec = DriveSpec(
        scenario="dur1-symmetry",
        query="What is the travel policy?",
        fixtures=WorldFixtures(knowledge=list(DOCS)),
        knowledge_search=True,
        fake_toolsets=(),
    )
    for key, value in overrides.items():
        setattr(spec, key, value)
    return spec


def _answer() -> ModelScript:
    return ModelScript(id="answer", turns=[Turn(reply=Reply(text="Economy under 6 hours."))])


async def _both(script: ModelScript, **overrides: object):  # noqa: ANN202
    legacy = await drive_captured(legacy_driver.drive, _spec(**overrides), script)
    durable = await drive_captured(durable_driver.drive, _spec(**overrides), script)
    return legacy, durable


@pytest.mark.parametrize(("reasoning", "examples"), [(False, True), (True, False)])
async def test_dur1_r1_one_model_entry_one_prompt_tier(reasoning: bool, examples: bool) -> None:
    legacy, durable = await _both(_answer(), is_reasoning=reasoning)
    assert first_difference(legacy.requests, durable.requests) is None
    # 128k non-reasoning is MID (worked examples); a reasoning model is FRONTIER (none), on both routes.
    assert (WORKED_EXAMPLES in legacy.requests[0].system) is examples


async def test_dur1_f5_the_system_cache_blocks_reach_the_transport_as_on_legacy() -> None:
    legacy, durable = await _both(_answer())
    assert first_difference(legacy.requests, durable.requests) is None


async def test_dur1_r2_the_client_time_reaches_both_routes_the_same_way() -> None:
    legacy, durable = await _both(_answer(), timezone=CLIENT_TIMEZONE, current_time=CLIENT_TIME)
    assert first_difference(legacy.requests, durable.requests) is None
    assert TIME_CONTEXT in legacy.requests[0].system
    assert CLIENT_TIME in legacy.requests[0].system
    legacy, durable = await _both(_answer())
    assert first_difference(legacy.requests, durable.requests) is None
    assert TIME_CONTEXT not in legacy.requests[0].system


async def test_dur1_r3_earlier_turns_reach_the_durable_model_as_on_legacy() -> None:
    turns = [
        {"role": "user_query", "content": "I'm attaching notes.csv for you to work with."},
        {"role": "bot_response", "content": "Thanks - notes.csv is saved in this conversation."},
    ]
    legacy, durable = await _both(_answer(), previous_turns=turns)
    assert first_difference(legacy.requests, durable.requests) is None
    assert any("notes.csv for you to work with" in m for m in durable.requests[0].messages)


async def test_dur1_r4_an_id_the_fixture_lacks_is_not_available_never_still_indexing() -> None:
    script = ModelScript(
        id="fetch-missing",
        turns=[
            Turn(reply=Reply(tool_calls=[ToolCallSpec(id="s1", name="knowledgegraph__search", args={"query": "travel"})])),
            Turn(reply=Reply(tool_calls=[ToolCallSpec(
                id="f1", name="knowledgegraph__fetch_record", args={"record_ids": ["Travel policy"]}
            )])),
            Turn(reply=Reply(text="Economy under 6 hours.")),
        ],
    )
    legacy, durable = await _both(script)
    assert first_difference(legacy.requests, durable.requests) is None
    fetched = json.loads(legacy.requests[-1].messages[-1])
    text = json.dumps(fetched)
    assert "not available" in text
    assert "still being indexed" not in text
