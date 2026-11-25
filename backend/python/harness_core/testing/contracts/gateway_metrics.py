"""Contract suites for ``GatewayMetrics`` adapters (C20.48, OB-08 GM-1..GM-3) and ``ReadCacheMetrics`` adapters
(C7.14, WP91-FU-1, RCM-1..RCM-2).

Subclass ``GatewayMetricsContract`` with a ``metrics`` fixture (the adapter) and a ``labels`` fixture: a callable
returning every label value the adapter has exported so far (cumulative is fine: the suite only asserts what must
never appear).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, get_args

from harness_core.telemetry.metrics import (
    MODEL_ROUTES,
    TOOL_FAMILIES,
    TOOL_ROUTES,
    GatewayRequest,
    Outcome,
    ReadCacheExecutor,
    ReadCacheObserved,
    ReadCacheOutcome,
    ToolCallObserved,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.telemetry.metrics import GatewayMetrics, ReadCacheMetrics

HOSTILE = "org-hostile-1/run-hostile-2"
MAX_LABEL = 64


class GatewayMetricsContract:
    def test_gm1_every_observation_a_gateway_makes_is_accepted(self, metrics: GatewayMetrics) -> None:
        for route in (*MODEL_ROUTES, "unmatched"):
            for outcome in get_args(Outcome):
                metrics.request(GatewayRequest("model", route, outcome, 0.01, reason="token_missing"))
        for route in (*TOOL_ROUTES, "unmatched"):
            metrics.request(GatewayRequest("tool", route, "ok", 0.01))
        for family in TOOL_FAMILIES:
            metrics.tool_call(ToolCallObserved(family, "executed", 0.5))

    def test_gm2_what_a_caller_sent_never_becomes_a_label(
        self, metrics: GatewayMetrics, labels: Callable[[], set[str]]
    ) -> None:
        metrics.request(GatewayRequest("model", f"/v1/{HOSTILE}", "refused", 0.1, reason=HOSTILE))
        metrics.request(GatewayRequest("tool", HOSTILE, "error", 0.1, reason="x" * 500))
        metrics.tool_call(ToolCallObserved(HOSTILE, HOSTILE, 0.1))
        seen = labels()
        assert not any("hostile" in value for value in seen), seen
        assert all(len(value) <= MAX_LABEL for value in seen)

    def test_gm3_a_negative_or_huge_latency_does_not_raise(self, metrics: GatewayMetrics) -> None:
        metrics.request(GatewayRequest("model", "chat_completions", "ok", -5.0))
        metrics.request(GatewayRequest("model", "chat_completions", "ok", 1e9))
        metrics.tool_call(ToolCallObserved("read", "executed", -1.0))


class ReadCacheMetricsContract:
    """Subclass with a ``read_metrics`` fixture (the adapter) and a ``labels`` fixture, as ``GatewayMetricsContract``."""

    def test_rcm1_every_lookup_a_cache_makes_is_accepted(self, read_metrics: ReadCacheMetrics) -> None:
        for executor in get_args(ReadCacheExecutor):
            for outcome in get_args(ReadCacheOutcome):
                read_metrics.lookup(ReadCacheObserved(executor, outcome))

    def test_rcm2_an_unknown_value_never_becomes_a_label(
        self, read_metrics: ReadCacheMetrics, labels: Callable[[], set[str]]
    ) -> None:
        read_metrics.lookup(ReadCacheObserved(HOSTILE, HOSTILE))  # pyright: ignore[reportArgumentType]
        seen = labels()
        assert not any("hostile" in value for value in seen), seen


__all__ = ["HOSTILE", "GatewayMetricsContract", "ReadCacheMetricsContract"]
