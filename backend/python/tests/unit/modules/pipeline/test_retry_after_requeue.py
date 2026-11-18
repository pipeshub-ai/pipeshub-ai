# ruff: noqa: F401, F811 (the spend-world fixtures are imported for pytest, then named as arguments)
"""A provider's Retry-After is untrusted input: it never parks a stage job for more than an hour.

The header is read off the wire and carried by the gateway's errors into the stage worker's requeue delay. This drives the
real stage worker, pause judge, gateway and fleet breaker (fakeredis) with a model that answers 429 and a Retry-After.
"""


from __future__ import annotations

import json
import math
from typing import ClassVar

import pytest

from app.modules.pipeline.models import StageOutcome, StageResult
from app.services.llm_gateway import gateway as gateway_module
from app.services.llm_gateway import wire
from app.utils.llm import LLMUnavailableError
from tests.support.gateway_world import GatewayWorld, ModelStage
from tests.support.spend_world import fleet, sim_clock

ONE_HOUR = 3600.0  # TRIAL_MAX: the longest the breaker itself ever waits before a trial


class _RAModel:
    def __init__(self, header: str) -> None:
        self.header = header
        self.requests = 0

    async def ainvoke(self, value: object, /, **kwargs: object) -> None:
        wire.admit_request()
        wire.note_request_sent()
        self.requests += 1
        wire.note_response(429, wire.parse_retry_after({"retry-after": self.header}))
        raise RuntimeError("HTTP 429")


class _Stage(ModelStage):
    name: ClassVar[str] = "classify"

    def __init__(self, gateway, model) -> None:
        self.gateway, self.model = gateway, model

    async def run(self, job, io) -> StageResult:
        try:
            await self.gateway.invoke(self.model, f"DOC:{job.virtual_record_id}", provider="FakeChat:fake-model@host")
        except LLMUnavailableError as exc:
            return StageResult.paused_for(exc, f"Model provider unavailable: {exc}")
        return StageResult(outcome=StageOutcome.COMPLETED)


@pytest.mark.parametrize("header", ["61", "3600", "86400", "1e9", "inf", "nan", "-5"])
async def test_the_requeue_delay_of_a_rate_limited_stage_job(fleet, sim_clock, monkeypatch, header) -> None:
    async def no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(gateway_module.asyncio, "sleep", no_sleep)
    model = _RAModel(header)
    world = GatewayWorld(fleet, sim_clock, stage_factory=lambda gateway: _Stage(gateway, model))
    world.add_job("doc", arrive_s=0.0)
    _at, _seq, _doc = world.queue.pop()
    await world.deliver("doc", 0)
    assert world.queue, "the job was not requeued"
    wake_s = world.queue[0][0] - world.now_s
    try:
        json.dumps({"_retry_not_before": 1.7e9 + wake_s}, allow_nan=False)
        serialisable = True
    except ValueError:
        serialisable = False
    # The Retry-After is clamped to an hour; the stage's own backoff adds at most its jitter.
    assert serialisable, "the requeue time must be strict JSON"
    assert math.isfinite(wake_s) and wake_s <= ONE_HOUR + 600.0, f"Retry-After {header}: parked {wake_s:.6g}s"
