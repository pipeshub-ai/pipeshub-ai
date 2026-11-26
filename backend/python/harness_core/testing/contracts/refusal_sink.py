"""Contract suite for ``RefusalSink`` adapters (SEC2 O-2, RS-1..RS-4; OB-03, RS-5..RS-6; C6.12 SEC-EG-14, RS-7..RS-8;
WP-103, RS-9).

Subclass ``RefusalSinkContract`` with a ``sink`` fixture whose adapter accepts ``TOKEN_A`` as run ``run-a``'s
gateway token and ``TOKEN_B`` as ``run-b``'s, and refuses any other token.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.gateway.refusal_reports import (
    MAX_ALLOWED_PER_RUN,
    MAX_HIDDEN_PER_RUN,
    MAX_REFUSALS_PER_RUN,
    MAX_VOLUME_ALERTS_PER_RUN,
    EgressAllowance,
    EgressVolumeAlert,
    RefusalNotAccepted,
    RefusalReport,
    SecretHidden,
)

if TYPE_CHECKING:
    from harness_core.gateway.refusal_reports import RefusalSink

TOKEN_A = "gateway-token-run-a"
TOKEN_B = "gateway-token-run-b"
TOKENS = {TOKEN_A: "run-a", TOKEN_B: "run-b"}
MODEL = RefusalReport("model", "unsupported_parameter", 400, param="tools.type")
EGRESS = RefusalReport("egress", "destination_not_allowed", 403, host="attacker.example")
VOLUME = EgressVolumeAlert("org_baseline", "up", 700 * 2**20, 100 * 2**20, "tunnel", baseline_bytes=10 * 2**20)


class RefusalSinkContract:
    async def test_rs1_a_report_is_recorded_once_per_run_gateway_and_reason(self, sink: RefusalSink) -> None:
        assert await sink.record("run-a", TOKEN_A, MODEL) == "recorded"
        assert await sink.record("run-a", TOKEN_A, MODEL) == "duplicate"
        assert await sink.record("run-a", TOKEN_A, EGRESS) == "recorded"
        assert await sink.record("run-b", TOKEN_B, MODEL) == "recorded"

    async def test_rs1_ob03_each_refused_destination_is_its_own_record(self, sink: RefusalSink) -> None:
        assert await sink.record("run-a", TOKEN_A, EGRESS) == "recorded"
        other = RefusalReport("egress", EGRESS.reason, EGRESS.status, host="other.example")
        assert await sink.record("run-a", TOKEN_A, other) == "recorded"
        assert await sink.record("run-a", TOKEN_A, other) == "duplicate"

    async def test_rs2_another_runs_token_is_not_accepted(self, sink: RefusalSink) -> None:
        with pytest.raises(RefusalNotAccepted):
            _ = await sink.record("run-a", TOKEN_B, MODEL)
        with pytest.raises(RefusalNotAccepted):
            _ = await sink.record("run-a", "not-a-token", MODEL)
        assert await sink.record("run-a", TOKEN_A, MODEL) == "recorded"

    async def test_rs3_past_the_cap_nothing_more_is_recorded(self, sink: RefusalSink) -> None:
        for i in range(MAX_REFUSALS_PER_RUN):
            assert await sink.record("run-a", TOKEN_A, RefusalReport("egress", f"reason_{i}", 403)) == "recorded"
        assert await sink.record("run-a", TOKEN_A, MODEL) == "capped"
        assert await sink.record("run-b", TOKEN_B, MODEL) == "recorded"

    async def test_rs4_a_malformed_report_is_not_accepted(self, sink: RefusalSink) -> None:
        for report in (
            RefusalReport("model", "Has Spaces", 400),
            RefusalReport("model", "unsupported_parameter", 200),
            RefusalReport("egress", "destination_not_allowed", 403, host="attacker.example/p?d=1"),
            RefusalReport("model", "unsupported_parameter", 400, param="tools.type=web_search"),
        ):
            with pytest.raises(RefusalNotAccepted):
                _ = await sink.record("run-a", TOKEN_A, report)

    async def test_rs5_ob03_an_allowed_destination_is_recorded_once_per_host_and_route(self, sink: RefusalSink) -> None:
        api = EgressAllowance("api.example", "intercepted")
        assert await sink.allowed("run-a", TOKEN_A, api) == "recorded"
        assert await sink.allowed("run-a", TOKEN_A, api) == "duplicate"
        assert await sink.allowed("run-a", TOKEN_A, EgressAllowance("api.example", "tunnel")) == "recorded"
        assert await sink.allowed("run-b", TOKEN_B, api) == "recorded"
        with pytest.raises(RefusalNotAccepted):
            _ = await sink.allowed("run-a", TOKEN_B, api)
        with pytest.raises(RefusalNotAccepted):
            _ = await sink.allowed("run-a", TOKEN_A, EgressAllowance("api.example/p?d=1", "http"))

    async def test_rs6_ob03_past_the_cap_no_more_allowed_destinations_are_recorded(self, sink: RefusalSink) -> None:
        for i in range(MAX_ALLOWED_PER_RUN):
            assert await sink.allowed("run-a", TOKEN_A, EgressAllowance(f"h{i}.example", "http")) == "recorded"
        assert await sink.allowed("run-a", TOKEN_A, EgressAllowance("late.example", "http")) == "capped"
        assert await sink.record("run-a", TOKEN_A, MODEL) == "recorded", "refusals have their own cap"

    async def test_rs7_sec_eg_14_a_volume_alert_is_recorded_once_per_kind_and_direction(self, sink: RefusalSink) -> None:
        assert await sink.volume("run-a", TOKEN_A, VOLUME) == "recorded"
        assert await sink.volume("run-a", TOKEN_A, VOLUME) == "duplicate"
        bigger = EgressVolumeAlert("org_baseline", "up", 900 * 2**20, 100 * 2**20, "tunnel", baseline_bytes=10 * 2**20)
        assert await sink.volume("run-a", TOKEN_A, bigger) == "duplicate", "the first crossing is the record"
        down = EgressVolumeAlert("run_threshold", "down", 3 * 2**30, 2 * 2**30, "model-gateway")
        assert await sink.volume("run-a", TOKEN_A, down) == "recorded"
        assert await sink.volume("run-b", TOKEN_B, VOLUME) == "recorded"
        with pytest.raises(RefusalNotAccepted):
            _ = await sink.volume("run-a", TOKEN_B, VOLUME)

    async def test_rs8_sec_eg_14_a_malformed_volume_alert_is_not_accepted(self, sink: RefusalSink) -> None:
        for alert in (
            EgressVolumeAlert("dns_entropy", "up", 1, 0, "tunnel"),  # pyright: ignore[reportArgumentType]
            EgressVolumeAlert("run_threshold", "up", 1, 0, "attacker.example"),
            EgressVolumeAlert("run_threshold", "up", -1, 0, "tunnel"),
        ):
            with pytest.raises(RefusalNotAccepted):
                _ = await sink.volume("run-a", TOKEN_A, alert)
        assert MAX_VOLUME_ALERTS_PER_RUN >= 2 * 3, "every kind and direction fits under the cap"

    async def test_rs9_wp103_a_hidden_secret_is_recorded_once_per_gateway_and_kind(self, sink: RefusalSink) -> None:
        pat = SecretHidden("egress", "pipeshub_pat")
        assert await sink.hidden("run-a", TOKEN_A, pat) == "recorded"
        assert await sink.hidden("run-a", TOKEN_A, pat) == "duplicate"
        assert await sink.hidden("run-a", TOKEN_A, SecretHidden("model", "pipeshub_pat")) == "recorded"
        assert await sink.hidden("run-b", TOKEN_B, pat) == "recorded"
        with pytest.raises(RefusalNotAccepted):
            _ = await sink.hidden("run-a", TOKEN_B, pat)
        with pytest.raises(RefusalNotAccepted):
            _ = await sink.hidden("run-a", TOKEN_A, SecretHidden("egress", "Not A Kind"))
        for i in range(MAX_HIDDEN_PER_RUN - 2):
            assert await sink.hidden("run-a", TOKEN_A, SecretHidden("egress", f"kind_{i}")) == "recorded"
        assert await sink.hidden("run-a", TOKEN_A, SecretHidden("egress", "late_kind")) == "capped"
        assert await sink.record("run-a", TOKEN_A, MODEL) == "recorded", "refusals have their own cap"


__all__ = ["EGRESS", "MODEL", "TOKENS", "TOKEN_A", "TOKEN_B", "VOLUME", "RefusalSinkContract"]
