"""UX5: the command-output port over the Redis activity bus (CO, BP-4) and its wire vector (EV-2)."""

from __future__ import annotations

import json
from pathlib import Path

import fakeredis
import pytest

from app.harness.livebus import (
    ACTIVITY_KEY_PREFIX,
    ACTIVITY_NOTIFY_PREFIX,
    RedisLiveBus,
    decode_frame,
    encode_frame,
    notify_channel,
    stream_key,
)
from harness_core.activity import BoundedCommandActivity
from harness_core.journal.types import LiveFrame
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.command_activity import (
    RUN,
    CommandActivityContract,
    spec,
)

VECTOR = Path(__file__).resolve().parents[5] / "contracts" / "harness" / "vectors" / "activity-frames.json"


class TestRedisActivityBusCommandActivity(CommandActivityContract):
    """``BoundedCommandActivity`` publishing to the activity bus's own Redis stream."""

    @pytest.fixture
    def bus(self) -> RedisLiveBus:
        return RedisLiveBus(fakeredis.FakeAsyncRedis(decode_responses=True)).activity_bus()


async def test_bp4_activity_frames_never_reach_the_chat_stream() -> None:
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    chat = RedisLiveBus(client)
    activity = BoundedCommandActivity(chat.activity_bus(), run_id=RUN, attempt_id="a", fence=1, clock=ManualClock())
    recorder = activity.begin(spec())
    recorder.write("stdout", "hello\n")
    _ = await recorder.finish(exit_code=0, status="completed")
    await activity.drained()
    assert await chat.read(RUN) == []
    assert await client.exists(f"{ACTIVITY_KEY_PREFIX}{RUN}") == 1
    assert await client.exists(f"harness:live:{RUN}") == 0
    await activity.aclose()


def _vector() -> dict[str, object]:
    return json.loads(VECTOR.read_text(encoding="utf-8"))


def test_ev2_keys_match_the_vector() -> None:
    for case in _vector()["keys"]:  # pyright: ignore[reportGeneralTypeIssues]
        assert stream_key(case["runId"], case["namespace"], ACTIVITY_KEY_PREFIX) == case["key"]
        assert notify_channel(case["runId"], case["namespace"], ACTIVITY_NOTIFY_PREFIX) == case["notifyChannel"]


def test_ev2_frames_encode_and_decode_as_the_vector_says() -> None:
    for case in _vector()["frames"]:  # pyright: ignore[reportGeneralTypeIssues]
        f = case["frame"]
        frame = LiveFrame(f["runId"], f["fence"], f["modelAttempt"], f["n"], f["kind"], f["data"])
        assert encode_frame(frame) == case["fields"]
        assert decode_frame(f["runId"], case["fields"]) == frame


async def test_ev2_the_publisher_writes_the_vector_key_and_notifies() -> None:
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    bus = RedisLiveBus(client, key_namespace="tenant-a").activity_bus()
    pubsub = client.pubsub()
    await pubsub.subscribe("tenant-a:harness:activity-notify:run_1")
    await bus.publish(LiveFrame("run_1", 3, 0, 1, "command_output", {"commandId": "c", "stream": "stdout", "text": "x"}))
    assert [f.data for f in await bus.read("run_1")] == [{"commandId": "c", "stream": "stdout", "text": "x"}]
    assert await client.exists("tenant-a:harness:activity:run_1") == 1
    await pubsub.aclose()
