"""SEC2 GR-1, GR-3, GR-4, GR-6 (model gateway side): a refusal of a call that carried a valid run token is
reported once, as ids and codes, without changing or delaying the answer to the engine."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from harness_core.gateway.chat_wire import CHAT_WIRE
from harness_core.gateway.refusal_reports import (
    MAX_REFUSALS_PER_RUN,
    InMemoryRefusalSink,
    RefusalReport,
    RefusalReporter,
)
from harness_core.refusals import HarnessRefusal
from harness_core.testing.clock import settle
from harness_core.testing.model_gateway import GatewayRig, body_of
from tests.unit.harness_core.gateway.test_responses_gateway import chat_body

SECRET = "exfil-7a1c"


@dataclass
class HangingSink:
    started: list[str] = field(default_factory=list[str])

    async def record(self, run_id: str, token: str, report: RefusalReport) -> str:
        self.started.append(report.reason)
        await asyncio.Event().wait()
        raise AssertionError("never answers")


@dataclass
class FailingSink:
    calls: int = 0

    async def record(self, run_id: str, token: str, report: RefusalReport) -> str:
        self.calls += 1
        raise ConnectionError("node down")


async def _refused(rig: GatewayRig, token: str | None, body: object) -> HarnessRefusal:
    with pytest.raises(HarnessRefusal) as caught:
        await rig.gateway.serve(CHAT_WIRE, token, {}, body_of(body))
    return caught.value


async def test_gr1_a_refusal_with_the_run_token_is_reported_once_with_ids_and_codes() -> None:
    rig = GatewayRig()
    binding = rig.pin()
    token = rig.token(binding)
    refused = await _refused(rig, token, chat_body(model="gpt-4o"))
    assert refused.reason_code == "model_not_granted"
    await rig.refusals.drain()
    [record] = rig.refusal_sink.records
    assert record.run_id == "run-1" and record.token == token
    assert record.report == RefusalReport("model", "model_not_granted", 404)


async def test_gr1_a_parameter_is_reported_by_its_path_never_its_value() -> None:
    rig = GatewayRig()
    token = rig.token(rig.pin())
    tools = [{"type": f"web_search?d={SECRET}"}]
    refused = await _refused(rig, token, chat_body(tools=tools))
    assert refused.reason_code == "unsupported_parameter"
    await rig.refusals.drain()
    [record] = rig.refusal_sink.records
    assert record.report.param == "tools.type"
    assert SECRET not in repr(record.report)


async def test_gr3_a_flood_of_one_reason_is_one_report() -> None:
    rig = GatewayRig()
    token = rig.token(rig.pin())
    for _ in range(1000):
        await _refused(rig, token, chat_body(model="gpt-4o"))
    await rig.refusals.drain()
    assert len(rig.refusal_sink.calls) == 1


async def test_gr3_the_sink_caps_a_run_and_dedupes_like_node() -> None:
    sink = InMemoryRefusalSink()
    outcomes = [
        await sink.record("run-1", "t", RefusalReport("egress", f"reason_{i}", 403)) for i in range(MAX_REFUSALS_PER_RUN + 3)
    ]
    assert outcomes.count("recorded") == MAX_REFUSALS_PER_RUN and outcomes[-1] == "capped"
    assert await sink.record("run-1", "t", RefusalReport("egress", "reason_0", 403)) == "duplicate"
    assert len(sink.records) == MAX_REFUSALS_PER_RUN


@pytest.mark.parametrize("token", [None, "sk-ant-api03-" + "A" * 40, "not-a-token"])
async def test_gr4_a_refusal_without_a_verifiable_run_token_is_never_reported(
    token: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    rig = GatewayRig()
    rig.pin()
    refused = await _refused(rig, token, chat_body())
    assert refused.reason_code.startswith("token_")
    await rig.refusals.drain()
    assert rig.refusal_sink.calls == []
    assert caplog.text.count("model_gateway.refused") == 1


async def test_gr6_a_sink_that_never_answers_does_not_delay_the_refusal() -> None:
    sink = HangingSink()
    rig = GatewayRig(refusal_sink=sink)
    token = rig.token(rig.pin())
    refusal = asyncio.ensure_future(_refused(rig, token, chat_body(model="gpt-4o")))
    await settle(refusal.done)
    assert refusal.result().reason_code == "model_not_granted"
    await settle(lambda: sink.started == ["model_not_granted"])
    assert rig.refusals.in_flight == 1, "the report is still waiting on the sink after the engine had its answer"
    await rig.refusals.close()


async def test_gr6_reports_in_flight_are_bounded_and_the_excess_is_dropped_not_queued() -> None:
    sink = HangingSink()
    reporter = RefusalReporter(sink)
    for i in range(100):
        reporter.report("run-1", "token", RefusalReport("model", f"reason_{i}", 400))
    assert reporter.in_flight == RefusalReporter.MAX_IN_FLIGHT
    await settle(lambda: len(sink.started) == RefusalReporter.MAX_IN_FLIGHT)
    await reporter.close()
    assert reporter.in_flight == 0
    assert len(sink.started) == RefusalReporter.MAX_IN_FLIGHT, "the dropped reports were never sent later"


async def test_gr6_a_failing_sink_leaves_the_answer_unchanged_and_is_logged_once_per_outage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sink = FailingSink()
    rig = GatewayRig(refusal_sink=sink)
    token = rig.token(rig.pin())
    reasons = []
    for _ in range(3):
        refused = await _refused(rig, token, chat_body(model="gpt-4o"))
        reasons.append(refused.reason_code)
        await rig.refusals.drain()
    assert reasons == ["model_not_granted"] * 3
    # Forgotten after a failure, so a later refusal retries; logged once until a report succeeds again.
    assert sink.calls == 3
    assert caplog.text.count("refusal_report.failed") == 1


def test_a_site_path_is_printable_ascii_without_a_query_or_fragment() -> None:
    from harness_core.gateway.refusal_reports import SiteReport

    allowed = {chr(c) for c in range(0x21, 0x7F)} - {"#", "?"}
    for code in range(0x300):
        char = chr(code)
        shown = SiteReport.of(f"/a{char}b", "read", "read")
        assert (shown is not None) is (char in allowed), repr(char)
    assert SiteReport.of("/" + "a" * 120, "read", "read") is not None
    assert SiteReport.of("/" + "a" * 121, "read", "read") is None
    # A trailing line break would end the activity line early.
    assert SiteReport.of("/docs\n", "read", "read") is None
    assert SiteReport.of("docs", "read", "read") is None
